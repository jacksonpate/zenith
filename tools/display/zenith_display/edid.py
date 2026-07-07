"""EDID 1.4 generation and parsing — the one implementation in the repo.

Two entry points:

* ``generate(mode)`` — 128-byte single-mode block for ephemeral VDDs
  (EVDI, DRM debugfs override): exactly the client's timing.
* ``generate_multi(modes)`` — multi-mode EDID (base block + CTA-861
  extension blocks) for permanently provisioned connectors; used by
  ``scripts/zenith-vdd-setup``.

Every display carries the monitor name ``ZenithVDD`` so the rest of the
stack (and the humans debugging it) can spot our virtual displays.  The
parser is intentionally tiny: just enough to recognize our own displays and
verify generated blocks in tests.
"""

from __future__ import annotations

import struct
from typing import Optional, Sequence, Tuple

from . import VDD_MONITOR_NAME
from .modes import Mode, Timing, cvt_rb

_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def _manufacturer_id(letters: str) -> bytes:
    """Pack a three-letter PNP id (A=1 .. Z=26, 5 bits each, big-endian)."""
    a, b, c = (ord(ch) - ord("A") + 1 for ch in letters.upper())
    packed = (a << 10) | (b << 5) | c
    return struct.pack(">H", packed)


def _detailed_timing(t: Timing, size_mm: Tuple[int, int] = (0, 0)) -> bytes:
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
    h_mm, v_mm = size_mm
    d[12] = h_mm & 0xFF
    d[13] = v_mm & 0xFF
    d[14] = ((h_mm >> 8) << 4) | (v_mm >> 8)
    d[17] = 0x1A  # digital, separate sync, +hsync -vsync (CVT-RB convention)
    return bytes(d)


def _display_name_descriptor(name: str) -> bytes:
    payload = name.encode("ascii", "replace")[:13]
    payload += b"\x0a"
    payload = payload.ljust(13, b" ")
    return b"\x00\x00\x00\xfc\x00" + payload


_DUMMY_DESCRIPTOR = b"\x00\x00\x00\x10\x00" + b"\x00" * 13

# Monitor range limits: 23-165 Hz vertical, 15-255 kHz horizontal, 660 MHz max
# pixel clock — generous bounds that keep every mode we generate in range.
_RANGE_LIMITS_DESCRIPTOR = bytes(
    [0, 0, 0, 0xFD, 0, 23, 165, 15, 255, 66, 0x0A, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20]
)

# Multi-mode layout: the base block has four 18-byte descriptor slots; two
# carry timings (the rest hold the name + range limits), and each CTA-861
# extension block fits six more.
_BASE_SLOTS = 2
_EXT_SLOTS = 6


def _checksummed(block: bytearray) -> bytes:
    block[127] = (256 - sum(block[:127]) % 256) % 256
    return bytes(block)


def _strict_timing(mode: Mode) -> Timing:
    """CVT-RB timing that must fit a DTD — provisioning never degrades silently."""
    timing = cvt_rb(mode)
    if timing.pixel_clock_khz // 10 > 0xFFFF:
        raise ValueError(
            f"{mode}: pixel clock {timing.pixel_clock_khz / 1000:.2f} MHz exceeds the "
            "655.35 MHz DTD limit — drop this mode or lower its refresh rate"
        )
    return timing


def _cta_extension(timings: Sequence[Timing], size_mm: Tuple[int, int]) -> bytes:
    """CTA-861 extension block carrying up to six additional DTDs."""
    block = bytearray(128)
    block[0] = 0x02
    block[1] = 0x03  # CTA-861 rev 3
    block[2] = 4  # DTDs start right after the header (no data blocks)
    offset = 4
    for timing in timings:
        block[offset:offset + 18] = _detailed_timing(timing, size_mm)
        offset += 18
    return _checksummed(block)


def generate_multi(modes: Sequence[Mode], name: str = VDD_MONITOR_NAME,
                   size_mm: Tuple[int, int] = (600, 340)) -> bytes:
    """Multi-mode EDID for a permanently provisioned VDD connector.

    The first mode is the preferred timing.  Raises ValueError for modes a
    DTD cannot represent — a provisioning tool should fail loudly, not
    quietly reshape the request.
    """
    if not modes:
        raise ValueError("at least one mode is required")
    timings = [_strict_timing(m) for m in modes]

    base_timings = timings[:_BASE_SLOTS]
    ext_timings = timings[_BASE_SLOTS:]
    ext_blocks = [ext_timings[i:i + _EXT_SLOTS] for i in range(0, len(ext_timings), _EXT_SLOTS)]

    block = bytearray(128)
    block[0:8] = _HEADER
    block[8:10] = _manufacturer_id("ZNH")
    block[10:12] = struct.pack("<H", 0x0002)  # product code: provisioned VDD
    block[12:16] = struct.pack("<I", 0)
    block[16] = 1
    block[17] = 36  # 1990 + 36 = 2026
    block[18] = 1  # EDID 1.4
    block[19] = 4
    block[20] = 0xA5  # digital input, 8 bpc, DisplayPort
    block[21] = size_mm[0] // 10  # cm
    block[22] = size_mm[1] // 10
    block[23] = 120  # gamma 2.2
    block[24] = 0x06  # sRGB default, preferred timing is native
    block[25:35] = bytes((0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54))
    block[38:54] = b"\x01\x01" * 8

    descriptors = [_detailed_timing(t, size_mm) for t in base_timings]
    descriptors.append(_RANGE_LIMITS_DESCRIPTOR)
    descriptors.append(_display_name_descriptor(name))
    while len(descriptors) < 4:
        descriptors.append(_DUMMY_DESCRIPTOR)
    offset = 54
    for desc in descriptors[:4]:
        block[offset:offset + 18] = desc
        offset += 18

    block[126] = len(ext_blocks)
    edid = _checksummed(block)
    for ext in ext_blocks:
        edid += _cta_extension(ext, size_mm)
    return edid


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
