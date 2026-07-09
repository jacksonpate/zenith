/**
 * @file src/clipboard.cpp
 * @brief Clipboard sync engine: wire codec, echo suppression, blob store,
 *        file offers. See clipboard.h for the protocol contract.
 */
// standard includes
#include <array>
#include <chrono>
#include <deque>
#include <filesystem>
#include <mutex>
#include <thread>
#include <unordered_map>

// lib includes
#include <nlohmann/json.hpp>
#include <openssl/rand.h>
#include <openssl/sha.h>

// local includes
#include "clipboard.h"
#include "config.h"
#include "logging.h"
#include "platform/common.h"
#include "uuid.h"

using namespace std::literals;

namespace clipboard {

  namespace {
    using clock = std::chrono::steady_clock;

    constexpr auto kEchoTtl = 5s;
    constexpr auto kBlobTtl = 120s;
    constexpr auto kOfferTtl = 300s;
    constexpr std::size_t kBlobStoreCap = 200 * 1024 * 1024;

    void put_u32le(std::vector<std::uint8_t> &out, std::uint32_t v) {
      out.push_back(v & 0xFF);
      out.push_back((v >> 8) & 0xFF);
      out.push_back((v >> 16) & 0xFF);
      out.push_back((v >> 24) & 0xFF);
    }

    std::uint32_t get_u32le(const std::uint8_t *p) {
      return (std::uint32_t) p[0] | ((std::uint32_t) p[1] << 8) |
             ((std::uint32_t) p[2] << 16) | ((std::uint32_t) p[3] << 24);
    }

    std::array<std::uint8_t, SHA256_DIGEST_LENGTH> digest(const std::vector<std::uint8_t> &bytes) {
      std::array<std::uint8_t, SHA256_DIGEST_LENGTH> d;
      SHA256(bytes.data(), bytes.size(), d.data());
      return d;
    }

    std::uint32_t random_token() {
      std::uint32_t t = 0;
      RAND_bytes(reinterpret_cast<unsigned char *>(&t), sizeof(t));
      return t;
    }

    struct engine_t {
      std::mutex mtx;
      bool watching = false;

      // Echo suppression: hashes of payloads we recently applied locally.
      std::deque<std::pair<std::array<std::uint8_t, SHA256_DIGEST_LENGTH>, clock::time_point>> applied;

      std::deque<std::vector<std::uint8_t>> outbound;  ///< wire-encoded frames

      void remember_applied(const std::vector<std::uint8_t> &bytes) {
        applied.emplace_back(digest(bytes), clock::now());
        while (applied.size() > 32) {
          applied.pop_front();
        }
      }

      bool is_echo(const std::vector<std::uint8_t> &bytes) {
        auto now = clock::now();
        auto d = digest(bytes);
        std::erase_if(applied, [&](const auto &e) {
          return now - e.second > kEchoTtl;
        });
        for (const auto &e : applied) {
          if (e.first == d) {
            return true;
          }
        }
        return false;
      }
    };

    engine_t &engine() {
      static engine_t instance;
      return instance;
    }

    struct blob_entry_t {
      std::string mime;
      std::vector<std::uint8_t> bytes;
      std::string path;  ///< non-empty for file offers (served from disk)
      clock::time_point expires;
    };

    struct blob_store_t {
      std::mutex mtx;
      std::unordered_map<std::string, blob_entry_t> entries;
      std::size_t bytes_total = 0;

      void sweep() {
        auto now = clock::now();
        for (auto it = entries.begin(); it != entries.end();) {
          if (now > it->second.expires) {
            bytes_total -= it->second.bytes.size();
            it = entries.erase(it);
          } else {
            ++it;
          }
        }
      }
    };

    blob_store_t &blobs() {
      static blob_store_t instance;
      return instance;
    }

