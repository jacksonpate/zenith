"""KDE backend against the real silverblue fixture (3 outputs, 1 VDD)."""

import json

from conftest import FakeRunner

from zenith_display.layouts.kscreen import KScreenBackend
from zenith_display.modes import Mode


def _backend(fixture_text):
    return KScreenBackend(FakeRunner({"kscreen-doctor": fixture_text("kscreen_silverblue.json")}))


def _backend_after_headless(fixture_text):
    """The fixture as the compositor reports it *during* a headless session:
    every physical output dark, only the VDD lit."""
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    for out in doc["outputs"]:
        out["enabled"] = out["name"] == "DP-1"
    return KScreenBackend(FakeRunner({"kscreen-doctor": json.dumps(doc)}))


# What the user was actually looking at before any of this started.
USER_LAYOUT = {
    "outputs": [
        {"name": "eDP-1", "enabled": True, "mode": "2560x1600@165",
         "x": -1707, "y": 0, "scale": 1.5, "priority": 2},
        {"name": "HDMI-A-1", "enabled": True, "mode": "1920x1080@120",
         "x": 16, "y": 0, "scale": 1.0, "priority": 1},
    ]
}


def test_outputs_parse_real_fleet_data(fixture_text):
    outs = _backend(fixture_text).outputs()
    names = {o.name for o in outs}
    assert {"eDP-1", "HDMI-A-1", "DP-1"} <= names
    edp = next(o for o in outs if o.name == "eDP-1")
    assert edp.connected and not edp.enabled
    assert edp.scale == 1.5


def test_snapshot_covers_every_connected_output(fixture_text):
    payload = _backend(fixture_text).snapshot()
    assert len(payload["outputs"]) == 3
    assert all("enabled" in o and "x" in o for o in payload["outputs"])


def test_headless_disables_everything_but_the_vdd(fixture_text):
    backend = _backend(fixture_text)
    backend.apply_headless("DP-1", Mode(2420, 1668, 120))
    applied = backend.runner.trace[-1]
    joined = " ".join(applied)
    assert "output.DP-1.enable" in joined
    assert "output.DP-1.priority.1" in joined
    assert "output.HDMI-A-1.disable" in joined
    assert "output.DP-1.disable" not in joined
    # eDP-1 was already disabled — no redundant command for it.
    assert "output.eDP-1.disable" not in joined


def test_dual_places_vdd_past_the_rightmost_output(fixture_text):
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120))
    joined = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.enable" in joined
    # The VDD lands after every lit monitor — never primary (headless leaves it
    # at priority 1) and never on top of one. HDMI-A-1 is lit at x=16, 1920 wide.
    assert "output.DP-1.priority.2" in joined
    assert "output.DP-1.priority.1" not in joined
    assert "output.DP-1.position.1936,0" in joined
    assert "disable" not in joined


def test_dual_relights_the_monitors_headless_turned_off(fixture_text):
    """Switching headless -> dual must put the physical outputs back.

    Dual used to only ever *add* the VDD, so coming from headless (every
    monitor dark) it produced exactly one lit display — headless again.
    """
    backend = _backend_after_headless(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), USER_LAYOUT)
    joined = " ".join(backend.runner.trace[-1])
    assert "output.eDP-1.enable" in joined
    assert "output.HDMI-A-1.enable" in joined
    assert "output.DP-1.enable" in joined
    # ...back at the mode and zoom they had, not whatever the compositor last held
    assert "output.eDP-1.mode.2560x1600@165" in joined
    assert "output.eDP-1.scale.1.5" in joined
    # ...and translated into positive space. The user's layout put eDP-1 at
    # x=-1707, which is a perfectly ordinary thing to own — a monitor to the left
    # of the origin — but KDE refuses to enable an output at a negative
    # coordinate and refuses the whole config with it. Sliding every screen by
    # the same amount preserves the arrangement, which is the part they chose.
    assert "output.eDP-1.position.0,0" in joined
    assert "output.HDMI-A-1.position.1723,0" in joined   # 16 + 1707
    assert "position.-" not in joined, f"a negative coordinate sinks the apply: {joined}"


