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
        "swaymsg -- output HEADLESS-2 enable mode --custom 2420x1668@60Hz position 0 0" in j
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


# --- dual entered from a headless session -----------------------------------
#
# sway reports a disabled output as `current_mode: null`, `rect` all zeroes and
# `scale: null` — its mode list is the only thing left to light it from. That is
# what made the fallback a silent no-op: every target arrived with width=0.

_SWAY_AFTER_HEADLESS = json.dumps([
    {
        "name": "DP-1", "active": False, "current_mode": None,
        "rect": {"x": 0, "y": 0, "width": 0, "height": 0}, "scale": None,
        "modes": [{"width": 2560, "height": 1440, "refresh": 60000}],
    },
    {
        "name": "DP-2", "active": False, "current_mode": None,
        "rect": {"x": 0, "y": 0, "width": 0, "height": 0}, "scale": None,
        "modes": [{"width": 1920, "height": 1080, "refresh": 60000}],
    },
    {
        "name": "HEADLESS-1", "active": True,
        "current_mode": {"width": 2420, "height": 1668, "refresh": 120000},
        "rect": {"x": 0, "y": 0, "width": 2420, "height": 1668}, "scale": 1.0,
        "modes": [{"width": 2420, "height": 1668, "refresh": 120000}],
    },
])

_USER_DESK = {"outputs": [
    {"name": "DP-1", "enabled": True, "width": 2560, "height": 1440, "refresh": 60,
     "x": 0, "y": 0, "scale": 1.0},
    {"name": "DP-2", "enabled": True, "width": 1920, "height": 1080, "refresh": 60,
     "x": 2560, "y": 0, "scale": 1.0},
]}


def _sway_after_headless():
    return WlrBackend(FakeRunner({"swaymsg": _SWAY_AFTER_HEADLESS}))


def _cmds(backend):
    return [" ".join(t) for t in backend.runner.trace]


def test_sway_dual_relights_the_monitors_headless_turned_off():
    backend = _sway_after_headless()
    backend.apply_dual("HEADLESS-1", Mode(2420, 1668, 120), _USER_DESK)
    cmds = _cmds(backend)
    assert any("output DP-1 enable" in c and "2560x1440@60Hz" in c for c in cmds)
    assert any("output DP-2 enable" in c and "position 2560 0" in c for c in cmds)
    assert any("output HEADLESS-1 enable" in c and "position 4480 0" in c for c in cmds)


def test_sway_dual_without_a_baseline_still_relights_them():
    """The documented fallback was dead code on wlr: a disabled output reports
    no mode, so `if out.enabled and out.width` skipped every monitor and left
    the desk exactly as dark as before the fix."""
    backend = _sway_after_headless()
    backend.apply_dual("HEADLESS-1", Mode(2420, 1668, 120))
    cmds = _cmds(backend)
    assert any("output DP-1 enable" in c and "2560x1440" in c for c in cmds)
    assert any("output DP-2 enable" in c and "1920x1080" in c for c in cmds)
    # ...and the VDD must land past them, not on top of them at 0.
    assert any("output HEADLESS-1 enable" in c and "position 4480 0" in c for c in cmds)


def test_sway_dual_applies_the_baselines_scale():
    """rightmost_edge() divides by scale, so an unapplied scale silently puts
    the VDD on top of a real monitor."""
    baseline = {"outputs": [
        {"name": "DP-1", "enabled": True, "width": 2560, "height": 1440, "refresh": 60,
         "x": 0, "y": 0, "scale": 2.0},
    ]}
    backend = _sway_after_headless()
    backend.apply_dual("HEADLESS-1", Mode(2420, 1668, 120), baseline)
    cmds = _cmds(backend)
    assert any("output DP-1 enable" in c and "scale 2.0" in c for c in cmds)
    assert any("output HEADLESS-1 enable" in c and "position 1280 0" in c for c in cmds)
