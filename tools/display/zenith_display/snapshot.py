"""Crash-safe layout snapshots.

A snapshot is written *before* the autopilot touches anything.  ``restore``
replays it; if Zenith crashes mid-session the next invocation finds the stale
snapshot and restores it first — a user's monitors must never stay dark
because a stream died.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional


def state_dir(environ=os.environ) -> str:
    base = environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "zenith", "display")
    os.makedirs(path, exist_ok=True)
    return path


def _path(environ=os.environ) -> str:
    return os.path.join(state_dir(environ), "snapshot.json")


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
    try:
        with open(_path(environ), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def clear(environ=os.environ) -> None:
    try:
        os.unlink(_path(environ))
    except OSError:
        pass
