"""KDE backend against the real silverblue fixture (3 outputs, 1 VDD)."""

import json

from conftest import FakeRunner

from zenith_display.layouts.kscreen import KScreenBackend
from zenith_display.modes import Mode


def _backend(fixture_text):
    return KScreenBackend(FakeRunner({"kscreen-doctor": fixture_text("kscreen_silverblue.json")}))


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
    assert "output.DP-1.priority.2" in joined
    assert "position." in joined
    assert "disable" not in joined


def test_restore_replays_snapshot(fixture_text):
    backend = _backend(fixture_text)
    payload = json.loads(json.dumps(backend.snapshot()))  # simulate disk roundtrip
    backend.restore(payload)
    joined = " ".join(backend.runner.trace[-1])
    assert "output.HDMI-A-1.enable" in joined
    assert "output.eDP-1.disable" in joined
