/**
 * @file src/platform/clipboard_stub.cpp
 * @brief Clipboard backend stub for platforms without an implementation.
 *
 * Reporting unavailable keeps the RTSP capability bits off, so clients never
 * attempt sync. Windows needs a user-session agent (the service runs in
 * session 0) — that lands with the native Windows integration.
 */
// local includes
#include "src/platform/common.h"

namespace platf::clipboard {

  /**
   * @brief Clipboard access is unavailable on this platform.
   * @return Always false.
   */
  bool available() {
    return false;
  }

  /**
   * @brief No-op: clipboard watching is unavailable on this platform.
   * @return Always false.
   */
  bool start_watch(std::function<void()>) {
    return false;
  }

  /**
   * @brief No-op: clipboard watching is unavailable on this platform.
   */
  void stop_watch() {
  }

  /**
   * @brief No-op: clipboard reads are unavailable on this platform.
   * @return Always std::nullopt.
   */
  std::optional<std::pair<std::string, std::vector<std::uint8_t>>> read() {
    return std::nullopt;
  }

  /**
   * @brief No-op: clipboard writes are unavailable on this platform.
   * @return Always false.
   */
  bool write(const std::string &, const std::vector<std::uint8_t> &) {
    return false;
  }

}  // namespace platf::clipboard
