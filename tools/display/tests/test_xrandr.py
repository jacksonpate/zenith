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
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any("--output eDP-1 --mode 1920x1080 --pos 0x0" in j and "--primary" in j for j in joined)
    assert any("--output HDMI-1 --mode 1920x1080 --pos 1920x0" in j for j in joined)


_ROTATED_FIXTURE = """\
Screen 0: minimum 320 x 200, current 3000 x 1920, maximum 16384 x 16384
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.01*+  59.97
DP-2 connected 1080x1920+1920+0 left (normal left inverted right x axis y axis) 527mm x 296mm
   1920x1080     60.00*+  50.00
"""


def _rotated_backend():
    return XrandrBackend(FakeRunner({"xrandr": _ROTATED_FIXTURE}))


def test_rotated_output_snapshot_keeps_native_mode_and_rotation():
    payload = _rotated_backend().snapshot()
    dp2 = next(o for o in payload["outputs"] if o["name"] == "DP-2")
    assert dp2["mode"] == "1920x1080"  # native mode, not the swapped geometry
    assert dp2["rotation"] == "left"


def test_restore_reapplies_rotation_per_output():
    backend = _rotated_backend()
    backend.restore(backend.snapshot())
    joined = [" ".join(t) for t in backend.runner.trace]
    assert any("--output DP-2 --mode 1920x1080" in j and "--rotate left" in j for j in joined)
    # per-output replay: eDP-1 and DP-2 each get their own xrandr call
    assert sum(1 for j in joined if j.startswith("xrandr --output")) >= 2


def test_restore_continues_past_one_failed_output():
    from zenith_display.runner import Result

    backend = _rotated_backend()
    payload = backend.snapshot()
    # Make the first output's restore fail; the second must still be attempted.
    failing = dict(backend.runner.responses)
    backend.runner.responses = failing
    calls = {"n": 0}
    orig = backend.runner.run

    def flaky(argv, timeout=15.0, check=False, mutating=True):
        if argv[0] == "xrandr" and "--output" in argv:
            calls["n"] += 1
            if calls["n"] == 1:
                backend.runner.trace.append(list(argv))
                return Result(argv=argv, returncode=1, stderr="cannot find mode")
        return orig(argv, timeout=timeout, check=check, mutating=mutating)

    backend.runner.run = flaky
    try:
        backend.restore(payload)
        raise AssertionError("expected RuntimeError for incomplete restore")
    except RuntimeError as exc:
        assert "restore incomplete" in str(exc)
    assert calls["n"] >= 2  # second output was still attempted


# --- dual entered from a headless session -----------------------------------
#
# The outputs are off, so X reports no geometry for them at all — only their
# mode lists survive. Everything below starts from that state, because that is
# where the bug lived: dual is routinely entered straight out of headless.

_AFTER_HEADLESS = """\
Screen 0: minimum 320 x 200, current 2420 x 1668, maximum 16384 x 16384
eDP-1 connected (normal left inverted right x axis y axis) 309mm x 174mm
   1920x1080     60.00 +
   1280x720      59.86
HDMI-1 connected (normal left inverted right x axis y axis) 530mm x 300mm
   2560x1440     59.95 +
DP-1 connected primary 2420x1668+0+0 (normal left inverted right x axis y axis) 0mm x 0mm
   2420x1668    120.00*+
"""

_USER_LAYOUT = {"outputs": [
    {"name": "eDP-1", "enabled": True, "mode": "1920x1080", "refresh": 60,
     "rotation": "normal", "x": 0, "y": 0, "primary": True},
    {"name": "HDMI-1", "enabled": True, "mode": "2560x1440", "refresh": 60,
     "rotation": "normal", "x": 1920, "y": 0, "primary": False},
]}


def _after_headless():
    return XrandrBackend(FakeRunner({"xrandr": _AFTER_HEADLESS}))


def test_dual_relights_the_monitors_headless_turned_off():
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), _USER_LAYOUT)
    argv = " ".join(backend.runner.trace[-1])
    assert "--output eDP-1 --mode 1920x1080 --pos 0x0" in argv
    assert "--output HDMI-1 --mode 2560x1440 --pos 1920x0" in argv
    assert "--output DP-1 --mode zvdd_2420x1668_120 --pos 4480x0" in argv


def test_dual_gives_the_primary_back_to_the_user():
    """apply_headless hands --primary to the VDD; dual must take it back, or
    menus and new windows keep opening on the streamed head."""
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), _USER_LAYOUT)
    argv = " ".join(backend.runner.trace[-1])
    assert "--output eDP-1 --mode 1920x1080 --pos 0x0 --rate 60 --primary" in argv


def test_dual_without_a_baseline_still_relights_them():
    """The fallback used to be a no-op: X reports width=0 for an output that is
    off, so every monitor was skipped and dual came out identical to headless."""
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120))
    argv = " ".join(backend.runner.trace[-1])
    assert "--output eDP-1 --mode 1920x1080" in argv   # its own preferred mode
    assert "--output HDMI-1 --mode 2560x1440" in argv
    assert "--off" not in argv


def test_dual_puts_a_rotated_monitor_back_on_its_side():
    """An output that is off has no CRTC, so xrandr silently resets it to
    RR_Rotate_0 unless told otherwise — the monitor came back landscape."""
    baseline = {"outputs": [
        dict(_USER_LAYOUT["outputs"][0]),
        {"name": "HDMI-1", "enabled": True, "mode": "2560x1440", "refresh": 60,
         "rotation": "left", "x": 1920, "y": 0, "primary": False},
    ]}
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), baseline)
    argv = " ".join(backend.runner.trace[-1])
    assert "--rotate left" in argv
    # ...and on its side it is 1440 wide, not 2560. Getting this wrong leaves a
    # 1120px dead gap between the monitor and the VDD.
    assert "--output DP-1 --mode zvdd_2420x1668_120 --pos 3360x0" in argv


def test_dual_survives_a_monitor_that_was_unplugged_mid_session():
    """One stale name used to take down the whole apply (single atomic xrandr
    call, check=True) and strand the user in headless."""
    baseline = {"outputs": _USER_LAYOUT["outputs"] + [
        {"name": "DP-9", "enabled": True, "mode": "3840x2160", "refresh": 60, "x": 4480, "y": 0},
    ]}
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), baseline)
    argv = " ".join(backend.runner.trace[-1])
    assert "DP-9" not in argv
    assert "--output eDP-1 --mode 1920x1080" in argv


def test_dual_falls_back_when_the_baselines_mode_is_gone():
    """Swap the monitor and the saved mode no longer exists: `xrandr: cannot
    find mode` would fail the whole call."""
    baseline = {"outputs": [
        {"name": "eDP-1", "enabled": True, "mode": "3840x2160", "refresh": 60,
         "x": 0, "y": 0, "primary": True},
    ]}
    backend = _after_headless()
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), baseline)
    argv = " ".join(backend.runner.trace[-1])
    assert "3840x2160" not in argv
    assert "--output eDP-1 --mode 1920x1080" in argv
