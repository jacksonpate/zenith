/**
 * @file src/platform/linux/clipboard_linux.cpp
 * @brief In-session clipboard access via wl-clipboard (Wayland) or xclip (X11).
 *
 * Wayland change detection uses `wl-paste --watch`, which requires the
 * compositor to implement wlr-data-control (KWin, wlroots — the fleet).
 * X11 falls back to a 1 s polling loop keyed on a content hash. GNOME
 * Wayland (no data-control) reports unavailable rather than half-working.
 */
// standard includes
#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <thread>

// lib includes
#include <boost/process/v1.hpp>
#include <openssl/sha.h>

// local includes
#include "src/logging.h"
#include "src/platform/common.h"

using namespace std::literals;
namespace bp = boost::process::v1;

namespace platf::clipboard {

  namespace {
    constexpr auto kToolTimeout = 5s;
    constexpr std::size_t kMaxRead = 50 * 1024 * 1024;

    /// The Wayland socket for the clipboard tools.
    ///
    /// Zenith frequently runs as a systemd user service whose environment has
    /// no WAYLAND_DISPLAY even though the user's compositor is up, so fall
    /// back to discovering the socket in XDG_RUNTIME_DIR. The value is only
    /// ever handed to the wl-clipboard child processes: exporting it into
    /// this process would flip the capture layer onto its Wayland path.
    const std::string &wayland_display() {
      static const std::string display = []() -> std::string {
        if (auto env = std::getenv("WAYLAND_DISPLAY"); env && *env) {
          return env;
        }
        auto runtime_dir = std::getenv("XDG_RUNTIME_DIR");
        if (!runtime_dir || !*runtime_dir) {
          return {};
        }
        // Prefer wayland-0, else the lowest-numbered socket present.
        std::vector<std::string> candidates;
        std::error_code ec;
        for (const auto &entry : std::filesystem::directory_iterator(runtime_dir, ec)) {
          auto name = entry.path().filename().string();
          if (name.rfind("wayland-", 0) == 0 && name.find(".lock") == std::string::npos) {
            candidates.push_back(std::move(name));
          }
        }
        if (candidates.empty()) {
          return {};
        }
        std::ranges::sort(candidates);
        return candidates.front();
      }();
      return display;
    }

    bool is_wayland() {
      return !wayland_display().empty();
    }

    bool is_x11() {
      auto env = std::getenv("DISPLAY");
      return env && *env;
    }

    /// Child environment carrying the discovered Wayland socket.
    bp::environment tool_env() {
      auto env = boost::this_process::environment();
      if (auto &display = wayland_display(); !display.empty()) {
        env["WAYLAND_DISPLAY"] = display;
      }
      return env;
    }

    std::string find_tool(const char *name) {
      auto path = bp::search_path(name);
      return path.empty() ? std::string {} : path.string();
    }

    /// Whether the compositor implements wlr-data-control, which `wl-paste
    /// --watch` needs to observe clipboard changes. KDE, Sway and Hyprland do;
    /// GNOME/Mutter does not, so those hosts fall back to polling.
    ///
    /// `wl-paste --watch` prints its complaint and still exits 0 on an
    /// unsupported compositor, so probe by checking stderr rather than the
    /// exit status.
    bool has_data_control() {
      static const bool supported = []() {
        try {
          bp::ipstream err;
          bp::child probe(
            std::vector<std::string> {find_tool("wl-paste"), "--watch", "true"},
            tool_env(),
            bp::std_out > bp::null,
            bp::std_err > err
          );
          // The complaint, if any, is printed immediately; a supported
          // compositor keeps the process alive instead.
          std::this_thread::sleep_for(500ms);
          bool alive = probe.running();
          probe.terminate();
          probe.wait();
          if (alive) {
            return true;
          }
          std::string line;
          std::getline(err, line);
          if (line.find("data-control") != std::string::npos) {
            BOOST_LOG(info) << "clipboard: compositor lacks wlr-data-control; polling for changes"sv;
          }
          return false;
        } catch (const std::exception &) {
          return false;
        }
      }();
      return supported;
    }

    /// Run `argv`, feed `input` to stdin when non-null, capture stdout.
    /// Returns false on spawn failure, nonzero exit, or output overflow.
    bool exec_capture(const std::vector<std::string> &argv, const std::vector<std::uint8_t> *input, std::vector<std::uint8_t> &output) {
      try {
        bp::pipe out_pipe;
        bp::opstream in_stream;
        bp::child proc(argv, tool_env(), bp::std_out > out_pipe, bp::std_err > bp::null, bp::std_in < in_stream);

        if (input) {
          in_stream.write((const char *) input->data(), input->size());
        }
        in_stream.flush();
        in_stream.pipe().close();

        output.clear();
        std::uint8_t buf[64 * 1024];
        int n;
        while ((n = out_pipe.read((char *) buf, sizeof(buf))) > 0) {
          if (output.size() + n > kMaxRead) {
            proc.terminate();
            return false;
          }
          output.insert(output.end(), buf, buf + n);
        }
        proc.wait();
        return proc.exit_code() == 0;
      } catch (const std::exception &e) {
        BOOST_LOG(warning) << "clipboard: exec failed: "sv << e.what();
        return false;
      }
    }

