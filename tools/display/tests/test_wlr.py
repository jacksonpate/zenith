"""wlroots backend parsing + command synthesis."""

import json

from conftest import FakeRunner

from zenith_display.layouts.wlr import WlrBackend
from zenith_display.modes import Mode

_FIXTURE = json.dumps([
    {
        "name": "eDP-1",
        "enabled": True,
        "position": {"x": 0, "y": 0},
        "scale": 1.0,
        "modes": [
            {"width": 1920, "height": 1080, "refresh": 60.0, "current": True},
            {"width": 1280, "height": 720, "refresh": 60.0, "current": False},
        ],
    },
    {
        "name": "HEADLESS-1",
        "enabled": False,
        "position": {"x": 0, "y": 0},
        "scale": 1.0,
        "modes": [],
    },
])


def _backend():
    return WlrBackend(FakeRunner({"wlr-randr": _FIXTURE}))


def test_parse():
    outs = _backend().outputs()
    assert outs[0].name == "eDP-1" and outs[0].enabled and outs[0].width == 1920
    assert outs[1].name == "HEADLESS-1" and not outs[1].enabled


def test_headless_custom_mode_and_offs():
    backend = _backend()
    backend.apply_headless("HEADLESS-1", Mode(2420, 1668, 120))
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any("--custom-mode 2420x1668@120Hz" in j and "HEADLESS-1" in j for j in joined)
    assert any("--output eDP-1 --off" in j for j in joined)


def test_dual_positions_past_edge():
    backend = _backend()
    backend.apply_dual("HEADLESS-1", Mode(1920, 1080, 60))
    joined = " ".join(backend.runner.trace[-1])
    assert "--pos 1920,0" in joined
