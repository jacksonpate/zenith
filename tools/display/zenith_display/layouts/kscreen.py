"""KDE Plasma layout backend (kscreen-doctor).

Generalizes the original silverblue script: no hardcoded connector names,
snapshot/restore covers every output, and mode selection falls back through
the client's refresh -> 120 -> 60 -> the output's preferred mode.
"""

from __future__ import annotations

import json
from typing import Iterable, List, Optional

from ..modes import Mode
from . import LayoutBackend, OutputState


class KScreenBackend(LayoutBackend):
    name = "kscreen"

    def _query(self) -> dict:
        res = self.runner.query(["kscreen-doctor", "-j"], timeout=10)
        if not res.ok:
            raise RuntimeError(f"kscreen-doctor -j failed: {res.stderr.strip()}")
        return json.loads(res.stdout)

    @staticmethod
    def _mode_str(output: dict) -> tuple:
        current = str(output.get("currentModeId", ""))
        for mode in output.get("modes", []):
            if str(mode.get("id")) == current:
                size = mode.get("size", {})
                return size.get("width", 0), size.get("height", 0), float(mode.get("refreshRate", 0.0))
        return 0, 0, 0.0

    def outputs(self) -> List[OutputState]:
        outs = []
        for raw in self._query().get("outputs", []):
            width, height, refresh = self._mode_str(raw)
            outs.append(
                OutputState(
                    name=raw.get("name", ""),
                    enabled=bool(raw.get("enabled")),
                    connected=bool(raw.get("connected")),
                    width=width,
                    height=height,
                    refresh=refresh,
                    x=raw.get("pos", {}).get("x", 0),
                    y=raw.get("pos", {}).get("y", 0),
                    scale=float(raw.get("scale", 1.0)),
                    priority=int(raw.get("priority", 0)),
                    modes=[m.get("name", "") for m in raw.get("modes", [])],
                )
            )
        return outs

    def snapshot(self) -> dict:
        outputs = []
        for out in self.outputs():
            if not out.connected:
                continue
            outputs.append(
                {
                    "name": out.name,
                    "enabled": out.enabled,
                    "mode": f"{out.width}x{out.height}@{round(out.refresh)}" if out.width else None,
                    "x": out.x,
                    "y": out.y,
                    "scale": out.scale,
                    "priority": out.priority,
                }
            )
        return {"outputs": outputs}

    def _set_mode_args(self, vdd: str, mode: Mode, available: List[str]) -> List[str]:
        """Choose the best mode argument with graceful refresh fallback."""
        for refresh in (mode.refresh, 120, 60):
            wanted = f"{mode.width}x{mode.height}@{refresh}"
            if any(name.startswith(wanted) for name in available):
                return [f"output.{vdd}.mode.{wanted}"]
        # No exact geometry: let kscreen pick, callers logged the miss.
        return []

    def _vdd_modes(self, vdd: str) -> List[str]:
        for out in self.outputs():
            if out.name == vdd:
                return out.modes
        return []

    def apply_headless(self, vdd: str, mode: Mode,
                       placement: Optional[dict] = None) -> None:
        args = [f"output.{vdd}.enable", f"output.{vdd}.priority.1"]
        # Position is meaningless when it is the only display; the zoom is not.
        # Left alone, the compositor guesses a scale from a physical size the
        # virtual display does not have — and the guess is what makes the whole
        # desktop soft and oversized on a stream that is otherwise pixel-exact.
        if placement and placement.get("scale"):
            args.append(f"output.{vdd}.scale.{placement['scale']}")
        args += self._set_mode_args(vdd, mode, self._vdd_modes(vdd))
        for out in self.outputs():
            if out.name != vdd and out.connected and out.enabled:
                args.append(f"output.{out.name}.disable")
        self.runner.run(["kscreen-doctor", *args], timeout=15, check=True)

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None,
                   placement: Optional[dict] = None) -> None:
        """Hang the virtual display off a desk the user keeps.

        Dual is entered straight out of headless as often as from the desktop, so
        the monitors cannot be assumed to be on — but nor can they be assumed to
        need moving.  Those are two different situations and they get two different
        treatments:

        *The monitors are lit.*  Then the desk in front of the user is the desk,
        whatever they have done to it since the last session.  Restating a
        remembered mode, position and zoom onto a screen they are looking at is how
        an in-session zoom change gets silently undone — and how the rescaled screen
        stops reaching its neighbour, which is a gap, which KDE reverts.  Leave them
        alone.  Say nothing about them at all.

        *The desk is dark* (we came from headless).  Then it has to be rebuilt, and
        `dual_targets` rebuilds it from the snapshot as one coherent piece.

        Either way the monitors arrive self-consistent, and only the virtual display
        is left to position.  One kscreen-doctor call, so the compositor
        reconfigures once.
        """
        live = self.outputs()
        lit = [o for o in live if o.enabled and o.name != vdd]

        args: List[str] = []
        if lit:
            targets = lit  # the user's desk, exactly as they left it
        else:
            targets = [o for o in self.dual_targets(vdd, baseline) if o.name != vdd]
            for out in targets:
                if not out.enabled:
                    args.append(f"output.{out.name}.disable")
                    continue
                args.append(f"output.{out.name}.enable")
                if out.width:
                    args.append(
                        f"output.{out.name}.mode.{out.width}x{out.height}@{round(out.refresh)}")
                args.append(f"output.{out.name}.position.{out.x},{out.y}")
                if out.scale:
                    args.append(f"output.{out.name}.scale.{out.scale}")
                if out.priority:
                    args.append(f"output.{out.name}.priority.{out.priority}")

        x, y, scale = self.place_vdd(mode, targets, placement)
        last = max((o.priority for o in targets if o.enabled), default=1)
        args += [
            f"output.{vdd}.enable",
            f"output.{vdd}.priority.{last + 1}",  # never primary
            f"output.{vdd}.position.{x},{y}",
        ]
        if scale:
            args.append(f"output.{vdd}.scale.{scale}")
        # The zoom and the spot are the user's; the resolution is not theirs to
        # keep. That belongs to whoever is connecting — quit on a tablet, pick up
        # on a phone, and a remembered mode hands the phone the tablet's screen.
        args += self._set_mode_args(vdd, mode, self._vdd_modes(vdd))
        self.runner.run(["kscreen-doctor", *args], timeout=15, check=True)

    def relight(self, vdds: Iterable[str] = ()) -> None:
        outs = self.outputs()
        monitors = [o for o in outs if o.connected and o.name not in vdds]
        if not monitors:
            return  # nothing real to fall back to — never blank the only display
        # kscreen keeps an output's mode and position while it is disabled, so
        # a bare .enable puts it back where it was.
        args = [f"output.{o.name}.enable" for o in monitors if not o.enabled]
        args += [f"output.{o.name}.disable" for o in outs if o.name in vdds and o.enabled]
        if args:
            self.runner.run(["kscreen-doctor", *args], timeout=15, check=True)

    def restore(self, payload: dict) -> None:
        args: List[str] = []
        for out in payload.get("outputs", []):
            name = out["name"]
            if out.get("enabled"):
                args.append(f"output.{name}.enable")
                if out.get("mode"):
                    args.append(f"output.{name}.mode.{out['mode']}")
                args.append(f"output.{name}.position.{out.get('x', 0)},{out.get('y', 0)}")
                if out.get("scale"):
                    args.append(f"output.{name}.scale.{out['scale']}")
                if out.get("priority"):
                    args.append(f"output.{name}.priority.{out['priority']}")
            else:
                args.append(f"output.{name}.disable")
        if args:
            self.runner.run(["kscreen-doctor", *args], timeout=15, check=True)
