/**
 * @file src/platform/linux/mic_write.cpp
 * @brief PipeWire virtual source fed by the remote microphone stream.
 *
 * Creates a `media.class = Audio/Source` stream node named "Zenith Mic" so any
 * desktop application (Discord, games, recorders) can select the remote client's
 * microphone like real hardware. Opus decode happens here, mirroring the
 * platform contract used on Windows by Sunshine-Foundation (WASAPI + virtual
 * driver there, a PipeWire node here).
 */
#include "mic_write.h"

#include <array>
#include <mutex>
#include <vector>

#include <opus/opus.h>
#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>
#include <spa/utils/result.h>

#include "src/logging.h"
#include "src/utility.h"

using namespace std::literals;

namespace platf::pw_mic {

  namespace {
    constexpr std::uint32_t kSampleRate = 48000;
    constexpr int kChannels = 1;
    // 120 ms is the maximum Opus frame duration.
    constexpr int kMaxOpusFrameSamples = kSampleRate * 120 / 1000;
    // One second of buffered audio; deeper only adds latency on sustained overrun.
    constexpr std::size_t kRingCapacity = kSampleRate;
  }  // namespace

  /**
   * @brief PipeWire-backed virtual microphone ("Zenith Mic") fed by remote Opus frames.
   */
  class pipewire_mic_t: public mic_out_t {
  public:
    ~pipewire_mic_t() override {
      if (loop) {
        pw_thread_loop_lock(loop);
        if (stream) {
          pw_stream_destroy(stream);
        }
        pw_thread_loop_unlock(loop);
        pw_thread_loop_stop(loop);
        pw_thread_loop_destroy(loop);
      }
      if (decoder) {
        opus_decoder_destroy(decoder);
      }
    }

    /**
     * @brief Create the Opus decoder and the PipeWire source stream.
     * @return true when the virtual microphone is ready for samples.
     */
    bool init() {
      pw_init(nullptr, nullptr);

      int opus_err = 0;
      decoder = opus_decoder_create(kSampleRate, kChannels, &opus_err);
      if (opus_err != OPUS_OK) {
        BOOST_LOG(error) << "zenith-mic: opus_decoder_create failed: "sv << opus_strerror(opus_err);
        return false;
      }

      ring.resize(kRingCapacity);

      loop = pw_thread_loop_new("zenith-mic", nullptr);
      if (!loop) {
        BOOST_LOG(error) << "zenith-mic: pw_thread_loop_new failed"sv;
        return false;
      }

      auto props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Playback",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_MEDIA_CLASS, "Audio/Source",
        PW_KEY_NODE_NAME, "zenith-mic",
        PW_KEY_NODE_DESCRIPTION, "Zenith Mic (remote client)",
        PW_KEY_NODE_VIRTUAL, "true",
        nullptr);

      static const pw_stream_events stream_events = []() {
        pw_stream_events ev = {};
        ev.version = PW_VERSION_STREAM_EVENTS;
        ev.process = &pipewire_mic_t::on_process;
        return ev;
      }();

      pw_thread_loop_lock(loop);
      auto unlock_guard = util::fail_guard([this]() {
        pw_thread_loop_unlock(loop);
      });

      stream = pw_stream_new_simple(
        pw_thread_loop_get_loop(loop),
        "zenith-mic",
        props,
        &stream_events,
        this);
      if (!stream) {
        BOOST_LOG(error) << "zenith-mic: pw_stream_new_simple failed"sv;
        return false;
      }

      std::uint8_t param_buffer[1024];
      spa_pod_builder builder = SPA_POD_BUILDER_INIT(param_buffer, sizeof(param_buffer));
      spa_audio_info_raw audio_format = {};
      audio_format.format = SPA_AUDIO_FORMAT_S16;
      audio_format.rate = kSampleRate;
      audio_format.channels = kChannels;
      audio_format.position[0] = SPA_AUDIO_CHANNEL_MONO;
      const spa_pod *params[1];
      params[0] = spa_format_audio_raw_build(&builder, SPA_PARAM_EnumFormat, &audio_format);

      int err = pw_stream_connect(
        stream,
        PW_DIRECTION_OUTPUT,
        PW_ID_ANY,
        static_cast<pw_stream_flags>(PW_STREAM_FLAG_MAP_BUFFERS | PW_STREAM_FLAG_RT_PROCESS),
        params,
        1);
      if (err < 0) {
        BOOST_LOG(error) << "zenith-mic: pw_stream_connect failed: "sv << spa_strerror(err);
        return false;
      }

      if (pw_thread_loop_start(loop) != 0) {
        BOOST_LOG(error) << "zenith-mic: pw_thread_loop_start failed"sv;
        return false;
      }

      BOOST_LOG(info) << "zenith-mic: virtual source node created (48 kHz mono)"sv;
      return true;
    }

    int write(const char *data, std::size_t size, std::uint16_t seq) override {
      (void) seq;  // FEC/PLC on sequence gaps is a v2 concern; see docs/design/remote-mic.md

      std::array<opus_int16, kMaxOpusFrameSamples> pcm;
      int samples = opus_decode(
        decoder,
        reinterpret_cast<const unsigned char *>(data),
        static_cast<opus_int32>(size),
        pcm.data(),
        kMaxOpusFrameSamples,
        0);
      if (samples < 0) {
        BOOST_LOG(warning) << "zenith-mic: opus_decode failed: "sv << opus_strerror(samples);
        return -1;
      }

      std::lock_guard lock(ring_mutex);
      for (int i = 0; i < samples; ++i) {
        ring[write_pos % kRingCapacity] = pcm[i];
        ++write_pos;
      }
      // On overrun the oldest audio is overwritten; advance the reader to match.
      if (write_pos - read_pos > kRingCapacity) {
        read_pos = write_pos - kRingCapacity;
      }
      return static_cast<int>(size);
    }

  private:
    static void on_process(void *userdata) {
      auto self = static_cast<pipewire_mic_t *>(userdata);

      pw_buffer *b = pw_stream_dequeue_buffer(self->stream);
      if (!b) {
        return;
      }
      spa_data &d = b->buffer->datas[0];
      if (!d.data) {
        pw_stream_queue_buffer(self->stream, b);
        return;
      }

      constexpr std::uint32_t stride = sizeof(opus_int16) * kChannels;
      std::uint32_t max_frames = d.maxsize / stride;
      if (b->requested) {
        max_frames = std::min<std::uint32_t>(max_frames, b->requested);
      }

      auto out = static_cast<opus_int16 *>(d.data);
      std::uint32_t filled = 0;
      {
        std::lock_guard lock(self->ring_mutex);
        while (filled < max_frames && self->read_pos < self->write_pos) {
          out[filled++] = self->ring[self->read_pos % kRingCapacity];
          ++self->read_pos;
        }
      }
      // Underrun → emit silence so the node keeps producing a steady clock.
      for (; filled < max_frames; ++filled) {
        out[filled] = 0;
      }

      d.chunk->offset = 0;
      d.chunk->stride = stride;
      d.chunk->size = max_frames * stride;
      pw_stream_queue_buffer(self->stream, b);
    }

    pw_thread_loop *loop = nullptr;
    pw_stream *stream = nullptr;
    OpusDecoder *decoder = nullptr;

    std::mutex ring_mutex;
    std::vector<opus_int16> ring;
    std::uint64_t read_pos = 0;
    std::uint64_t write_pos = 0;
  };

  std::unique_ptr<mic_out_t> create() {
    auto mic = std::make_unique<pipewire_mic_t>();
    if (!mic->init()) {
      return nullptr;
    }
    return mic;
  }

}  // namespace platf::pw_mic
