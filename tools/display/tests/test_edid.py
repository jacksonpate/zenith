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
