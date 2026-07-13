"""Where the streaming display sits, and how big things are on it.

The virtual display is created fresh every session, so nothing about it survives
on its own — drag it under the desk, set the zoom that makes text readable from
the sofa, and next time it is back off the right edge at whatever scale the
compositor guessed. That is not a new display each time; it is *the* streaming
display, and it should come back where it was left.

The same principle as the deliberately-dark laptop panel: the arrangement is the
user's, and putting it back is the whole job.

What is emphatically *not* the user's is the resolution. That belongs to whoever
is connecting — quit on a tablet, pick up on a phone.
"""

import json

from conftest import FakeRunner

from zenith_display import snapshot
from zenith_display.layouts import OutputState, Rect, is_coherent
from zenith_display.layouts.kscreen import KScreenBackend
from zenith_display.modes import Mode


def _environ(tmp_path):
    return {"XDG_STATE_HOME": str(tmp_path)}


def _backend(fixture_text):
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    return KScreenBackend(FakeRunner({"kscreen-doctor": json.dumps(doc)}))


# In the fixture: HDMI-A-1 is lit at (16,0), 1920x1080 @ scale 1 -> logical
# 1920x1080, priority 1 (the anchor). Its right edge is therefore x=1936.
BELOW_THE_MONITOR = {"anchor": "HDMI-A-1", "dx": -16, "dy": 1080, "scale": 1.25}


def test_a_placement_round_trips(tmp_path):
    env = _environ(tmp_path)
    snapshot.remember_vdd("DP-1", scale=1.25, offset=BELOW_THE_MONITOR, environ=env)
    got = snapshot.remembered_vdd(environ=env)
    assert got["scale"] == 1.25
    assert got["anchor"] == "HDMI-A-1" and got["dx"] == -16 and got["dy"] == 1080


def test_nothing_remembered_is_not_an_error(tmp_path):
    assert snapshot.remembered_vdd(environ=_environ(tmp_path)) is None


def test_dual_puts_the_vdd_back_where_it_was_left(fixture_text):
    """Tucked under the monitor last night; it belongs under the monitor tonight."""
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None, placement=BELOW_THE_MONITOR)
    argv = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.position.0,1080" in argv   # 16 + (-16), 0 + 1080
    assert "output.DP-1.scale.1.25" in argv


def test_the_position_is_remembered_against_a_monitor_not_as_a_coordinate(fixture_text):
    """The reason offsets exist at all.

    Compositors renormalise a layout after every apply — KDE slides the desktop's
    top-left corner back to 0,0 — so the absolute coordinate a session records is
    not the coordinate the next session needs. Move the whole desk and "under the
    monitor" still has to mean under the monitor.
    """
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    for out in doc["outputs"]:            # the desk shifted 500px right overnight
        if out["name"] == "HDMI-A-1":
            out["pos"] = {"x": 516, "y": 0}
    backend = KScreenBackend(FakeRunner({"kscreen-doctor": json.dumps(doc)}))

    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None, placement=BELOW_THE_MONITOR)
    argv = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.position.500,1080" in argv, \
        f"the offset must follow the monitor, not a stale coordinate: {argv}"


def test_without_a_placement_it_lands_past_the_right_edge(fixture_text):
    """First run on a new machine: no memory, so fall back to the sane default
    rather than stacking it on top of a real monitor at 0,0."""
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None)
    argv = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.position.1936,0" in argv


def test_an_incoherent_memory_is_dropped_rather_than_applied(fixture_text):
    """The bug that made this whole design necessary.

    KDE rejects a layout whose screens overlap or leave a gap — and it rejects it
    *whole*, reverting every part of it. Zenith enables the streaming display in
    the same atomic call that positions everything else, so one impossible
    coordinate does not produce a slightly-wrong desk: it produces no streaming
    display at all, and the user sees a plain mirrored desktop with no error.

    A memory that no longer fits the desk it was measured against is therefore
    worse than no memory. Drop it and put the display somewhere that works.
    """
    backend = _backend(fixture_text)
    marooned = {"anchor": "HDMI-A-1", "dx": 0, "dy": 5000, "scale": 1.25}  # miles below
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None, placement=marooned)
    argv = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.position.16,5080" not in argv, "applied a layout KDE will revert"
    assert "output.DP-1.position.1936,0" in argv, f"should have snapped right: {argv}"
    assert "output.DP-1.scale.1.25" in argv, "the zoom is still the user's"


def test_a_lit_monitor_is_never_restated(fixture_text):
    """While the monitors are lit, the desk in front of the user IS the desk.

    Restating a remembered mode, position or zoom onto a screen they are looking
    at is how an in-session zoom change gets silently undone — and how a rescaled
    screen stops reaching its neighbour, which is a gap, which KDE reverts.
    """
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), None, placement=BELOW_THE_MONITOR)
    argv = " ".join(backend.runner.trace[-1])
    assert "HDMI-A-1" not in argv, f"said something about a monitor it should not touch: {argv}"


def test_dual_from_headless_relights_the_monitors(fixture_text):
    """...but when the desk is dark, it does have to be rebuilt — that is the
    original bug: dual entered from headless left every monitor off."""
    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    for out in doc["outputs"]:
        out["enabled"] = out["name"] == "DP-1"    # headless: only the VDD is lit
    backend = KScreenBackend(FakeRunner({"kscreen-doctor": json.dumps(doc)}))

    baseline = {"outputs": [{"name": "HDMI-A-1", "enabled": True,
                             "mode": "1920x1080@120", "x": 0, "y": 0, "scale": 1.0}]}
    backend.apply_dual("DP-1", Mode(2420, 1668, 120), baseline)
    argv = " ".join(backend.runner.trace[-1])
    assert "output.HDMI-A-1.enable" in argv, f"left the desk dark: {argv}"


