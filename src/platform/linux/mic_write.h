/**
 * @file src/platform/linux/mic_write.h
 * @brief Declarations for the PipeWire remote-microphone output ("Zenith Mic").
 */
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

namespace platf::pw_mic {

  /**
   * @brief A virtual PipeWire source node fed by remote (Moonlight client) microphone audio.
   *
   * Appears to desktop applications as a normal microphone named "Zenith Mic".
   * Payloads are Opus frames (48 kHz mono) as received from the mic UDP stream,
   * already decrypted by the caller.
   */
  class mic_out_t {
  public:
    virtual ~mic_out_t() = default;

    /**
     * @brief Decode one Opus payload and queue its PCM for the virtual source.
     *
     * @param data Opus frame payload (decrypted).
     * @param size Payload size in bytes.
     * @param seq Little-endian-decoded RTP sequence number (currently informational).
     * @return Number of bytes consumed, or -1 on failure.
     */
    virtual int write(const char *data, std::size_t size, std::uint16_t seq) = 0;
  };

  /**
   * @brief Create the virtual source node and start its PipeWire thread.
   *
   * @return The mic output, or nullptr when PipeWire is unavailable.
   */
  std::unique_ptr<mic_out_t> create();

}  // namespace platf::pw_mic