    std::string list_types() {
      std::vector<std::uint8_t> out;
      if (is_wayland()) {
        if (!exec_capture({find_tool("wl-paste"), "--list-types"}, nullptr, out)) {
          return {};
        }
      } else {
        if (!exec_capture({find_tool("xclip"), "-selection", "clipboard", "-t", "TARGETS", "-o"}, nullptr, out)) {
          return {};
        }
      }
      return std::string(out.begin(), out.end());
    }

    struct watcher_t {
      std::atomic<bool> running {false};
      std::function<void()> cb;
      std::thread thread;
      std::unique_ptr<bp::child> child;  // wayland watch process

      ~watcher_t() {
        // Owned by a static; the process exits right after anyway.
      }
    };

    watcher_t &watcher() {
      static watcher_t instance;
      return instance;
    }
  }  // namespace

  bool available() {
    if (is_wayland()) {
      return !find_tool("wl-paste").empty() && !find_tool("wl-copy").empty();
    }
    if (is_x11()) {
      return !find_tool("xclip").empty();
    }
    return false;
  }

  std::optional<std::pair<std::string, std::vector<std::uint8_t>>> read() {
    if (!available()) {
      return std::nullopt;
    }
    auto types = list_types();
    std::vector<std::uint8_t> bytes;

    if (types.find("image/png") != std::string::npos) {
      bool ok = is_wayland() ?
                  exec_capture({find_tool("wl-paste"), "--type", "image/png"}, nullptr, bytes) :
                  exec_capture({find_tool("xclip"), "-selection", "clipboard", "-t", "image/png", "-o"}, nullptr, bytes);
      if (ok && !bytes.empty()) {
        return std::make_pair("image/png"s, std::move(bytes));
      }
    }

    bool ok = is_wayland() ?
                exec_capture({find_tool("wl-paste"), "--no-newline", "--type", "text/plain;charset=utf-8"}, nullptr, bytes) :
                exec_capture({find_tool("xclip"), "-selection", "clipboard", "-t", "UTF8_STRING", "-o"}, nullptr, bytes);
    if (ok && !bytes.empty()) {
      return std::make_pair("text/plain; charset=utf-8"s, std::move(bytes));
    }
    return std::nullopt;
  }

  bool write(const std::string &mime, const std::vector<std::uint8_t> &bytes) {
    if (!available()) {
      return false;
    }
    std::vector<std::uint8_t> ignored;
    if (is_wayland()) {
      return exec_capture({find_tool("wl-copy"), "--type", mime}, &bytes, ignored);
    }
    return exec_capture({find_tool("xclip"), "-selection", "clipboard", "-t", mime, "-i"}, &bytes, ignored);
  }

  bool start_watch(std::function<void()> cb) {
    auto &w = watcher();
    if (w.running.exchange(true)) {
      return true;
    }
    w.cb = std::move(cb);

    if (is_wayland() && has_data_control()) {
      // `wl-paste --watch CMD` runs CMD on every clipboard change; a pipe on
      // its stdout turns each change into one line we can block on.
      try {
        auto out_pipe = std::make_shared<bp::pipe>();
        w.child = std::make_unique<bp::child>(
          std::vector<std::string> {find_tool("wl-paste"), "--watch", "echo", "x"},
          tool_env(),
          bp::std_out > *out_pipe,
          bp::std_err > bp::null
        );
        w.thread = std::thread([out_pipe]() {
          char buf[64];
          int n;
          while ((n = out_pipe->read(buf, sizeof(buf))) > 0) {
            auto &w = watcher();
            if (!w.running) {
              break;
            }
            w.cb();
          }
        });
        return true;
      } catch (const std::exception &e) {
        BOOST_LOG(warning) << "clipboard: wl-paste --watch failed: "sv << e.what();
        w.running = false;
        return false;
      }
    }

    // X11, and Wayland compositors without wlr-data-control (GNOME/Mutter):
    // poll the clipboard and hash it to spot changes.
    if (is_wayland() || is_x11()) {
      w.thread = std::thread([]() {
        std::array<std::uint8_t, SHA256_DIGEST_LENGTH> last {};
        while (watcher().running) {
          std::this_thread::sleep_for(1s);
          auto content = read();
          if (!content) {
            continue;
          }
          std::array<std::uint8_t, SHA256_DIGEST_LENGTH> now;
          SHA256(content->second.data(), content->second.size(), now.data());
          if (now != last) {
            if (last != std::array<std::uint8_t, SHA256_DIGEST_LENGTH> {}) {
              watcher().cb();
            }
            last = now;
          }
        }
      });
      return true;
    }

    w.running = false;
    return false;
  }

  void stop_watch() {
    auto &w = watcher();
    if (!w.running.exchange(false)) {
      return;
    }
    if (w.child) {
      std::error_code ec;
      w.child->terminate(ec);
      w.child.reset();
    }
    if (w.thread.joinable()) {
      w.thread.join();
    }
  }

}  // namespace platf::clipboard
