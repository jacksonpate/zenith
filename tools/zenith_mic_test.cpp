/**
 * @file tools/zenith_mic_test.cpp
 * @brief Dev harness: feed a 440 Hz sine through the Zenith Mic PipeWire node.
 *
 * Exercises the exact production path (Opus encode -> platf::pw_mic decode ->
 * virtual source) without a Moonlight client. While it runs, record the node:
 *
 *   ./zenith-mic-test 5 &
 *   pw-record --target zenith-mic /tmp/mic_test.wav
 *
 * A clean 440 Hz tone in the capture proves the platform layer end to end.
 */
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include <opus/opus.h>

#include "src/platform/linux/mic_write.h"

int main(int argc, char *argv[]) {
  int seconds = argc > 1 ? std::atoi(argv[1]) : 5;

  auto mic = platf::pw_mic::create();
  if (!mic) {
    std::fprintf(stderr, "failed to create Zenith Mic node (is PipeWire running?)\n");
    return 1;
  }
  std::printf("Zenith Mic node up; feeding %d s of 440 Hz\n", seconds);

  int err = 0;
  OpusEncoder *enc = opus_encoder_create(48000, 1, OPUS_APPLICATION_VOIP, &err);
  if (err != OPUS_OK) {
    std::fprintf(stderr, "opus_encoder_create: %s\n", opus_strerror(err));
    return 1;
  }

  constexpr int kFrameSamples = 960;  // 20 ms @ 48 kHz
  std::vector<opus_int16> pcm(kFrameSamples);
  std::vector<unsigned char> packet(1500);

  double phase = 0.0;
  const double step = 2.0 * M_PI * 440.0 / 48000.0;
  std::uint16_t seq = 0;

  const int frames = seconds * 50;  // 50 x 20 ms per second
  auto next = std::chrono::steady_clock::now();
  for (int f = 0; f < frames; ++f) {
    for (int i = 0; i < kFrameSamples; ++i) {
      pcm[i] = static_cast<opus_int16>(std::sin(phase) * 12000.0);
      phase += step;
    }
    opus_int32 len = opus_encode(enc, pcm.data(), kFrameSamples, packet.data(), packet.size());
    if (len < 0) {
      std::fprintf(stderr, "opus_encode: %s\n", opus_strerror(len));
      break;
    }
    if (mic->write(reinterpret_cast<const char *>(packet.data()), len, seq++) < 0) {
      std::fprintf(stderr, "mic write failed at frame %d\n", f);
      break;
    }
    next += std::chrono::milliseconds(20);
    std::this_thread::sleep_until(next);
  }

  opus_encoder_destroy(enc);
  std::printf("done\n");
  return 0;
}
