"""EDID generation must produce blocks the kernel will actually accept."""

import pytest

from zenith_display import VDD_MONITOR_NAME, edid
from zenith_display.modes import Mode

CASES = [
    Mode(1920, 1080, 60),
    Mode(2420, 1668, 120),  # iPad Pro 11" over Moonlight
    Mode(2266, 1488, 60),   # iPad mini
    Mode(1280, 800, 90),
]


@pytest.mark.parametrize("mode", CASES, ids=str)
def test_block_is_structurally_valid(mode):
    block = edid.generate(mode)
    assert len(block) == 128
    assert block[:8] == b"\x00\xff\xff\xff\xff\xff\xff\x00"
    assert sum(block) % 256 == 0  # checksum
    assert block[126] == 0  # no extensions


@pytest.mark.parametrize("mode", CASES, ids=str)
def test_dtd_roundtrips_the_requested_mode(mode):
    block = edid.generate(mode)
    parsed = edid.parse_dtd_mode(block)
    assert parsed is not None
    assert (parsed.width, parsed.height) == (mode.width, mode.height)
    assert abs(parsed.refresh - mode.refresh) <= 1  # clock quantization


def test_4k120_degrades_refresh_within_dtd_clock_ceiling():
    # 4K@120 needs ~1.07 GHz — beyond the 655.35 MHz EDID DTD limit. The
    # generator must fall back to the fastest refresh that fits (4K@60).
    block = edid.generate(Mode(3840, 2160, 120))
    parsed = edid.parse_dtd_mode(block)
    assert (parsed.width, parsed.height) == (3840, 2160)
    assert parsed.refresh == 60
    assert sum(block) % 256 == 0


def test_monitor_name_embedded_and_parsed():
    block = edid.generate(Mode(1920, 1080, 60))
    assert edid.monitor_name(block) == VDD_MONITOR_NAME


def test_monitor_name_rejects_garbage():
    assert edid.monitor_name(b"") is None
    assert edid.monitor_name(b"\x00" * 128) is None


def test_dimensions_beyond_dtd_limit_raise_cleanly():
    with pytest.raises(ValueError, match="lower the client resolution"):
        edid.generate(Mode(5120, 1440, 60))
    with pytest.raises(ValueError, match="lower the client resolution"):
        edid.generate(Mode(1440, 5120, 60))


def test_multi_mode_edid_structure():
    modes = [
        Mode(1920, 1080, 60), Mode(2560, 1440, 60), Mode(2420, 1668, 120),
        Mode(2752, 2064, 60), Mode(1280, 720, 60),
    ]
    blob = edid.generate_multi(modes)
    # base + one CTA extension (2 base DTDs, 3 overflow into the extension)
    assert len(blob) == 256
    assert blob[126] == 1  # extension count
    assert blob[128] == 0x02 and blob[129] == 0x03  # CTA-861 rev 3
    for i in range(len(blob) // 128):  # every block checksums to zero
        assert sum(blob[128 * i:128 * (i + 1)]) % 256 == 0
    assert edid.monitor_name(blob) == VDD_MONITOR_NAME
    parsed = edid.parse_dtd_mode(blob)
    assert (parsed.width, parsed.height, parsed.refresh) == (1920, 1080, 60)


def test_multi_mode_rejects_undtdable_mode():
    with pytest.raises(ValueError, match="655.35 MHz"):
        edid.generate_multi([Mode(1920, 1080, 60), Mode(3840, 2160, 120)])


def test_dtd_uses_cvt_rb_sync_polarity():
    blob = edid.generate(Mode(1920, 1080, 60))
    assert blob[54 + 17] == 0x1A  # digital separate sync, +hsync -vsync


def test_the_edid_carries_a_serial_number():
    """Without one, KWin cannot tell this display from any other and falls back to
    identifying it by the port it is plugged into — so it announces itself as
    "DP-1-Zenith-VDD", and renames itself whenever it borrows a different port.

        make: 'ZNH'   model: 'DP-1-Zenith-VDD'      <- no serial
        make: 'ZNH'   model: 'Zenith-VDD'           <- serial

    Stable, because a serial that changed every session would look like a
    different monitor each time, and anything remembering the display by identity
    would have nothing to hold on to.
    """
    import struct

    first = edid.generate(Mode(1920, 1080, 60))
    again = edid.generate(Mode(2420, 1668, 120))   # different mode, same display
    serial = struct.unpack("<I", first[12:16])[0]

    assert serial != 0, "a zero serial is what KWin treats as no serial at all"
    assert struct.unpack("<I", again[12:16])[0] == serial, "the display must keep its identity"


def test_two_displays_are_not_the_same_display():
    import struct

    a = edid.generate(Mode(1920, 1080, 60), name="Zenith-VDD")
    b = edid.generate(Mode(1920, 1080, 60), name="Zenith-VDD-2")
    assert struct.unpack("<I", a[12:16]) != struct.unpack("<I", b[12:16])
