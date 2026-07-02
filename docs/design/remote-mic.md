# M1 — Remote Microphone (client mic → host virtual source)

Zenith reimplements the remote-microphone feature pioneered by Sunshine-Foundation, keeping
**wire compatibility with their enhanced Moonlight clients** (qiin2333/moonlight-qt,
qiin2333/moonlight-android, VoidLink iOS) so those clients work against Zenith unchanged.
Host side is Linux-native: a PipeWire virtual source ("Zenith Mic") instead of Foundation's
WASAPI + virtual audio driver.

Protocol facts below were extracted from foundation-sunshine (GPL-3.0, same license) and
qiin2333/moonlight-common-c branch `mic`. We reimplement; we do not copy their diffs.

## Wire protocol (must match exactly)

**Negotiation**
- Feature gate: config `stream_mic` (Zenith: `mic_enabled`). When on, the RTSP DESCRIBE SDP
  advertises: `m=audio <mic_port> RTP/AVP 96`, `a=rtpmap:96 opus/48000/2`,
  `a=fmtp:96 minptime=10;useinbandfec=1`.
- Client issues RTSP SETUP for stream type `"mic"` (alongside video/audio/control) →
  session flags `enable_mic` / `setup_mic`.
- Mic UDP port = base port **+12** (video +9, control +10, audio +11).

**Packets** (client → host UDP, max 1400 B payload)
- Legacy 8-bit header (Android client, `MICROPHONE_PACKET_HEADER`, packed 12 B):
  `flags u8 | packetType u8 | sequenceNumber u16 LE | timestamp u32 LE | ssrc u32 LE`
  with `packetType == 0x61` (MIC_PACKET_TYPE_OPUS), `ssrc == 0x12345678` (MIC_PACKET_MAGIC).
- Extended 16-bit type header (`rtp_packet_ext_t`, packed 13 B):
  `header u8 | packetType u16 | sequenceNumber u16 | timestamp u32 | ssrc u32`
  with `packetType == 0x5504` (Sunshine protocol extension "Microphone data"; 0x5505 = mic
  config, reserved).
- **Sequence numbers are little-endian** (client uses LE explicitly; do not ntohs).
- Payload after header: one Opus frame (48 kHz, mono, in-band FEC enabled).

**Encryption** (per client, optional)
- Enabled when client's launch `encryptionFlagsEnabled & SS_ENC_MIC (0x08)`.
- AES-128-CBC with PKCS7, key = session's audio/remote-input AES key (`session.audio.cipher.key`).
- **IV differs from the audio stream**: `IV[0:4] = BE32(BE32(remoteInputAesIv[0:4]) + (seq & 0xFFFF))`,
  `IV[4:16] = 0`. (Audio uses avRiKeyId + seq; mic uses the raw base IV — easy to get wrong.)
- No cipher registered for that client IP → treat payload as plaintext Opus unless the
  reject-plaintext hardening flag is set.

**Decode & sanity**
- `opus_decoder_create(48000, 1)`; drop obviously invalid payloads (first 4 bytes all 0x00 or
  all 0xFF). Foundation uses `opus_decoder_get_nb_samples` + FEC decode on gaps.

## Host architecture (Zenith / Linux)

```
UDP :base+12 ──► mic recv thread ──► per-client AES-CBC decrypt ──► opus decode (48k mono)
                                                                        │ S16 PCM
                                                    PipeWire pw_stream ◄┘
                                              media.class = Audio/Source
                                              node "Zenith Mic" — apps record from it
```

- `src/platform/common.h`: extend `audio_control_t` with `write_mic_data(const char*, size, seq)`
  (mirrors Foundation's contract so core stays platform-agnostic).
- `src/platform/linux/mic_write.cpp`: PipeWire `pw_stream`, `PW_KEY_MEDIA_CLASS "Audio/Source"`,
  `node.name = zenith-mic`, `node.description = "Zenith Mic"`, F32/S16 48 kHz mono; opus decode
  lives here (as in Foundation's Windows impl); ring buffer between UDP thread and pw thread;
  silence fill on underrun so the node keeps a live clock.
- Requires a user-session PipeWire (true for the default systemd user service). Flatpak/system
  service caveats documented later.
- Windows/macOS: `write_mic_data` returns -1 (feature Linux-only in Zenith).

## Scope cuts for v1
- No mic-config packet handling (0x5505) — fixed 48k mono.
- No multi-client mixing — last SETUP wins the mic (Foundation tracks per-client stats only).
- Plaintext fallback allowed, hardening flag later.