def test_a_new_client_gets_its_own_resolution_not_the_last_one(fixture_text):
    """Quit on the iPad, connect from the phone, and the streaming display came
    back at the iPad's 2420x1668.

    Where the display sits and how big things are on it are decisions the user
    made. Its resolution is not: that is whatever the client on the other end
    asked for, and it changes every time a different device connects.
    """
    backend = _backend(fixture_text)
    backend.apply_dual("DP-1", Mode(2340, 1080, 60), None, placement=BELOW_THE_MONITOR)
    argv = " ".join(backend.runner.trace[-1])
    assert "2420x1668" not in argv, f"replayed the last client's resolution: {argv}"
    assert "output.DP-1.scale.1.25" in argv       # the zoom they chose: kept


def test_headless_honours_the_remembered_zoom(fixture_text):
    """Position is meaningless when it is the only display, but the zoom is not:
    the compositor guesses a scale from a physical size the VDD does not have,
    and the guess is what makes everything soft and oversized."""
    backend = _backend(fixture_text)
    backend.apply_headless("DP-1", Mode(2420, 1668, 120), placement={"scale": 1.25})
    argv = " ".join(backend.runner.trace[-1])
    assert "output.DP-1.scale.1.25" in argv


def test_headless_never_teaches_it_a_position(tmp_path):
    """In headless the virtual display is the ONLY display, so it sits at 0,0 —
    which is not a choice the user made, it is just where a lone screen goes, and
    there is no monitor beside it to measure an offset against.

    The zoom, on the other hand, IS the user's — learn that from anywhere.
    """
    env = _environ(tmp_path)
    snapshot.remember_vdd("DP-1", scale=1.0, offset=BELOW_THE_MONITOR, environ=env)
    snapshot.remember_vdd_scale("DP-1", scale=1.25, environ=env)   # then a headless session
    got = snapshot.remembered_vdd(environ=env)
    assert got["dy"] == 1080, "the dual placement must survive a headless session"
    assert got["scale"] == 1.25, "but the zoom the user just set must stick"


def test_a_change_made_during_a_dual_session_is_not_undone(fixture_text):
    """Rescale the monitor while streaming and `restore` used to shove it back to
    whatever it was before the session — silently undoing a deliberate change,
    and leaving a gap in the layout where the resized screen no longer reached.
    """
    from zenith_display import cli

    doc = json.loads(fixture_text("kscreen_silverblue.json"))
    for out in doc["outputs"]:
        out["enabled"] = True          # a dual session: monitors lit, VDD lit
        if out["name"] == "HDMI-A-1":
            out["scale"] = 1.75        # the user just changed the zoom
    runner = FakeRunner({"kscreen-doctor": json.dumps(doc)})

    stale = {"backend": "kscreen", "provider": "", "vdd_output": "DP-1",
             "payload": {"outputs": [
                 {"name": "HDMI-A-1", "enabled": True, "mode": "1920x1080@120",
                  "x": 0, "y": 0, "scale": 1.0},   # the PRE-session zoom
             ]}}
    cli._restore_from(stale, _fake_env(), runner)

    applied = " ".join(runner.trace[-1])
    assert "output.DP-1.disable" in applied, "the VDD must still go away"
    assert "output.HDMI-A-1.scale.1.0" not in applied, \
        f"must not shove the monitor back to its old zoom: {applied}"


def _fake_env():
    from zenith_display.detect import Environment
    return Environment(session_type="wayland", desktop="kde", distro="fedora", tools={},
                       connectors=[], is_root=False, has_passwordless_sudo=False)


# --- the coherence rule itself -------------------------------------------------

def test_touching_screens_are_coherent():
    assert is_coherent([Rect(0, 0, 1920, 1080), Rect(1920, 0, 2420, 1668)])


def test_overlapping_screens_are_not():
    assert not is_coherent([Rect(0, 0, 1920, 1080), Rect(1900, 0, 2420, 1668)])


def test_a_gap_is_not():
    """A gap is the failure the user actually hit: change a monitor's zoom and the
    display tucked beneath it no longer reaches its edge."""
    assert not is_coherent([Rect(0, 0, 1097, 617), Rect(0, 1080, 1936, 1334)])


def test_screens_meeting_at_a_corner_are_not():
    """Corner-to-corner leaves the desktop pinched to a point, and the compositor
    counts that as a gap — the pointer cannot cross."""
    assert not is_coherent([Rect(0, 0, 100, 100), Rect(100, 100, 100, 100)])


def test_the_poisoned_desk_that_started_all_this():
    """The literal layout Zenith had memorised and replayed every session: three
    disjoint islands, courtesy of an emergency relight that switched the laptop
    panel on at whatever stale coordinates KDE had lying around.

    KDE threw the whole thing out, every time, and with it the one instruction
    that mattered — turn the streaming display on.
    """
    assert not is_coherent([
        Rect(1920, -3227, 1707, 1067),   # eDP-1, marooned above
        Rect(16, 0, 1097, 617),          # HDMI-A-1
        Rect(0, 1080, 1936, 1334),       # DP-1, marooned below
    ])


def test_the_anchor_is_the_primary():
    outs = [OutputState(name="HDMI-A-1", enabled=True, width=1920, height=1080,
                        x=16, y=0, priority=1),
            OutputState(name="eDP-1", enabled=True, width=2560, height=1600,
                        x=0, y=0, priority=2)]
    assert KScreenBackend.anchor_of(outs).name == "HDMI-A-1"


def test_a_dark_desk_has_no_anchor():
    outs = [OutputState(name="HDMI-A-1", enabled=False, width=1920, height=1080)]
    assert KScreenBackend.anchor_of(outs) is None
