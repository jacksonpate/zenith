"""wlroots backend: both transports (sway IPC preferred, wlr-randr fallback)."""

import json

from conftest import FakeRunner

from zenith_display.layouts.wlr import WlrBackend
from zenith_display.modes import Mode
from zenith_display.runner import Result

_WLR_RANDR_FIXTURE = json.dumps([
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

_NO_SWAY = {("swaymsg", "-t", "get_version"): Result(argv=[], returncode=1)}


def _wlr_randr_backend():
    responses = dict(_NO_SWAY)
    responses["wlr-randr"] = _WLR_RANDR_FIXTURE
    return WlrBackend(FakeRunner(responses))


def _sway_backend(fixture_text):
    return WlrBackend(FakeRunner({
        ("swaymsg", "-t", "get_outputs", "--raw"): fixture_text("sway_outputs.json"),
    }))


# -- wlr-randr transport ----------------------------------------------------

def test_wlr_randr_parse():
    outs = _wlr_randr_backend().outputs()
    assert outs[0].name == "eDP-1" and outs[0].enabled and outs[0].width == 1920
    assert outs[1].name == "HEADLESS-1" and not outs[1].enabled


def test_wlr_randr_headless_custom_mode_and_offs():
    backend = _wlr_randr_backend()
    backend.apply_headless("HEADLESS-1", Mode(2420, 1668, 120))
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any("--custom-mode 2420x1668@120Hz" in j and "HEADLESS-1" in j for j in joined)
    assert any("--output eDP-1 --off" in j for j in joined)


def test_wlr_randr_dual_positions_past_edge():
    backend = _wlr_randr_backend()
    backend.apply_dual("HEADLESS-1", Mode(1920, 1080, 60))
    joined = " ".join(backend.runner.trace[-1])
    assert "--pos 1920,0" in joined


# -- sway transport -----------------------------------------------------------

def test_sway_parse_millihertz_and_active(fixture_text):
    outs = _sway_backend(fixture_text).outputs()
    assert outs[0].name == "HEADLESS-1" and outs[0].enabled
    assert outs[0].width == 1280 and outs[0].refresh == 0.0
    assert not outs[1].enabled


def test_sway_headless_uses_swaymsg_custom_mode(fixture_text):
    backend = _sway_backend(fixture_text)
    backend.apply_headless("HEADLESS-2", Mode(2420, 1668, 60))
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any(
        "swaymsg output HEADLESS-2 enable mode --custom 2420x1668@60Hz position 0 0" in j
        for j in joined
    )
    assert any("swaymsg output HEADLESS-1 disable" in j for j in joined)


def test_sway_restore_reenables_from_snapshot(fixture_text):
    backend = _sway_backend(fixture_text)
    payload = backend.snapshot()
    backend.restore(payload)
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any("output HEADLESS-1 enable mode --custom 1280x720@60Hz position 0 0" in j for j in joined)
    assert any("output HEADLESS-2 disable" in j for j in joined)
