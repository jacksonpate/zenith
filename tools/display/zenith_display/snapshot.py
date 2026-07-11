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
