# Zenith — Linux-first Sunshine fork

Fork of [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine) focused on making the
Linux host experience match (and pass) what the Windows-only forks ship — without their
Windows-driver architecture.

## Why this exists

The Windows forks (Apollo, Sunshine-Foundation) get their headline latency from a custom
virtual display driver (SudoVDA / ZakoVDD) that hands frames to the encoder at present time,
bypassing DXGI Desktop Duplication. **Linux doesn't have that problem** — KMS capture already
reads the scanout framebuffer zero-copy — but Linux Sunshine is missing the *features* those
forks pair with it: per-client virtual display management, remote microphone, per-frame HDR
metadata, event-driven capture pacing. Zenith ports the ideas, not the Windows code.

## Principles

1. **Linux is the first-class target.** Windows/macOS code stays compiling (cheap, eases
   upstream syncs) but gets no feature work.
2. **Track upstream.** Changes stay additive and modular so rebasing on LizardByte master
   stays viable. Steal ideas from forks, never their diff-debt.
3. **Both GPU vendors are first-class.** NVIDIA (NVENC/CUDA, virtual connector EDID) and
   AMD (VAAPI, later Vulkan encode) get feature parity wherever the hardware allows.
4. **GNOME/Wayland is the reference desktop.** X11 and wlroots stay supported, but the
   PipeWire/portal path is where pacing and HDR work lands first.
5. **Packagable everywhere.** deb (Ubuntu/Debian/Mint), rpm (Fedora x86_64 **and aarch64 —
   Asahi Linux on Apple Silicon**), AppImage as fallback. Clean systemd user service, no
   assumptions about distro internals. Asahi runs software encode until the Apple Silicon
   GPU's video encoder is exposed by the Asahi drivers; capture goes through PipeWire/portal
   like any other Wayland desktop.

## Roadmap

### M0 — Foundation (now)
- [x] Fork upstream, build on Linux Mint 22.3 (Ubuntu 24.04 base), GTX 1660 Ti
- [x] CI: build deb (Ubuntu 24.04, Debian 13), rpm (Fedora 42, x86_64 + aarch64/Asahi) on every push
- [ ] Side-by-side install story (doesn't fight a packaged Sunshine on the same box)

### M1 — Remote microphone (client mic → host)
Foundation's most portable win. Protocol side (extra audio stream from Moonlight client)
reimplemented against upstream; host side is a **PipeWire virtual source** ("Zenith Mic")
that any app (Discord, games) sees as a real microphone. Works with the enhanced Moonlight
clients (qiin2333 moonlight-qt / moonlight-android) that already send mic audio.
- PipeWire native; PulseAudio compat via pipewire-pulse (covers Ubuntu/Debian/Fedora)

### M2 — Present-paced capture ("encode-on-present")
Kill the last timer-driven sampling in the Linux capture path:
- KMS path: pace off DRM CRTC sequence events (`drmCrtcQueueSequence`) instead of
  steady-clock sleeps — wake exactly at scanout, encode immediately
- PipeWire/portal path (GNOME/Wayland): frames are already compositor-pushed; audit the
  buffer chain for hidden copies/waits and surface per-stage latency in the web UI
- Instrument: capture→encode→send timestamps per frame, exposed as stats (Foundation-style)

### M3 — Per-client display profiles + virtual display lifecycle
The Apollo/Foundation "VDD manager" concept, Linux-native:
- Per-paired-client profiles (resolution / refresh / HDR / target display), keyed by client cert
- On session start, apply the client's mode to the target output:
  - NVIDIA: virtual connector (EDID override on a headless DP) modeset
  - AMD: DRM `force` connector + EDID firmware, or a headless GNOME session
  - Mutter D-Bus display-config API on GNOME/Wayland for dynamic mode changes
- Longer term: dynamic virtual outputs via EVDI or a small DRM helper, so displays are
  created/destroyed per client like SudoVDA does on Windows

### M4 — HDR pipeline
Port Foundation's per-frame luminance analysis (MaxCLL/MaxFALL with percentile clipping +
EMA smoothing, injected as HEVC/AV1 metadata) off HLSL:
- NVIDIA: CUDA kernel in the NVENC path
- AMD: Vulkan compute in the VAAPI/Vulkan-encode path
- HLG (BT.2100) transfer support alongside HDR10 PQ
- Gated on GNOME HDR maturity; wired to `mutter` HDR session state

### M5 — Quality-of-life from the fork ecosystem
- Web UI: live session dashboard (per-stage latency, encoder stats, client info)
- Smarter pairing UX (named clients, per-client permissions)
- Optional clipboard sync (PipeWire/portal-friendly, opt-in)

## Hardware reference targets
| Box | GPU | Encode path | Display path |
|-----|-----|-------------|--------------|
| pate-pc-lm | GTX 1660 Ti | NVENC (CUDA) | NVIDIA virtual connector (DP-1 EDID) |
| aupate-pve-lm | RX 6800 | VAAPI / Vulkan | DRM force-connector + EDID |
| Asahi (Apple Silicon) | AGX | software (x264/x265) | PipeWire/portal |

GNOME/Wayland everywhere.
