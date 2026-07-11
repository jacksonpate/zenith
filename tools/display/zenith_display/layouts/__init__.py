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
from typing import Iterable, List, Optional

from ..modes import Mode
from ..runner import Runner

_SIDEWAYS = ("left", "right")


@dataclass
class OutputState:
    """Backend-agnostic view of one output."""

    name: str
    enabled: bool
    connected: bool = True
    width: int = 0  # the mode's own width, i.e. before any rotation
    height: int = 0
    refresh: float = 0.0
    x: int = 0
    y: int = 0
    scale: float = 1.0
    primary: bool = False
    priority: int = 0
    rotation: str = "normal"  # normal | left | right | inverted
    modes: List[str] = field(default_factory=list)  # "WxH@Hz" strings

    @property
    def logical_width(self) -> int:
        """Width as the desktop sees it: rotated, then scaled.

        A monitor on its side is 1080 wide, not 1920 — place the next output at
        its mode width and you leave a dead gap.
        """
        width = self.height if self.rotation in _SIDEWAYS else self.width
        return int(width / (self.scale or 1.0))

    def best_mode(self) -> Optional[Mode]:
        """The output's own preferred mode, for when nothing else knows one.

        A *disabled* output reports no current mode on most stacks — sway says
        `current_mode: null`, X reports no geometry — so this is the only way to
        light one back up without a snapshot to read a mode out of.
        """
        for name in self.modes:  # backends list preferred-first
            geometry, _, hz = name.partition("@")
            w, _, h = geometry.partition("x")
            try:
                return Mode(int(w), int(h), round(float(hz)) if hz else 60)
            except ValueError:
                continue
        return None


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

    def relight(self, vdds: Iterable[str] = ()) -> None:
        """Assert a plain desktop — every monitor on, every VDD off.

        The last resort: the snapshot is missing, or was poison and got
        discarded, and the desk is dark anyway.  A plausible desktop beats a
        dark one, so bring the connected outputs up at whatever mode the display
        stack still remembers (or prefers) for them.

        The VDDs go too.  Nothing else will take them down — the snapshot that
        named the provider which created them is exactly what we no longer have
        — and a leftover virtual display is a phantom monitor on the user's
        desk.  Implementations must leave the display alone entirely if there is
        no real monitor to fall back to, rather than blank the only output.
        """
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

    def wait_for_user_layout(self, exclude: Iterable[str] = (), timeout: float = 5.0) -> bool:
        """Block until some monitor that is not a VDD is lit again.

        `restore` hands the compositor a new layout and returns; the monitors
        come back a beat later.  Anything that snapshots in that window records
        a dark desk as the user's own layout.
        """
        virtual = set(exclude)
        deadline = time.monotonic() + timeout
        while True:
            if any(o.enabled and o.name not in virtual for o in self.outputs()):
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
                    rotation=raw.get("rotation") or "normal",
                )
            )
        return outs

    def dual_targets(self, vdd: str, baseline: Optional[dict]) -> List[OutputState]:
        """The state the non-VDD outputs must end up in for a dual session.

        Every target this returns is *applicable*: it names an output the
        display stack currently has, it carries a mode to light it with, and the
        set as a whole is packed from the origin with exactly one primary.  A
        backend can hand the result straight to its display tool.

        That is not fussiness.  A target the stack has never heard of takes down
        the whole apply (mutter rejects the config outright; xrandr exits 1 and
        `check=True` raises), and a target with no mode gets silently skipped —
        both of which stranded the user in headless, which is the very bug this
        code exists to prevent.

        Prefer the saved baseline: it is the layout the user actually chose, and
        the only record of it once headless has switched their monitors off.
        Failing that, guess from the outputs themselves — a dual session that
        leaves the desk dark is worse than one that guesses.
        """
        from ..snapshot import is_user_layout

        live = {o.name: o for o in self.outputs() if o.name != vdd}

        if is_user_layout(baseline or {}, vdd):
            # Drop what the stack no longer has: a monitor unplugged since the
            # snapshot was taken is not an error, it is Tuesday.
            wanted = [o for o in self.baseline_outputs(baseline)
                      if o.name != vdd and o.name in live]
            targets = [self._playable(o, live[o.name]) for o in wanted]
            targets = [o for o in targets if o is not None]
            if any(o.enabled for o in targets):
                return self._packed(targets)

        # No baseline worth the name. Light every connected monitor at its own
        # preferred mode; positions are gone, so lay them out left to right.
        guessed = []
        for out in live.values():
            if not out.connected:
                continue
            playable = self._playable(replace(out, enabled=True, x=0, y=0), out)
            if playable is not None:
                guessed.append(playable)
        return self._packed(guessed, repack=True)

    def _playable(self, want: OutputState, live: OutputState) -> Optional[OutputState]:
        """`want`, with a mode it can actually be lit at — or None if there is none.

        Two ways a target arrives unlightable.  A disabled output reports no
        current mode on most stacks (sway says `current_mode: null`, X reports no
        geometry), so a target rebuilt from one carries `width=0` and backends
        skipped it silently, leaving the monitor dark.  And a mode read out of a
        snapshot may simply be gone — swap the monitor, dock elsewhere — which
        takes the whole apply down with it (`xrandr: cannot find mode`).

        Either way the output's own mode list is the answer.
        """
        if not want.enabled:
            return want
        if not live.modes:  # backend reports no mode list; trust what we were given
            return want
        if want.width and any(m.startswith(f"{want.width}x{want.height}") for m in live.modes):
            return want
        mode = live.best_mode()
        if mode is None:
            return None  # nothing to light it with; better skipped than fatal
        return replace(want, width=mode.width, height=mode.height, refresh=mode.refresh)

    @staticmethod
    def _packed(targets: List[OutputState], repack: bool = False) -> List[OutputState]:
        """Guarantee the two invariants a layout cannot be applied without.

        Positions from a real baseline are left exactly as the user had them —
        negative coordinates and all; a monitor to the left of the origin is a
        normal thing to own.  They are only rewritten when they cannot be used
        as-is: two outputs sharing a position is a *mirrored* desktop, not a
        dual one, and a layout with no primary is rejected outright by mutter
        and silently leaves the VDD primary on X11.
        """
        lit = [o for o in targets if o.enabled]
        if not lit:
            return targets

        if repack or len({(o.x, o.y) for o in lit}) < len(lit):
            edge = 0
            for out in lit:
                out.x, out.y = edge, 0
                edge += out.logical_width

        if not any(o.primary for o in lit):
            lit[0].primary = True
        return targets

    @staticmethod
    def rightmost_edge(outputs: List[OutputState]) -> int:
        """X coordinate just past the rightmost enabled output."""
        edge = 0
        for out in outputs:
            if out.enabled:
                edge = max(edge, out.x + out.logical_width)
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
