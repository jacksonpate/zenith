"""X11 layout backend (xrandr) — covers Cinnamon, XFCE, MATE, KDE-on-X11…

Unlike Wayland backends, X11 can attach a brand-new modeline to any output,
so this backend also knows how to inject the client's CVT-RB mode.

It also has to do something no Wayland compositor needs: *adopt* a virtual
display once a provider has made one.  A Wayland compositor picks up a new DRM
card by itself; X11 does not.  It sees the card, lists it as a PRIME provider —
and leaves its outputs invisible to `xrandr -q` until they are explicitly
sourced from the primary GPU.  Without that step evdi, the only provider a
stock machine has, produces a display the session can never see.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional, Tuple

from ..modes import Mode, cvt_rb
from . import LayoutBackend, OutputState

log = logging.getLogger("zenith-display")

_HEAD_RE = re.compile(
    r"^(?P<name>\S+) (?P<status>connected|disconnected)"
    r"(?P<primary> primary)?"
    r"(?: (?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+))?"
    r"(?: (?P<rotation>normal|left|right|inverted))?"
)
_MODE_RE = re.compile(r"^\s+(?P<w>\d+)x(?P<h>\d+)i?\s+(?P<rates>.+)$")
_PROVIDER_RE = re.compile(r"^Provider (?P<idx>\d+): id: (?P<id>0x[0-9a-f]+) cap: (?P<cap>0x[0-9a-f]+)")

_SWAPPING_ROTATIONS = ("left", "right")

# RandR provider capability bits (randr.h).
_CAP_SOURCE_OUTPUT = 0x1  # can drive another provider's outputs — the GPU
_CAP_SINK_OUTPUT = 0x2    # has outputs that need driving — the virtual display


class XrandrBackend(LayoutBackend):
    name = "xrandr"

    def _query(self) -> str:
        res = self.runner.query(["xrandr", "--query"], timeout=10)
        if not res.ok:
            raise RuntimeError(f"xrandr --query failed: {res.stderr.strip()}")
        return res.stdout

    def parse(self, text: str) -> List[OutputState]:
        outs: List[OutputState] = []
        current: Optional[OutputState] = None
        rotations = {}
        for line in text.splitlines():
            head = _HEAD_RE.match(line)
            if head:
                rotation = head.group("rotation") or "normal"
                width = int(head.group("w") or 0)
                height = int(head.group("h") or 0)
                # xrandr reports post-rotation geometry; store the panel's
                # native (mode) orientation so mode names stay real.
                if rotation in _SWAPPING_ROTATIONS:
                    width, height = height, width
                current = OutputState(
                    name=head.group("name"),
                    connected=head.group("status") == "connected",
                    enabled=head.group("w") is not None,
                    width=width,
                    height=height,
                    x=int(head.group("x") or 0),
                    y=int(head.group("y") or 0),
                    primary=bool(head.group("primary")),
                    rotation=rotation,
                )
                rotations[current.name] = rotation
                outs.append(current)
                continue
            mode = _MODE_RE.match(line)
            if mode and current is not None:
                for rate in re.findall(r"(\d+(?:\.\d+)?)(\*?)\+?", mode.group("rates")):
                    value, is_current = rate
                    label = f"{mode.group('w')}x{mode.group('h')}@{round(float(value))}"
                    if label not in current.modes:
                        current.modes.append(label)
                    if is_current:
                        current.refresh = float(value)
        self._rotations = rotations
        return outs

    def outputs(self) -> List[OutputState]:
        return self.parse(self._query())

    def _providers(self) -> List[Tuple[str, int, int]]:
        """(provider id, capability bits, associated-provider count)."""
        res = self.runner.query(["xrandr", "--listproviders"], timeout=10)
        if not res.ok:
            return []
        found = []
        for line in res.stdout.splitlines():
            m = _PROVIDER_RE.match(line.strip())
            if m:
                assoc = re.search(r"associated providers: (\d+)", line)
                found.append((m.group("id"), int(m.group("cap"), 16),
                              int(assoc.group(1)) if assoc else 0))
        return found

    def attach_new_outputs(self) -> None:
        """Make a freshly-created virtual display visible to the X session.

        evdi fabricates a separate DRM *card*, and X11 — unlike every Wayland
        compositor — does not adopt one on its own.  It lists the card as a
        PRIME provider whose outputs belong to nobody, and `xrandr -q` never
        shows them until they are sourced from the GPU.  This is the step that
        makes the difference between the display existing and the session being
        able to use it, and evdi is the only provider a stock machine has.
        """
        providers = self._providers()
        source = next((pid for pid, cap, _a in providers if cap & _CAP_SOURCE_OUTPUT), None)
        # A real GPU reports Sink Output too (cap 0xf) — attaching it to itself
        # is nonsense — and a sink already associated with a source is adopted.
        sinks = [pid for pid, cap, assoc in providers
                 if cap & _CAP_SINK_OUTPUT and pid != source and not assoc]
        if not sinks:
            return  # nothing to adopt — the VDD is an ordinary connector

        if source is None:
            # Nothing on this machine can drive it. Say so plainly: the
            # alternative is the caller timing out and reporting only that the
            # display "never appeared", which sends people looking in entirely
            # the wrong place.
            log.error("this GPU advertises no PRIME Source Output capability, so X11 "
                      "cannot drive a virtual display attached to it (providers: %s)",
                      ", ".join(f"{p}:cap={c:#x}" for p, c, _a in providers) or "none")
            return

        for sink in sinks:
            res = self.runner.run(["xrandr", "--setprovideroutputsource", sink, source],
                                  timeout=10)
            if not res.ok:
                log.warning("could not attach provider %s to %s: %s",
                            sink, source, res.stderr.strip())

    def snapshot(self) -> dict:
        outputs = []
        for out in self.outputs():
            if not out.connected:
                continue
            outputs.append(
                {
                    "name": out.name,
                    "enabled": out.enabled,
                    "mode": f"{out.width}x{out.height}" if out.enabled else None,
                    "refresh": round(out.refresh) if out.refresh else None,
                    "rotation": out.rotation,
                    "x": out.x,
                    "y": out.y,
                    "primary": out.primary,
                }
            )
        return {"outputs": outputs}

    def ensure_mode(self, vdd: str, mode: Mode) -> str:
        """Create + attach the client modeline; returns the mode name."""
        modeline = cvt_rb(mode).xrandr_modeline()
        name = modeline[0]
        self.runner.run(["xrandr", "--newmode", *modeline], timeout=10)  # EEXIST is fine
        self.runner.run(["xrandr", "--addmode", vdd, name], timeout=10, check=True)
        return name

    def apply_headless(self, vdd: str, mode: Mode,
                       placement: Optional[dict] = None) -> None:
        mode_name = self.ensure_mode(vdd, mode)
        args = ["xrandr", "--output", vdd, "--mode", mode_name, "--pos", "0x0", "--primary"]
        # X has no per-output scale; --scale zooms the framebuffer, which is not
        # the same thing and would blur the stream. Zoom is a Wayland luxury here.
        for out in self.outputs():
            if out.name != vdd and out.enabled:
                args += ["--output", out.name, "--off"]
        self.runner.run(args, timeout=15, check=True)

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None,
                   placement: Optional[dict] = None) -> None:
        """Restate the user's monitors, then add the VDD off the right edge.

        Coming out of headless they are all ``--off`` (and the VDD holds
        ``--primary``), so dual has to put them back rather than assume them.
        """
        mode_name = self.ensure_mode(vdd, mode)
        targets = self.dual_targets(vdd, baseline)
        args = ["xrandr"]
        for out in targets:
            args += ["--output", out.name]
            if not out.enabled:
                args += ["--off"]
                continue
            args += ["--mode", f"{out.width}x{out.height}", "--pos", f"{out.x}x{out.y}"]
            if out.refresh:
                args += ["--rate", str(round(out.refresh))]
            # An output that is off has no CRTC, so xrandr defaults it back to
            # RR_Rotate_0 unless told otherwise — headless turned the monitor
            # off, so without this a rotated monitor returns landscape.
            if out.rotation != "normal":
                args += ["--rotate", out.rotation]
            if out.primary:
                args += ["--primary"]
        x, y, _scale = self.place_vdd(mode, targets, placement)
        args += ["--output", vdd, "--mode", mode_name, "--pos", f"{x}x{y}"]
        self.runner.run(args, timeout=15, check=True)

    def relight(self, vdds: Iterable[str] = ()) -> None:
        outs = self.outputs()
        monitors = [o for o in outs if o.connected and o.name not in vdds]
        if not monitors:
            return  # nothing real to fall back to — never blank the only display
        # X forgets a disabled output's geometry entirely, so --auto (preferred
        # mode, sensible placement) is the best guess available.
        args = []
        for out in monitors:
            if not out.enabled:
                args += ["--output", out.name, "--auto"]
        for out in outs:
            if out.name in vdds and out.enabled:
                args += ["--output", out.name, "--off"]
        if args:
            self.runner.run(["xrandr", *args], timeout=15, check=True)

    def restore(self, payload: dict) -> None:
        """Replay per-output so one bad entry can't strand the whole layout."""
        failures = []
        for out in payload.get("outputs", []):
            args = ["xrandr", "--output", out["name"]]
            if out.get("enabled") and out.get("mode"):
                args += ["--mode", out["mode"], "--pos", f"{out.get('x', 0)}x{out.get('y', 0)}"]
                if out.get("refresh"):
                    args += ["--rate", str(out["refresh"])]
                if out.get("rotation") and out["rotation"] != "normal":
                    args += ["--rotate", out["rotation"]]
                if out.get("primary"):
                    args += ["--primary"]
            else:
                args += ["--off"]
            res = self.runner.run(args, timeout=15)
            if not res.ok:
                failures.append(f"{out['name']}: {res.stderr.strip()}")
        if failures:
            raise RuntimeError("restore incomplete: " + "; ".join(failures))
