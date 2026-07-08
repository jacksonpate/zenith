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

  bool available() {
    return false;
  }

  bool start_watch(std::function<void()>) {
    return false;
  }

  void stop_watch() {
  }

  std::optional<std::pair<std::string, std::vector<std::uint8_t>>> read() {
    return std::nullopt;
  }

  bool write(const std::string &, const std::vector<std::uint8_t> &) {
    return false;
  }

}  // namespace platf::clipboard
