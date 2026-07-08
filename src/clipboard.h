/**
 * @file src/clipboard.h
 * @brief Clipboard sync between host and Moonlight clients (Zenith).
 *
 * Wire compatibility with the Sunshine-Foundation ecosystem (VoidLink,
 * qiin2333 Moonlight forks). Protocol facts reimplemented from their
 * published agent; we do not copy their code.
 *
 * Transport: control-stream packet 0x5508 carrying a v1 frame
 * (little-endian):
 *
 *   u8  version = 1
 *   u8  kind      (1 = utf8 text, 2 = png image, 3 = blob-ref JSON,
 *                  4 = file-transfer offer JSON)
 *   u32 token     (echo-suppression nonce)
 *   u32 length
 *   bytes payload
 *
 * Payloads above kInlineThreshold move out-of-band: the frame becomes a
 * kind=3 JSON ref {"id","mime","size"} and the bytes are served/accepted on
 * the paired HTTPS server (/api/v1/clipboard/blob[/<id>]).
 *
 * Unlike Foundation (Windows service in session 0 + user-session GUI agent),
 * Zenith on Linux runs inside the user session and touches the clipboard
 * directly through platf::clipboard.
 */
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace clipboard {

  constexpr std::uint8_t kWireVersion = 1;

  enum class kind_e : std::uint8_t {
    text = 1,  ///< UTF-8 text, inline
    png = 2,  ///< PNG image, inline
    ref = 3,  ///< JSON blob reference, bytes over HTTPS
    file_offer = 4,  ///< JSON file-transfer offer
  };

  /// Payload size at/above which frames switch to out-of-band blob refs.
  /// The encrypted control frame ceiling is ~65525 payload bytes.
  constexpr std::size_t kInlineThreshold = 60'000;
  constexpr std::size_t kMaxTextBytes = 1 * 1024 * 1024;
  constexpr std::size_t kMaxBlobBytes = 50 * 1024 * 1024;

  struct frame_t {
    kind_e kind;
    std::uint32_t token;
    std::vector<std::uint8_t> payload;
  };

  /// Serialize a frame into wire bytes. Empty result when payload violates
  /// the caller-passed limit.
  std::vector<std::uint8_t> encode(const frame_t &frame);

  /// Parse wire bytes; std::nullopt on malformed/oversized/unknown input.
  std::optional<frame_t> decode(const std::uint8_t *data, std::size_t size);

  // ---- Host-side sync engine -------------------------------------------

  /// True when clipboard sync is configured on and the platform backend
  /// works on this machine; drives the RTSP capability bits.
  bool available();

  /// Start watching the local clipboard (idempotent). Called when the first
  /// session starts.
  void start();

  /// Stop watching (last session gone).
  void stop();

  /// Handle a decrypted inbound 0x5508 payload from a client: decode, fetch
  /// blob refs, echo-suppress, and apply to the local clipboard.
  void on_inbound(const std::uint8_t *data, std::size_t size);

  /// Drain pending outbound frames (local clipboard changes and file
  /// offers), each already wire-encoded. Called from the control broadcast
  /// thread tick.
  std::vector<std::vector<std::uint8_t>> drain_outbound();

  /// Queue a file-transfer offer (kind=4) built from a host file path.
  /// Returns the offer JSON on success, an empty string when the path is
  /// not a readable regular file.
  std::string offer_file(const std::string &path);

  // ---- Blob store (out-of-band payloads on the paired HTTPS server) -----

  namespace blob {
    /// Store bytes; returns the blob id used in kind=3 refs.
    std::string put(std::string mime, std::vector<std::uint8_t> bytes);

    /// Fetch and consume a blob by id.
    std::optional<std::pair<std::string, std::vector<std::uint8_t>>> take(const std::string &id);

    /// Resolve a registered file-offer id to its on-disk path (download
    /// endpoint; does not consume — offers expire by TTL).
    std::optional<std::string> file_path(const std::string &id);
  }  // namespace blob

}  // namespace clipboard