    /// Queue the current local clipboard content as an outbound frame.
    void queue_local_clipboard() {
      auto content = platf::clipboard::read();
      if (!content) {
        return;
      }
      auto &[mime, bytes] = *content;
      if (bytes.empty()) {
        return;
      }

      auto &eng = engine();
      std::lock_guard lg(eng.mtx);
      if (eng.is_echo(bytes)) {
        return;  // our own inbound write coming back around
      }

      bool is_text = mime.rfind("text/", 0) == 0;
      if (is_text && bytes.size() > kMaxTextBytes) {
        return;
      }
      if (!is_text && bytes.size() > kMaxBlobBytes) {
        return;
      }

      frame_t frame;
      frame.token = random_token();
      if (bytes.size() < kInlineThreshold) {
        frame.kind = is_text ? kind_e::text : kind_e::png;
        frame.payload = std::move(bytes);
      } else {
        auto size = bytes.size();
        auto id = blob::put(is_text ? "text/plain; charset=utf-8" : "image/png", std::move(bytes));
        nlohmann::json ref {{"id", id}, {"mime", is_text ? "text/plain; charset=utf-8" : "image/png"}, {"size", size}};
        auto text = ref.dump();
        frame.kind = kind_e::ref;
        frame.payload.assign(text.begin(), text.end());
      }
      BOOST_LOG(info) << "clipboard: queued local change kind="sv << (int) frame.kind
                      << " bytes="sv << frame.payload.size();
      eng.outbound.emplace_back(encode(frame));
    }

    /// Apply an inbound payload to the local clipboard, arming echo
    /// suppression first so the watcher skips the resulting change event.
    void apply_local(const std::string &mime, std::vector<std::uint8_t> bytes) {
      auto &eng = engine();
      {
        std::lock_guard lg(eng.mtx);
        eng.remember_applied(bytes);
      }
      if (!platf::clipboard::write(mime, bytes)) {
        BOOST_LOG(warning) << "clipboard: applying inbound "sv << mime << " ("sv << bytes.size() << " bytes) failed"sv;
      }
    }
  }  // namespace

  std::vector<std::uint8_t> encode(const frame_t &frame) {
    std::vector<std::uint8_t> out;
    if (frame.payload.size() > kMaxBlobBytes) {
      return out;
    }
    out.reserve(10 + frame.payload.size());
    out.push_back(kWireVersion);
    out.push_back(static_cast<std::uint8_t>(frame.kind));
    put_u32le(out, frame.token);
    put_u32le(out, (std::uint32_t) frame.payload.size());
    out.insert(out.end(), frame.payload.begin(), frame.payload.end());
    return out;
  }

  std::optional<frame_t> decode(const std::uint8_t *data, std::size_t size) {
    if (!data || size < 10 || data[0] != kWireVersion) {
      return std::nullopt;
    }
    auto kind = data[1];
    if (kind < 1 || kind > 4) {
      return std::nullopt;
    }
    auto length = get_u32le(data + 6);
    if (length != size - 10 || length > kMaxBlobBytes) {
      return std::nullopt;
    }
    frame_t frame;
    frame.kind = static_cast<kind_e>(kind);
    frame.token = get_u32le(data + 2);
    frame.payload.assign(data + 10, data + 10 + length);
    return frame;
  }

  bool available() {
    if (!config::input.clipboard_sync) {
      return false;
    }
    return platf::clipboard::available();
  }

  void start() {
    auto &eng = engine();
    std::lock_guard lg(eng.mtx);
    if (eng.watching) {
      return;
    }
    if (!config::input.clipboard_sync) {
      BOOST_LOG(info) << "clipboard: sync disabled by configuration"sv;
      return;
    }
    eng.watching = platf::clipboard::start_watch([]() {
      queue_local_clipboard();
    });
    if (eng.watching) {
      BOOST_LOG(info) << "clipboard: watching the local clipboard for changes"sv;
    } else {
      BOOST_LOG(warning) << "clipboard: could not watch the local clipboard; host-to-client sync is off"sv;
    }
  }

  void stop() {
    auto &eng = engine();
    std::lock_guard lg(eng.mtx);
    if (!eng.watching) {
      return;
    }
    platf::clipboard::stop_watch();
    eng.watching = false;
    eng.outbound.clear();
  }

