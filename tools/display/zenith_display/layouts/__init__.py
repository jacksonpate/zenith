"""Layout backends: enumerate outputs, apply headless/dual, restore.

A backend controls *arrangement* of displays the compositor already knows
about; making a new display exist at all is the providers' job.  Every
backend implements the same small surface:

    outputs()                       -> list[OutputState]
    snapshot()                      -> JSON-safe payload for restore()
    apply_headless(vdd, mode)       -> only the VDD stays lit
    apply_dual(vdd, mode)           -> VDD joins to the right of everything
    restore(payload)                -> replay a snapshot exactly
    wait_for_output(name, timeout)  -> block until a (new) output appears
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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

    def apply_dual(self, vdd: str, mode: Mode) -> None:
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
