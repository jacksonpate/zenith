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

  /**
   * @brief One decoded clipboard control frame.
   */
  struct frame_t {
    kind_e kind;  ///< Payload interpretation.
    std::uint32_t token;  ///< Echo-suppression nonce, echoed back by peers.
    std::vector<std::uint8_t> payload;  ///< Frame body (kind-dependent).
  };

  /**
   * @brief Serialize a frame into wire bytes.
   * @param frame The frame to encode.
   * @return Wire bytes, or an empty vector when the payload exceeds the limit.
   */
  std::vector<std::uint8_t> encode(const frame_t &frame);

  /**
   * @brief Parse wire bytes into a frame.
   * @param data Pointer to the frame bytes.
   * @param size Number of bytes available at @p data.
   * @return The parsed frame, or std::nullopt on malformed/oversized/unknown input.
   */
  std::optional<frame_t> decode(const std::uint8_t *data, std::size_t size);

  // ---- Host-side sync engine -------------------------------------------

  /**
   * @brief Whether clipboard sync should be advertised and driven.
   * @return True when configured on and the platform backend works here.
   */
  bool available();

  /**
   * @brief Start watching the local clipboard (idempotent).
   */
  void start();

  /**
   * @brief Stop watching the local clipboard (last session gone).
   */
  void stop();

  /**
   * @brief Handle a decrypted inbound clipboard payload from a client.
   * @param data Pointer to the frame bytes.
   * @param size Number of bytes available at @p data.
   */
  void on_inbound(const std::uint8_t *data, std::size_t size);

  /**
   * @brief Drain pending outbound frames (local changes and file offers).
   * @return Wire-encoded frames to send, oldest first.
   */
  std::vector<std::vector<std::uint8_t>> drain_outbound();

  /**
   * @brief Queue a file-transfer offer (kind=4) built from a host file path.
   * @param path Absolute path to a readable regular file on the host.
   * @return The offer JSON on success, or an empty string when @p path is not
   *         a readable regular file.
   */
  std::string offer_file(const std::string &path);

  // ---- Blob store (out-of-band payloads on the paired HTTPS server) -----

  namespace blob {
    /**
     * @brief Store bytes for out-of-band retrieval.
     * @param mime MIME type reported when the blob is served.
     * @param bytes The blob contents.
     * @return The blob id used in kind=3 refs, or an empty string when full.
     */
    std::string put(std::string mime, std::vector<std::uint8_t> bytes);

    /**
     * @brief Fetch and consume a stored blob by id.
     * @param id The blob id from a kind=3 ref.
     * @return The (mime, bytes) pair, or std::nullopt if unknown/consumed.
     */
    std::optional<std::pair<std::string, std::vector<std::uint8_t>>> take(const std::string &id);

    /**
     * @brief Resolve a registered file-offer id to its on-disk path.
     * @param id The offer id from a kind=4 offer.
     * @return The file path, or std::nullopt if unknown. Does not consume;
     *         offers expire by TTL.
     */
    std::optional<std::string> file_path(const std::string &id);
  }  // namespace blob

}  // namespace clipboard
