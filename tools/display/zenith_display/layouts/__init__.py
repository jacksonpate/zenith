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

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Iterable, List, Optional

from ..modes import Mode
from ..runner import Runner

log = logging.getLogger("zenith-display")

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

    @property
    def logical_height(self) -> int:
        """Height as the desktop sees it: rotated, then scaled."""
        height = self.width if self.rotation in _SIDEWAYS else self.height
        return int(height / (self.scale or 1.0))

    @property
    def rect(self) -> "Rect":
        return Rect(self.x, self.y, self.logical_width, self.logical_height)

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


@dataclass(frozen=True)
class Rect:
    """One display's footprint on the desktop, in logical pixels."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def overlaps(self, other: "Rect") -> bool:
        return (self.x < other.right and other.x < self.right
                and self.y < other.bottom and other.y < self.bottom)

    def touches(self, other: "Rect") -> bool:
        """Do these two share a border of non-zero length?

        Corner-to-corner does not count: two screens meeting at a single point
        leave the desktop pinched, and the compositor treats that as a gap.
        """
        edge_x = self.right == other.x or other.right == self.x
        edge_y = self.bottom == other.y or other.bottom == self.y
        overlap_y = min(self.bottom, other.bottom) - max(self.y, other.y)
        overlap_x = min(self.right, other.right) - max(self.x, other.x)
        return (edge_x and overlap_y > 0) or (edge_y and overlap_x > 0)


def is_coherent(rects: List[Rect]) -> bool:
    """Would a compositor accept this arrangement?

    KDE — and it is not alone — rejects a layout outright if any two screens
    overlap, or if the screens do not form one connected surface. It does not
    partially apply such a layout; it reverts the whole thing. That matters far
    more than it sounds, because Zenith enables the virtual display in the *same*
    atomic call that positions the monitors: one bad coordinate anywhere and the
    stream has no display at all. The user sees a plain desktop and no error.

    So anything assembled from memory gets checked here before it is applied, and
    anything that fails the check is not applied — it is replaced by a layout that
    is merely correct rather than remembered.
    """
    if len(rects) < 2:
        return True
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a.overlaps(b):
                return False

    # One connected surface: walk the touch graph and see if it reaches everyone.
    seen = {0}
    frontier = [0]
    while frontier:
        i = frontier.pop()
        for j, other in enumerate(rects):
            if j not in seen and rects[i].touches(other):
                seen.add(j)
                frontier.append(j)
    return len(seen) == len(rects)


class LayoutBackend:
    name = "abstract"

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def outputs(self) -> List[OutputState]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def apply_headless(self, vdd: str, mode: Mode,
                       placement: Optional[dict] = None) -> None:
        raise NotImplementedError

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None,
                   placement: Optional[dict] = None) -> None:
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

    def attach_new_outputs(self) -> None:
        """Adopt a virtual display a provider has just created.

        Wayland compositors pick up a new DRM card on their own, so for them
        this is nothing.  X11 does not — see `XrandrBackend.attach_new_outputs`.
        """

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

    def snapshot_of(self, outputs: List[OutputState]) -> dict:
        """A snapshot payload for a subset of outputs, in this backend's shape.

        `snapshot()` reads the display fresh; this serialises outputs already in
        hand — the ones the user was looking at a moment ago, before the virtual
        display was taken away.
        """
        names = {o.name for o in outputs}
        return {"outputs": [o for o in self.snapshot().get("outputs", [])
                            if o.get("name") in names]}

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

    @staticmethod
    def anchor_of(targets: List[OutputState]) -> Optional[OutputState]:
        """The monitor the virtual display's position is remembered against.

        The primary, or failing that the leftmost lit screen — anything, so long
        as it is chosen the same way every time.
        """
        lit = [o for o in targets if o.enabled]
        if not lit:
            return None
        for out in lit:
            if out.primary or out.priority == 1:
                return out
        return min(lit, key=lambda o: (o.x, o.y, o.name))

    def place_vdd(self, mode: Mode, targets: List[OutputState],
                  placement: Optional[dict] = None) -> tuple:
        """Where to put the virtual display, and how far to zoom it.

        The user's monitors are not ours to move.  At dual time they are either
        already lit in front of the user — in which case that *is* the desk, zoom
        changes and all — or they are coming back from a snapshot that is
        internally consistent because it was captured all at once.  Either way the
        only thing left to decide is where the streaming display goes.

        Its position is remembered as an offset from a monitor rather than an
        absolute coordinate, and that is the whole trick.  Compositors renormalise
        a layout after every apply (KDE slides the top-left corner back to 0,0), so
        an absolute coordinate does not survive the session that recorded it.  An
        offset from a screen the user can see does: "below the monitor" stays below
        the monitor whether or not the desk moved, and whether or not they changed
        its zoom.

        Even so, the offset is a memory, and the desk it was measured against may
        have changed shape underneath it — rescale a monitor and a display tucked
        under it no longer reaches its edge.  So the result is checked, and a
        placement that would leave the desktop overlapping or gapped is dropped in
        favour of one that works: hard against the right edge, which is always
        valid and never a surprise.
        """
        scale = float((placement or {}).get("scale") or 0) or None
        vdd = Rect(0, 0, int(mode.width / (scale or 1.0)), int(mode.height / (scale or 1.0)))
        lit = [o.rect for o in targets if o.enabled]
        anchor = self.anchor_of(targets)

        if placement and anchor is not None and placement.get("dx") is not None:
            # Prefer the monitor the offset was measured against; if it is gone,
            # any anchor beats refusing to remember anything at all.
            base = next((o for o in targets
                         if o.enabled and o.name == placement.get("anchor")), anchor)
            x = base.x + int(placement["dx"])
            y = base.y + int(placement["dy"] or 0)
            if is_coherent(lit + [Rect(x, y, vdd.w, vdd.h)]):
                return x, y, scale
            log.debug("remembered VDD offset no longer fits the desk — snapping right")

        if anchor is None:  # headless: it is the only display
            return 0, 0, scale
        return self.rightmost_edge(targets), anchor.y, scale

    @staticmethod
    def offset_from_anchor(vdd: OutputState, targets: List[OutputState]) -> dict:
        """Record the VDD's spot as an offset from a monitor, for `place_vdd`."""
        anchor = LayoutBackend.anchor_of([o for o in targets if o.name != vdd.name])
        if anchor is None:
            return {}
        return {"anchor": anchor.name,
                "dx": vdd.x - anchor.x,
                "dy": vdd.y - anchor.y}


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