def test_a_negative_coordinate_is_never_emitted(fixture_text):
    """KDE: "Position of enabled output DP-1 is negative (-16, 1,080)" — printed on
    stdout, with an exit status of zero, so for a long time nothing noticed.

    The refusal is total, and the virtual display is enabled in the same call, so
    this did not produce a slightly-wrong desk. It produced no streaming display
    at all, and a plain mirrored desktop with no error anywhere the user could see.
    """
    backend = _backend(fixture_text)
    # The VDD remembered as sitting slightly left of a monitor now at x=0.
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None,
                       placement={"anchor": "HDMI-A-1", "dx": -16, "dy": 1080,
                                  "scale": 1.25})
    joined = " ".join(backend.runner.trace[-1])
    assert "position.-" not in joined, f"emitted a negative coordinate: {joined}"


def test_kscreen_failing_with_exit_status_zero_is_still_a_failure(fixture_text):
    """kscreen-doctor prints "applying config failed!" on stdout and exits 0.

    Believing the exit status meant Zenith logged "dual active" at the same moment
    KDE threw the layout away. Every rollback downstream depends on this raising.
    """
    import pytest

    from zenith_display.runner import Result

    rejection = Result(
        argv=[], returncode=0,   # <- zero. That is the entire problem.
        stdout="Enabling output 2\n"
               "applying config failed! Position of enabled output DP-1 is negative (-16, 1,080)")
    backend = KScreenBackend(FakeRunner({
        ("kscreen-doctor", "output.DP-1.enable"): rejection,
    }))
    with pytest.raises(RuntimeError, match="rejected"):
        backend._apply_args(["output.DP-1.enable"])


def test_dual_without_a_baseline_still_relights_connected_monitors(fixture_text):
    """No snapshot to lean on is not a licence to leave the user in the dark."""
    backend = _backend_after_headless(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120))
    joined = " ".join(backend.runner.trace[-1])
    assert "output.eDP-1.enable" in joined
    assert "output.HDMI-A-1.enable" in joined
    assert "output.DP-1.enable" in joined


def test_dual_puts_the_vdd_past_the_baseline_not_on_top_of_it(fixture_text):
    """rightmost_edge() only counts *enabled* outputs. From a headless state
    that is nobody, so the VDD would land at x=0, on top of the monitors."""
    backend = _backend_after_headless(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), USER_LAYOUT)
    joined = " ".join(backend.runner.trace[-1])
    # Right of both relit monitors, in the same positive space they were slid into:
    # eDP-1 (1706 logical) at 0, HDMI-A-1 (1920) at 1723, so the desk ends at 3643.
    assert "output.DP-1.position.3643,0" in joined


def test_relight_restores_a_plain_desktop(fixture_text):
    """The last resort when the snapshot is gone or was poison: monitors back
    on, and the orphaned VDD off — nothing else will ever take it down."""
    backend = _backend_after_headless(fixture_text)
    backend.relight({"DP-1"})
    joined = " ".join(backend.runner.trace[-1])
    assert "output.eDP-1.enable" in joined
    assert "output.HDMI-A-1.enable" in joined
    assert "output.DP-1.disable" in joined
    assert "output.DP-1.enable" not in joined  # ("eDP-1" contains "DP-1" — match on the full arg)


def test_relight_never_blanks_the_only_display(fixture_text):
    """A box whose only output IS the VDD must be left alone, not blanked."""
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    doc["outputs"] = [o for o in doc["outputs"] if o["name"] == "DP-1"]
    backend = KScreenBackend(FakeRunner({"kscreen-doctor": json.dumps(doc)}))
    backend.relight({"DP-1"})
    assert not any(t[0] == "kscreen-doctor" and len(t) > 2 for t in backend.runner.trace)


def test_restore_replays_snapshot(fixture_text):
    backend = _backend(fixture_text)
    payload = json.loads(json.dumps(backend.snapshot()))  # simulate disk roundtrip
    backend.restore(payload)
    joined = " ".join(backend.runner.trace[-1])
    assert "output.HDMI-A-1.enable" in joined
    assert "output.eDP-1.disable" in joined
