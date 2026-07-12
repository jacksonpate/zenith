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
    assert not snapshot.is_user_layout(captured_mid_teardown, {"DP-1"})


def test_one_lit_monitor_is_enough_to_be_a_user_layout():
    payload = {
        "outputs": [
            {"name": "eDP-1", "enabled": True},
            {"name": "HDMI-A-1", "enabled": False},  # lid closed, say
        ]
    }
    assert snapshot.is_user_layout(payload, {"DP-1"})


def test_a_poisoned_snapshot_on_disk_is_discarded_not_replayed(tmp_path):
    """Self-heal for installs upgrading with a bad file already written.

    Older Zenith could persist a mid-teardown capture — every monitor dark — as
    the layout to restore *to*.  Loading one must drop it, not hand it to
    restore, or the user's monitors never come back.
    """
    env = _environ(tmp_path)
    snapshot.save(
        "kscreen",
        {"outputs": [
            {"name": "eDP-1", "enabled": False},
            {"name": "HDMI-A-1", "enabled": False},
            {"name": "DP-1", "enabled": True},
        ]},
        provider="forced-connector", vdd_output="DP-1", environ=env,
    )
    assert snapshot.load(environ=env) is None
    # ...and it is gone, so it cannot poison the next session either.
    assert not os.path.exists(os.path.join(snapshot.state_dir(environ=env), "snapshot.json"))


def test_a_healthy_snapshot_still_loads(tmp_path):
    env = _environ(tmp_path)
    payload = {"outputs": [
        {"name": "eDP-1", "enabled": True, "mode": "2560x1600@165"},
        {"name": "DP-1", "enabled": False},
    ]}
    snapshot.save("kscreen", payload, provider="forced-connector",
                  vdd_output="DP-1", environ=env)
    doc = snapshot.load(environ=env)
    assert doc is not None and doc["payload"] == payload


def test_save_is_atomic_no_tmp_left_behind(tmp_path):
    env = _environ(tmp_path)
    snapshot.save("wlr", {"outputs": []}, environ=env)
    entries = os.listdir(snapshot.state_dir(environ=env))
    assert "snapshot.json" in entries
    assert not any(e.endswith(".tmp") for e in entries)


# --- the remembered desktop -------------------------------------------------
#
# "Put the monitors back" is not "switch every monitor on". A laptop folded
# under a desk with its panel deliberately dark is a normal way to work.

_DESK = {"outputs": [
    {"name": "eDP-1", "enabled": False},                       # laptop, lid down, on the floor
    {"name": "HDMI-A-1", "enabled": True, "mode": "1920x1080@120",
     "x": 0, "y": 0, "primary": True},
]}


def test_a_deliberately_dark_panel_is_remembered_as_dark(tmp_path):
    env = _environ(tmp_path)
    snapshot.remember("kscreen", _DESK, environ=env)
    desk = snapshot.remembered(environ=env)
    edp = next(o for o in desk["payload"]["outputs"] if o["name"] == "eDP-1")
    assert edp["enabled"] is False, "the user's dark laptop panel must stay dark"


def test_the_desk_survives_a_restore(tmp_path):
    """Unlike the session snapshot, it is never cleared — it is what `restore`
    falls back to when there is no snapshot at all."""
    env = _environ(tmp_path)
    snapshot.remember("kscreen", _DESK, environ=env)
    snapshot.save("kscreen", _DESK, vdd_output="DP-1", environ=env)
    snapshot.clear(environ=env)
    assert snapshot.load(environ=env) is None
    assert snapshot.remembered(environ=env) is not None


def test_a_dark_desk_is_never_remembered(tmp_path):
    env = _environ(tmp_path)
    snapshot.remember("kscreen", _DESK, environ=env)
    snapshot.remember("kscreen", {"outputs": [                  # mid-headless capture
        {"name": "eDP-1", "enabled": False},
        {"name": "HDMI-A-1", "enabled": False},
    ]}, environ=env)
    desk = snapshot.remembered(environ=env)
    hdmi = next(o for o in desk["payload"]["outputs"] if o["name"] == "HDMI-A-1")
    assert hdmi["enabled"] is True, "the good desk must not be overwritten by a dark one"


def test_forget_drops_it(tmp_path):
    env = _environ(tmp_path)
    snapshot.remember("kscreen", _DESK, environ=env)
    snapshot.forget(environ=env)
    assert snapshot.remembered(environ=env) is None


# --- knowing which outputs are ours ------------------------------------------

def test_a_real_monitor_named_like_a_vdd_is_not_ours(tmp_path):
    """Headless sway calls the user's genuine outputs HEADLESS-1, HEADLESS-2 —
    the same names it gives the virtual displays we create. Guessing from the
    name meant destroying the user's monitor as an 'orphaned VDD'. Only what we
    recorded creating is ours."""
    env = _environ(tmp_path)
    snapshot.track_vdd("HEADLESS-2", environ=env)   # the one we made
    ours = snapshot.tracked_vdds(environ=env)
    assert ours == {"HEADLESS-2"}
    assert "HEADLESS-1" not in ours                 # the user's monitor. Hands off.


def test_a_vdd_stops_being_ours_once_torn_down(tmp_path):
    env = _environ(tmp_path)
    snapshot.track_vdd("HEADLESS-2", environ=env)
    snapshot.untrack_vdd("HEADLESS-2", environ=env)
    assert snapshot.tracked_vdds(environ=env) == set()
