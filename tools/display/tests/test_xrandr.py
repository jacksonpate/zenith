"""X11 backend (the Cinnamon/Mint path) against a dual-monitor fixture."""

from conftest import FakeRunner

from zenith_display.layouts.xrandr import XrandrBackend
from zenith_display.modes import Mode


def _backend(fixture_text):
    return XrandrBackend(FakeRunner({"xrandr": fixture_text("xrandr_mint_dual.txt")}))


def test_parse_finds_all_outputs(fixture_text):
    outs = _backend(fixture_text).outputs()
    assert [o.name for o in outs] == ["eDP-1", "HDMI-1", "DP-1", "DP-2", "HDMI-2"]
    edp = outs[0]
    assert edp.enabled and edp.primary and edp.width == 1920 and edp.x == 0
    hdmi = outs[1]
    assert hdmi.enabled and hdmi.x == 1920
    assert not outs[2].connected


def test_parse_reads_current_refresh(fixture_text):
    outs = _backend(fixture_text).outputs()
    assert round(outs[0].refresh) == 60
    assert "1920x1080@60" in outs[0].modes


def test_headless_injects_modeline_and_kills_the_rest(fixture_text):
    backend = _backend(fixture_text)
    backend.apply_headless("DP-1", Mode(2266, 1488, 60))
    trace = backend.runner.trace
    newmode = next(t for t in trace if "--newmode" in t)
    assert "zvdd_2266x1488_60" in newmode
    addmode = next(t for t in trace if "--addmode" in t)
    assert addmode[-2:] == ["DP-1", "zvdd_2266x1488_60"]
    final = trace[-1]
    joined = " ".join(final)
    assert "--output DP-1 --mode zvdd_2266x1488_60" in joined
    assert "--output eDP-1 --off" in joined
    assert "--output HDMI-1 --off" in joined


def test_dual_positions_vdd_at_right_edge(fixture_text):
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2266, 1488, 60))
    joined = " ".join(backend.runner.trace[-1])
    assert "--pos 3840x0" in joined  # 1920 (eDP) + 1920 (HDMI)
    assert "--off" not in joined


def test_restore_reenables_and_repositions(fixture_text):
    backend = _backend(fixture_text)
    payload = backend.snapshot()
    backend.restore(payload)
    joined = " ".join(backend.runner.trace[-1])
    assert "--output eDP-1 --mode 1920x1080 --pos 0x0" in joined
    assert "--primary" in joined
    assert "--output HDMI-1 --mode 1920x1080 --pos 1920x0" in joined
