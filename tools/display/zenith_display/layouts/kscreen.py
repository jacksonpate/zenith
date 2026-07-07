"""KDE Plasma layout backend (kscreen-doctor).

Generalizes the original silverblue script: no hardcoded connector names,
snapshot/restore covers every output, and mode selection falls back through
the client's refresh -> 120 -> 60 -> the output's preferred mode.
"""

from __future__ import annotations

import json
from typing import List

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

    def apply_headless(self, vdd: str, mode: Mode) -> None:
        args = [f"output.{vdd}.enable", f"output.{vdd}.priority.1"]
        args += self._set_mode_args(vdd, mode, self._vdd_modes(vdd))
        for out in self.outputs():
            if out.name != vdd and out.connected and out.enabled:
                args.append(f"output.{out.name}.disable")
        self.runner.run(["kscreen-doctor", *args], timeout=15, check=True)

    def apply_dual(self, vdd: str, mode: Mode) -> None:
        edge = self.rightmost_edge([o for o in self.outputs() if o.name != vdd])
        args = [
            f"output.{vdd}.enable",
            f"output.{vdd}.priority.2",
            f"output.{vdd}.position.{edge},0",
        ]
        args += self._set_mode_args(vdd, mode, self._vdd_modes(vdd))
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
