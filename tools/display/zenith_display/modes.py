"""Client video mode handling and CVT-RB timing math.

The Moonlight client's requested geometry arrives in the environment that
Zenith passes to prep commands (``SUNSHINE_CLIENT_WIDTH`` / ``HEIGHT`` /
``FPS``).  Providers that fabricate a display from nothing (EVDI, debugfs
EDID override, xrandr ``--newmode``) need a complete modeline, which we derive
with the VESA CVT Reduced Blanking v1 algorithm — the standard for digital
panels and what real VDD drivers emit.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

# CVT-RB v1 constants (VESA CVT 1.2 spec, reduced blanking).
_RB_H_BLANK = 160
_RB_H_FRONT = 48
_RB_H_SYNC = 32
_RB_MIN_V_BLANK_US = 460.0
_RB_V_FRONT = 3
_RB_MIN_V_BACK = 6
_CLOCK_STEP_MHZ = 0.25


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    refresh: int

    def __str__(self) -> str:  # e.g. "2420x1668@120"
        return f"{self.width}x{self.height}@{self.refresh}"


@dataclass(frozen=True)
class Timing:
    """A full CVT-RB modeline for `Mode`."""

    mode: Mode
    pixel_clock_khz: int
    h_total: int
    h_front: int
    h_sync: int
    h_back: int
    v_total: int
    v_front: int
    v_sync: int
    v_back: int

    def xrandr_modeline(self) -> list:
        """Arguments for ``xrandr --newmode`` (name first)."""
        m = self.mode
        hs_start = m.width + self.h_front
        hs_end = hs_start + self.h_sync
        vs_start = m.height + self.v_front
        vs_end = vs_start + self.v_sync
        return [
            f"zvdd_{m.width}x{m.height}_{m.refresh}",
            f"{self.pixel_clock_khz / 1000:.2f}",
            str(m.width), str(hs_start), str(hs_end), str(self.h_total),
            str(m.height), str(vs_start), str(vs_end), str(self.v_total),
            "+hsync", "-vsync",
        ]


def client_mode(environ=os.environ, fallback: Mode = Mode(1920, 1080, 60)) -> Mode:
    """Resolve the mode the connecting client asked for."""

    def _int(name: str, default: int) -> int:
        raw = environ.get(name, "")
        try:
            value = int(float(raw))
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default

    return Mode(
        width=_int("SUNSHINE_CLIENT_WIDTH", fallback.width),
        height=_int("SUNSHINE_CLIENT_HEIGHT", fallback.height),
        refresh=_int("SUNSHINE_CLIENT_FPS", fallback.refresh),
    )


def _vsync_lines(width: int, height: int) -> int:
    """CVT aspect-ratio keyed vertical sync width."""
    ratios = {
        (4, 3): 4,
        (16, 9): 5,
        (16, 10): 6,
        (5, 4): 7,
        (15, 9): 7,
    }
    for (rw, rh), sync in ratios.items():
        if width * rh == height * rw:
            return sync
    return 10  # CVT default for non-standard aspect ratios


def cvt_rb(mode: Mode) -> Timing:
    """Compute a CVT Reduced Blanking timing for `mode`."""
    v_sync = _vsync_lines(mode.width, mode.height)

    h_period_est_us = ((1_000_000.0 / mode.refresh) - _RB_MIN_V_BLANK_US) / mode.height
    vbi_lines = int(_RB_MIN_V_BLANK_US / h_period_est_us) + 1
    min_vbi = _RB_V_FRONT + v_sync + _RB_MIN_V_BACK
    act_vbi = max(vbi_lines, min_vbi)

    v_total = act_vbi + mode.height
    h_total = mode.width + _RB_H_BLANK

    clock_mhz = mode.refresh * v_total * h_total / 1_000_000.0
    clock_mhz = math.floor(clock_mhz / _CLOCK_STEP_MHZ) * _CLOCK_STEP_MHZ

    return Timing(
        mode=mode,
        pixel_clock_khz=int(clock_mhz * 1000),
        h_total=h_total,
        h_front=_RB_H_FRONT,
        h_sync=_RB_H_SYNC,
        h_back=_RB_H_BLANK - _RB_H_FRONT - _RB_H_SYNC,
        v_total=v_total,
        v_front=_RB_V_FRONT,
        v_sync=v_sync,
        v_back=act_vbi - _RB_V_FRONT - v_sync,
    )
