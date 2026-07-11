"""Layout backends: enumerate outputs, apply headless/dual, restore.

A backend controls *arrangement* of displays the compositor already knows
about; making a new display exist at all is the providers' job.  Every
backend implements the same small surface:

    outputs()                        -> list[OutputState]
    snapshot()                       -> JSON-safe payload for restore()
    apply_headless(vdd, mode)        -> only the VDD stays lit
    apply_dual(vdd, mode, baseline)  -> the user's layout, plus the VDD right of it
    restore(payload)                 -> replay a snapshot exactly
    wait_for_output(name, timeout)   -> block until a (new) output appears

``apply_headless`` and ``apply_dual`` both *assert* a complete target state
rather than nudging the current one.  That matters because they are reached in
any order and from any starting point: dual is routinely entered straight out
of a headless session, when every physical output is dark.  An apply that only
adds the VDD would leave it that way — still headless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import List, Optional

from ..modes import Mode
from ..runner import Runner


@dataclass
class OutputState:
    """Backend-agnostic view of one output."""

    name: str
    enabled: bool
    connected: bool = True
    width: int = 0
    height: int = 0
    refresh: float = 0.0
    x: int = 0
    y: int = 0
    scale: float = 1.0
    primary: bool = False
    priority: int = 0
    modes: List[str] = field(default_factory=list)  # "WxH@Hz" strings


class LayoutBackend:
    name = "abstract"

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def outputs(self) -> List[OutputState]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def apply_headless(self, vdd: str, mode: Mode) -> None:
        raise NotImplementedError

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None) -> None:
        raise NotImplementedError

    def restore(self, payload: dict) -> None:
        raise NotImplementedError

    def wait_for_output(self, name_hint: str, timeout: float = 8.0) -> Optional[str]:
        """Poll until an output matching `name_hint` shows up.

        `name_hint` may be an exact name or a prefix (providers that cannot
        predict the final name pass a prefix like ``HEADLESS-``).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for out in self.outputs():
                if out.name == name_hint or out.name.startswith(name_hint):
                    return out.name
            if self.runner.dry_run:
                return name_hint
            time.sleep(0.25)
        return None

    def wait_for_user_layout(self, exclude: Optional[str] = None, timeout: float = 5.0) -> bool:
        """Block until some monitor other than `exclude` is lit again.

        `restore` hands the compositor a new layout and returns; the monitors
        come back a beat later.  Anything that snapshots in that window records
        a dark desk as the user's own layout.
        """
        deadline = time.monotonic() + timeout
        while True:
            if any(o.enabled and o.name != exclude for o in self.outputs()):
                return True
            if self.runner.dry_run or time.monotonic() >= deadline:
                return self.runner.dry_run
            time.sleep(0.25)

    @staticmethod
    def baseline_outputs(baseline: Optional[dict]) -> List[OutputState]:
        """Read a saved snapshot payload back as `OutputState`s.

        Backends record geometry differently — kscreen/gnome write a
        ``"2560x1600@165"`` mode string, xrandr a ``"2560x1600"`` plus a
        separate refresh, wlr plain width/height/refresh — so accept all three.
        """
        outs: List[OutputState] = []
        for raw in (baseline or {}).get("outputs", []):
            width = int(raw.get("width") or 0)
            height = int(raw.get("height") or 0)
            refresh = float(raw.get("refresh") or 0.0)
            if not width:
                geometry, _, hz = str(raw.get("mode") or "").partition("@")
                w, _, h = geometry.partition("x")
                try:
                    width, height = int(w), int(h)
                    refresh = float(hz) if hz else refresh
                except ValueError:  # no mode recorded, or an unparseable one
                    width = height = 0
            outs.append(
                OutputState(
                    name=raw.get("name", ""),
                    enabled=bool(raw.get("enabled")),
                    width=width,
                    height=height,
                    refresh=refresh,
                    x=raw.get("x", 0),
                    y=raw.get("y", 0),
                    scale=float(raw.get("scale") or 1.0),
                    primary=bool(raw.get("primary")),
                    priority=int(raw.get("priority") or 0),
                )
            )
        return outs

    def dual_targets(self, vdd: str, baseline: Optional[dict]) -> List[OutputState]:
        """The state the non-VDD outputs must end up in for a dual session.

        Prefer the saved baseline: it is the layout the user actually chose, and
        it is the only record of it once headless has switched their monitors
        off.  With no usable baseline, fall back to lighting every connected
        monitor at the geometry the compositor still remembers — a dual session
        that leaves the desk dark is worse than one that guesses.
        """
        from ..snapshot import is_user_layout

        if is_user_layout(baseline or {}, vdd):
            return [o for o in self.baseline_outputs(baseline) if o.name != vdd]
        return [
            replace(o, enabled=True)
            for o in self.outputs()
            if o.name != vdd and o.connected
        ]

    @staticmethod
    def rightmost_edge(outputs: List[OutputState]) -> int:
        """X coordinate just past the rightmost enabled output."""
        edge = 0
        for out in outputs:
            if out.enabled:
                logical_w = int(out.width / (out.scale or 1.0))
                edge = max(edge, out.x + logical_w)
        return edge


def get_backend(env, runner: Runner) -> Optional[LayoutBackend]:
    """Pick the layout backend for this environment."""
    from . import gnome, kscreen, wlr, xrandr

    desktop = env.desktop
    if env.session_type == "wayland":
        if "kde" in desktop and env.tools.get("kscreen-doctor"):
            return kscreen.KScreenBackend(runner)
        if ("gnome" in desktop or "ubuntu" in desktop) and gnome.available():
            return gnome.GnomeBackend(runner)
        if env.tools.get("wlr-randr"):
            return wlr.WlrBackend(runner)
        if env.tools.get("kscreen-doctor"):
            return kscreen.KScreenBackend(runner)
    if env.tools.get("xrandr") and env.session_type == "x11":
        return xrandr.XrandrBackend(runner)
    return None
