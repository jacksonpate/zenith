"""Snapshot persistence: roundtrip, atomicity, crash-recovery semantics."""

import os

from zenith_display import snapshot


def _environ(tmp_path):
    return {"XDG_STATE_HOME": str(tmp_path)}


def test_roundtrip(tmp_path):
    env = _environ(tmp_path)
    payload = {"outputs": [{"name": "eDP-1", "enabled": True}]}
    snapshot.save("kscreen", payload, provider="evdi", vdd_output="DVI-I-1", environ=env)
    doc = snapshot.load(environ=env)
    assert doc["backend"] == "kscreen"
    assert doc["provider"] == "evdi"
    assert doc["vdd_output"] == "DVI-I-1"
    assert doc["payload"] == payload


def test_clear_removes_state(tmp_path):
    env = _environ(tmp_path)
    snapshot.save("xrandr", {}, environ=env)
    snapshot.clear(environ=env)
    assert snapshot.load(environ=env) is None


def test_load_survives_corrupt_file(tmp_path):
    env = _environ(tmp_path)
    path = os.path.join(snapshot.state_dir(environ=env), "snapshot.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert snapshot.load(environ=env) is None


def test_a_headless_layout_is_never_a_user_layout():
    """The bug this guards: a snapshot taken while a previous session was still
    torn down records every monitor dark, and `restore` then replays *that* —
    so the monitors never come back."""
    captured_mid_teardown = {
        "outputs": [
            {"name": "eDP-1", "enabled": False},
            {"name": "HDMI-A-1", "enabled": False},
            {"name": "DP-1", "enabled": True},  # the VDD, still lit
        ]
    }
    assert not snapshot.is_user_layout(captured_mid_teardown, vdd_output="DP-1")


def test_one_lit_monitor_is_enough_to_be_a_user_layout():
    payload = {
        "outputs": [
            {"name": "eDP-1", "enabled": True},
            {"name": "HDMI-A-1", "enabled": False},  # lid closed, say
        ]
    }
    assert snapshot.is_user_layout(payload, vdd_output="DP-1")


def test_save_is_atomic_no_tmp_left_behind(tmp_path):
    env = _environ(tmp_path)
    snapshot.save("wlr", {"outputs": []}, environ=env)
    entries = os.listdir(snapshot.state_dir(environ=env))
    assert "snapshot.json" in entries
    assert not any(e.endswith(".tmp") for e in entries)
