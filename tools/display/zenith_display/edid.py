"""Minimal EDID 1.4 generation and parsing.

Providers that fabricate a monitor (EVDI, DRM debugfs override) must hand the
kernel an EDID.  We generate a 128-byte base block advertising exactly one
detailed timing — the mode the client asked for — under the monitor name
``ZenithVDD`` so the rest of the stack (and the humans debugging it) can spot
our virtual displays.  The parser is intentionally tiny: just enough to
recognize our own displays and verify generated blocks in tests.
"""

from __future__ import annotations

import struct
from typing import Optional

from . import VDD_MONITOR_NAME
from .modes import Mode, Timing, cvt_rb

_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def _manufacturer_id(letters: str) -> bytes:
    """Pack a three-letter PNP id (A=1 .. Z=26, 5 bits each, big-endian)."""
    a, b, c = (ord(ch) - ord("A") + 1 for ch in letters.upper())
    packed = (a << 10) | (b << 5) | c
    return struct.pack(">H", packed)


def _detailed_timing(t: Timing) -> bytes:
    """18-byte Detailed Timing Descriptor for a CVT-RB timing."""
    m = t.mode
    h_blank = t.h_total - m.width
    v_blank = t.v_total - m.height
    clock_10khz = t.pixel_clock_khz // 10

    d = bytearray(18)
    d[0:2] = struct.pack("<H", clock_10khz)
    d[2] = m.width & 0xFF
    d[3] = h_blank & 0xFF
    d[4] = ((m.width >> 8) << 4) | (h_blank >> 8)
    d[5] = m.height & 0xFF
    d[6] = v_blank & 0xFF
    d[7] = ((m.height >> 8) << 4) | (v_blank >> 8)
    d[8] = t.h_front & 0xFF
    d[9] = t.h_sync & 0xFF
    d[10] = ((t.v_front & 0x0F) << 4) | (t.v_sync & 0x0F)
    d[11] = (
        ((t.h_front >> 8) << 6)
        | ((t.h_sync >> 8) << 4)
        | ((t.v_front >> 4) << 2)
        | (t.v_sync >> 4)
    )
    # Physical size left at zero (virtual display); border pixels zero.
    d[17] = 0x1E  # digital, separate sync, +hsync +vsync
    return bytes(d)


def _display_name_descriptor(name: str) -> bytes:
    payload = name.encode("ascii", "replace")[:13]
    payload += b"\x0a"
    payload = payload.ljust(13, b" ")
    return b"\x00\x00\x00\xfc\x00" + payload


_DUMMY_DESCRIPTOR = b"\x00\x00\x00\x10\x00" + b"\x00" * 13


_DTD_MAX_CLOCK_KHZ = 655_350  # EDID 1.4 detailed-timing ceiling (16-bit, 10 kHz units)


_DTD_MAX_DIMENSION = 4095  # 12-bit active-pixel fields in a classic DTD


def _fitting_timing(mode: Mode) -> Timing:
    """CVT-RB timing that fits a DTD, degrading refresh if physics demands.

    A classic EDID descriptor tops out at 655.35 MHz — beyond that (e.g.
    4K@120) real monitors switch to DisplayID/CTA extensions.  A virtual
    display can simply serve the highest refresh that fits; the stream's
    FPS cap does the rest.
    """
    if mode.width > _DTD_MAX_DIMENSION or mode.height > _DTD_MAX_DIMENSION:
        raise ValueError(
            f"{mode} exceeds the EDID detailed-timing limit of "
            f"{_DTD_MAX_DIMENSION}px per axis; lower the client resolution"
        )
    for refresh in (mode.refresh, 120, 100, 60, 30):
        if refresh > mode.refresh:
            continue
        candidate = cvt_rb(Mode(mode.width, mode.height, refresh))
        if candidate.pixel_clock_khz <= _DTD_MAX_CLOCK_KHZ:
            return candidate
    raise ValueError(f"no DTD-representable timing for {mode}")


def generate(mode: Mode, name: str = VDD_MONITOR_NAME) -> bytes:
    """Build a valid 128-byte EDID advertising `mode` as the native timing."""
    timing = _fitting_timing(mode)

    block = bytearray(128)
    block[0:8] = _HEADER
    block[8:10] = _manufacturer_id("ZNH")
    block[10:12] = struct.pack("<H", 0x0001)  # product code
    block[12:16] = struct.pack("<I", 0)  # serial
    block[16] = 1  # week
    block[17] = 36  # 1990 + 36 = 2026
    block[18] = 1  # EDID 1.4
    block[19] = 4
    block[20] = 0xA5  # digital input, 8 bpc, DisplayPort
    block[21] = 0  # width cm: undefined (virtual)
    block[22] = 0
    block[23] = 120  # gamma 2.2
    block[24] = 0x06  # sRGB default, preferred timing is native
    block[25:35] = bytes(  # canned sRGB chromaticity
        (0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54)
    )
    # Established/standard timings: none — the DTD is the whole story.
    block[38:54] = b"\x01\x01" * 8

    block[54:72] = _detailed_timing(timing)
    block[72:90] = _display_name_descriptor(name)
    block[90:108] = _DUMMY_DESCRIPTOR
    block[108:126] = _DUMMY_DESCRIPTOR
    block[126] = 0  # no extensions
    block[127] = (256 - sum(block[:127]) % 256) % 256
    return bytes(block)


def monitor_name(edid: bytes) -> Optional[str]:
    """Extract the display product name (0xFC descriptor), if present."""
    if len(edid) < 128 or edid[:8] != _HEADER:
        return None
    for offset in (54, 72, 90, 108):
        desc = edid[offset:offset + 18]
        if desc[0:3] == b"\x00\x00\x00" and desc[3] == 0xFC:
            return desc[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
    return None


def parse_dtd_mode(edid: bytes) -> Optional[Mode]:
    """Recover (width, height, refresh) from the first DTD — used by tests."""
    if len(edid) < 72:
        return None
    d = edid[54:72]
    clock_khz = struct.unpack("<H", d[0:2])[0] * 10
    if clock_khz == 0:
        return None
    width = d[2] | ((d[4] >> 4) << 8)
    h_blank = d[3] | ((d[4] & 0x0F) << 8)
    height = d[5] | ((d[7] >> 4) << 8)
    v_blank = d[6] | ((d[7] & 0x0F) << 8)
    refresh = round(clock_khz * 1000 / ((width + h_blank) * (height + v_blank)))
    return Mode(width=width, height=height, refresh=refresh)
