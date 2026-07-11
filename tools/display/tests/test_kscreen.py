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
    # The VDD lands after every real monitor — never primary (headless leaves it
    # at priority 1) and never sharing a slot with one of them.
    assert "output.DP-1.priority.3" in joined
    assert "output.DP-1.priority.1" not in joined
    assert "position." in joined
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
    # ...and back where they were, not at whatever the compositor last held.
    assert "output.eDP-1.mode.2560x1600@165" in joined
    assert "output.eDP-1.position.-1707,0" in joined
    assert "output.eDP-1.scale.1.5" in joined


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
    assert "output.DP-1.position.1936,0" in joined  # 16 + 1920 logical px


def test_restore_replays_snapshot(fixture_text):
    backend = _backend(fixture_text)
    payload = json.loads(json.dumps(backend.snapshot()))  # simulate disk roundtrip
    backend.restore(payload)
    joined = " ".join(backend.runner.trace[-1])
    assert "output.HDMI-A-1.enable" in joined
    assert "output.eDP-1.disable" in joined
