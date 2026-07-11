"""Restore must never be the reason a machine goes dark.

A layout outlives the hardware it describes. Take the laptop off the desk and
the remembered desktop — "eDP-1 off, HDMI-A-1 on" — no longer names anything
this machine has except a panel it was told to keep dark. Replaying that
literally is a black screen with no way back.
"""

import json

import pytest
from conftest import FakeRunner

from zenith_display import cli
from zenith_display.detect import Connector, Environment


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def _env():
    return Environment(
        session_type="wayland", desktop="kde", distro="fedora", tools={"kscreen-doctor": True},
        connectors=[Connector(sysfs="/sys/class/drm/card1-DP-1", name="DP-1",
                              status="connected", enabled=False, is_vdd=True)],
        is_root=False, has_passwordless_sudo=False,
    )


# The desk it remembers: laptop panel deliberately dark, HDMI monitor doing the work.
REMEMBERED_DESK = {
    "backend": "kscreen",
    "provider": "",
    "vdd_output": "DP-1",
    "payload": {"outputs": [
        {"name": "eDP-1", "enabled": False},
        {"name": "HDMI-A-1", "enabled": True, "mode": "1920x1080@120",
         "x": 0, "y": 0, "primary": True},
    ]},
}


def _on_the_road(fixture_text):
    """The same laptop, away from the desk: no HDMI, and the panel is off."""
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    doc["outputs"] = [o for o in doc["outputs"] if o["name"] in ("eDP-1", "DP-1")]
    for out in doc["outputs"]:
        out["enabled"] = False
    return FakeRunner({"kscreen-doctor": json.dumps(doc)})


def test_restore_lights_what_the_machine_has_when_the_remembered_monitor_is_gone(fixture_text):
    runner = _on_the_road(fixture_text)
    cli._restore_from(REMEMBERED_DESK, _env(), runner)
    applied = " ".join(runner.trace[-1])
    # The laptop panel is all this machine has. Honouring "keep it dark" here
    # would leave the user with nothing at all.
    assert "output.eDP-1.enable" in applied
    assert "output.eDP-1.disable" not in applied


def test_restore_still_honours_a_dark_panel_when_the_monitor_is_there(fixture_text):
    """...and the moment the HDMI is back, the panel goes dark again as asked."""
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    for out in doc["outputs"]:
        out["enabled"] = out["name"] == "DP-1"  # mid-session: only the VDD is lit
    runner = FakeRunner({"kscreen-doctor": json.dumps(doc)})
    cli._restore_from(REMEMBERED_DESK, _env(), runner)
    applied = " ".join(runner.trace[-1])
    assert "output.HDMI-A-1.enable" in applied
    assert "output.eDP-1.disable" in applied  # the user's choice, respected
    assert "output.DP-1.disable" in applied   # and the VDD goes away