  void on_inbound(const std::uint8_t *data, std::size_t size) {
    auto frame = decode(data, size);
    if (frame) {
      BOOST_LOG(info) << "clipboard: inbound frame kind="sv << (int) frame->kind
                      << " bytes="sv << frame->payload.size();
    }
    if (!frame) {
      BOOST_LOG(warning) << "clipboard: dropping malformed inbound frame ("sv << size << " bytes)"sv;
      return;
    }

    switch (frame->kind) {
      case kind_e::text:
        if (frame->payload.size() <= kMaxTextBytes) {
          apply_local("text/plain; charset=utf-8", std::move(frame->payload));
        }
        break;
      case kind_e::png:
        apply_local("image/png", std::move(frame->payload));
        break;
      case kind_e::ref:
        {
          auto ref = nlohmann::json::parse(frame->payload.begin(), frame->payload.end(), nullptr, false);
          if (ref.is_discarded() || !ref.contains("id")) {
            break;
          }
          // The client uploads the blob before (or right after) sending the
          // ref; a short grace period covers the race.
          auto id = ref["id"].get<std::string>();
          for (int attempt = 0; attempt < 10; ++attempt) {
            if (auto blob = blob::take(id)) {
              apply_local(blob->first, std::move(blob->second));
              return;
            }
            std::this_thread::sleep_for(200ms);
          }
          BOOST_LOG(warning) << "clipboard: blob ref "sv << id << " never arrived"sv;
        }
        break;
      case kind_e::file_offer:
        // Client -> host file offers have no ecosystem client yet; log so we
        // notice the first client that tries.
        BOOST_LOG(info) << "clipboard: inbound file offer (unsupported direction): "sv
                        << std::string_view((const char *) frame->payload.data(), std::min<std::size_t>(frame->payload.size(), 256));
        break;
    }
  }

  std::vector<std::vector<std::uint8_t>> drain_outbound() {
    auto &eng = engine();
    std::lock_guard lg(eng.mtx);
    std::vector<std::vector<std::uint8_t>> out(eng.outbound.begin(), eng.outbound.end());
    eng.outbound.clear();
    return out;
  }

  std::string offer_file(const std::string &path) {
    std::error_code ec;
    auto canonical = std::filesystem::canonical(path, ec);
    if (ec || !std::filesystem::is_regular_file(canonical, ec)) {
      return {};
    }
    auto size = std::filesystem::file_size(canonical, ec);
    if (ec) {
      return {};
    }

    auto id = uuid_util::uuid_t::generate().string();
    {
      auto &store = blobs();
      std::lock_guard lg(store.mtx);
      store.sweep();
      store.entries[id] = blob_entry_t {"application/octet-stream", {}, canonical.string(), clock::now() + kOfferTtl};
    }

    nlohmann::json offer {
      {"id", id},
      {"name", canonical.filename().string()},
      {"size", size},
      {"mime", "application/octet-stream"},
      {"download_url", "/clipboard/file/"s + id},
      {"expires_in", std::chrono::duration_cast<std::chrono::seconds>(kOfferTtl).count()},
      {"type", "file"},
    };
    auto text = offer.dump();

    frame_t frame;
    frame.kind = kind_e::file_offer;
    frame.token = random_token();
    frame.payload.assign(text.begin(), text.end());

    auto &eng = engine();
    std::lock_guard lg(eng.mtx);
    eng.outbound.emplace_back(encode(frame));
    return text;
  }

  namespace blob {
    std::string put(std::string mime, std::vector<std::uint8_t> bytes) {
      auto &store = blobs();
      std::lock_guard lg(store.mtx);
      store.sweep();
      if (bytes.size() > kMaxBlobBytes || store.bytes_total + bytes.size() > kBlobStoreCap) {
        return {};
      }
      auto id = uuid_util::uuid_t::generate().string();
      store.bytes_total += bytes.size();
      store.entries[id] = blob_entry_t {std::move(mime), std::move(bytes), {}, clock::now() + kBlobTtl};
      return id;
    }

    std::optional<std::pair<std::string, std::vector<std::uint8_t>>> take(const std::string &id) {
      auto &store = blobs();
      std::lock_guard lg(store.mtx);
      auto it = store.entries.find(id);
      if (it == store.entries.end() || !it->second.path.empty()) {
        return std::nullopt;
      }
      auto result = std::make_pair(std::move(it->second.mime), std::move(it->second.bytes));
      store.bytes_total -= result.second.size();
      store.entries.erase(it);
      return result;
    }

    std::optional<std::string> file_path(const std::string &id) {
      auto &store = blobs();
      std::lock_guard lg(store.mtx);
      store.sweep();
      auto it = store.entries.find(id);
      if (it == store.entries.end() || it->second.path.empty()) {
        return std::nullopt;
      }
      return it->second.path;
    }
  }  // namespace blob

}  // namespace clipboard
