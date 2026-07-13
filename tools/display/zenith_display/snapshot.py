"""Crash-safe layout snapshots, and a memory of the user's actual desk.

Two files, with two different lifetimes:

``snapshot.json`` is written *before* the autopilot touches anything and
deleted once it has put things back.  ``restore`` replays it; if Zenith crashes
mid-session the next invocation finds the stale snapshot and restores it first
— a user's monitors must never stay dark because a stream died.

``desktop.json`` is the last layout that looked like a desk somebody was
actually sitting at, and it is *never* deleted.  It exists because "put the
monitors back" is not the same instruction as "switch every monitor on": a
laptop folded under a desk with its panel deliberately dark is a normal way to
work, and a session that ends by lighting it up has not restored anything — it
has rearranged the room.  Which outputs were off, where they sat relative to
one another, which one was primary: all of it is the user's, and none of it is
recoverable by guessing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Iterable, Optional, Union

log = logging.getLogger("zenith-display")


def state_dir(environ=os.environ) -> str:
    base = environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "zenith", "display")
    os.makedirs(path, exist_ok=True)
    return path


def _path(environ=os.environ) -> str:
    return os.path.join(state_dir(environ), "snapshot.json")


def _desk_path(environ=os.environ) -> str:
    return os.path.join(state_dir(environ), "desktop.json")


def remember(backend: str, payload: dict, environ=os.environ) -> None:
    """Learn this layout as the user's desk, if it plausibly is one.

    Called whenever Zenith looks at a display with no virtual one in the way.
    It is how ``restore`` knows to leave the laptop panel dark and put the
    primary back where it belongs, instead of lighting up everything it finds.
    """
    if not is_user_layout(payload):
        return  # a dark desk is not a desk anyone chose
    doc = {"version": 1, "saved": time.time(), "backend": backend, "payload": payload}
    tmp = _desk_path(environ) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, _desk_path(environ))


def _vdds_path(environ=os.environ) -> str:
    return os.path.join(state_dir(environ), "vdds.json")


def tracked_vdds(environ=os.environ) -> set:
    """Virtual displays Zenith created and has not torn down.

    The only trustworthy record of what is ours.  A name cannot be trusted:
    sway calls its outputs HEADLESS-1, HEADLESS-2… whether they are virtual
    displays we made or the user's actual monitors in a headless session, and
    mistaking the latter for the former means destroying somebody's screen.
    """
    try:
        with open(_vdds_path(environ), encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def _write_vdds(names: set, environ=os.environ) -> None:
    tmp = _vdds_path(environ) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(names), fh)
    os.replace(tmp, _vdds_path(environ))


def track_vdd(name: str, environ=os.environ) -> None:
    _write_vdds(tracked_vdds(environ) | {name}, environ)


def untrack_vdd(name: str, environ=os.environ) -> None:
    _write_vdds(tracked_vdds(environ) - {name}, environ)


def forget(environ=os.environ) -> None:
    try:
        os.unlink(_desk_path(environ))
    except OSError:
        pass


def remembered(environ=os.environ) -> Optional[dict]:
    """The last layout the user was really sitting at, or None."""
    try:
        with open(_desk_path(environ), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if is_user_layout(doc.get("payload", {})) else None


def save(backend: str, payload: dict, provider: Optional[str] = None,
         vdd_output: Optional[str] = None, environ=os.environ) -> str:
    doc = {
        "version": 1,
        "created": time.time(),
        "backend": backend,
        "provider": provider,
        "vdd_output": vdd_output,
        "payload": payload,
    }
    path = _path(environ)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)
    return path


def load(environ=os.environ) -> Optional[dict]:
    """The snapshot on disk, or None — including when the one on disk is poison.

    Zenith used to capture the baseline without waiting for a previous
    session's restore to land, so a snapshot could record *every monitor dark*
    as the layout to restore to.  Replaying one is how a stream ends with the
    desk still dark, and every session after it re-captured the darkness.

    Discard those on sight rather than replaying them: an upgraded install
    heals itself the first time it reads the bad file.  Callers that find no
    snapshot must relight the monitors themselves — see `cmd_restore`.
    """
    try:
        with open(_path(environ), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not is_user_layout(doc.get("payload", {}), doc.get("vdd_output")):
        log.warning("discarding a snapshot that has no monitor lit — it was captured "
                    "mid-teardown and restoring it would leave the display dark")
        clear(environ)
        return None
    return doc


def clear(environ=os.environ) -> None:
    try:
        os.unlink(_path(environ))
    except OSError:
        pass


def is_user_layout(payload: dict, vdds: Union[str, Iterable[str], None] = None) -> bool:
    """True when `payload` could plausibly be a layout the user was using.

    A layout with no *real* monitor lit is one of ours, caught mid-teardown —
    nobody sits in front of a dark desk.  Saving one as the restore target is how
    a session ends with the monitors still off: every later ``restore``
    faithfully replays the darkness.

    `vdds` is every virtual display, not merely the current one: a VDD leaked by
    a crashed session has a different name, and mistaking it for a monitor is
    what makes a dark desk look lit.
    """
    if vdds is None:
        vdds = ()
    elif isinstance(vdds, str):
        vdds = (vdds,)
    virtual = set(vdds)
    for out in payload.get("outputs", []):
        if out.get("enabled") and out.get("name") not in virtual:
            return True
    return False

def _vdd_placement_path(environ=os.environ) -> str:
    return os.path.join(state_dir(environ), "vdd-placement.json")


def remember_vdd(name: str, scale: float, offset: Optional[dict] = None,
                 environ=os.environ) -> None:
    """Where the streaming display sat, and how big things were on it.

    The virtual display is destroyed at the end of every session, so nothing
    about it survives on its own: drag it below the desk, set a zoom that is
    readable from the sofa, and next session it is back off the right edge at
    whatever scale the compositor guessed from a physical size it does not have.
    It is not a new display each time — it is *the* streaming display, and it
    belongs where it was left.

    `offset` places it against a monitor (``anchor``/``dx``/``dy``) rather than
    at an absolute coordinate, because absolute coordinates do not survive:
    compositors renormalise a layout after every apply — KDE slides the top-left
    corner of the desktop back to 0,0 — so the numbers a session records are not
    the numbers the next session would need. An offset from a screen the user can
    actually see is stable under that, and under a zoom change too.

    Kept apart from the desktop snapshot on purpose. That records the user's real
    monitors and is cleared when a session ends; this outlives every session,
    because the whole point is to survive one.
    """
    doc = {"version": 2, "saved": time.time(), "name": name, "scale": float(scale)}
    doc.update(offset or {})
    tmp = _vdd_placement_path(environ) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, _vdd_placement_path(environ))


def remembered_vdd(environ=os.environ) -> Optional[dict]:
    """The placement the user last left the streaming display in, or None."""
    try:
        with open(_vdd_placement_path(environ), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or "scale" not in doc:
        return None
    return doc


def remember_vdd_scale(name: str, scale: float, environ=os.environ) -> None:
    """Update only the zoom, leaving the remembered position untouched.

    A headless session has one display, and a lone display sits at 0,0 — that is
    where the compositor puts it, not a choice anybody made, and there is no
    monitor beside it to measure an offset against. Recording it as the user's
    position means the next dual session drops the virtual display straight on top
    of a monitor, and the desktop mirrors instead of extending.

    The zoom is different: the user sets it, and it is theirs wherever they set it.
    """
    doc = remembered_vdd(environ) or {"version": 2, "name": name}
    doc["name"] = name
    doc["scale"] = float(scale)
    doc["saved"] = time.time()
    tmp = _vdd_placement_path(environ) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, _vdd_placement_path(environ))
