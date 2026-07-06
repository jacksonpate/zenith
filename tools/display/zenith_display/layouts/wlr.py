"""wlroots layout backend (wlr-randr) — Sway, Hyprland, river, Wayfire…

Any compositor implementing wlr-output-management is driven uniformly here;
creating the headless output itself is compositor-specific (see the sway and
hyprland providers).
"""

from __future__ import annotations

import json
from typing import List

from ..modes import Mode
from . import LayoutBackend, OutputState


class WlrBackend(LayoutBackend):
    name = "wlr"

    def _query(self) -> list:
        res = self.runner.run(["wlr-randr", "--json"], timeout=10)
        if not res.ok:
            raise RuntimeError(f"wlr-randr --json failed: {res.stderr.strip()}")
        return json.loads(res.stdout)

    def outputs(self) -> List[OutputState]:
        outs = []
        for raw in self._query():
            current = next((m for m in raw.get("modes", []) if m.get("current")), None)
            outs.append(
                OutputState(
                    name=raw.get("name", ""),
                    enabled=bool(raw.get("enabled")),
                    width=(current or {}).get("width", 0),
                    height=(current or {}).get("height", 0),
                    refresh=(current or {}).get("refresh", 0.0),
                    x=raw.get("position", {}).get("x", 0),
                    y=raw.get("position", {}).get("y", 0),
                    scale=float(raw.get("scale", 1.0)),
                    modes=[
                        f"{m.get('width')}x{m.get('height')}@{round(m.get('refresh', 0))}"
                        for m in raw.get("modes", [])
                    ],
                )
            )
        return outs

    def snapshot(self) -> dict:
        outputs = []
        for out in self.outputs():
            outputs.append(
                {
                    "name": out.name,
                    "enabled": out.enabled,
                    "mode": f"{out.width}x{out.height}@{out.refresh:.3f}" if out.enabled else None,
                    "x": out.x,
                    "y": out.y,
                    "scale": out.scale,
                }
            )
        return {"outputs": outputs}

    def _enable_args(self, vdd: str, mode: Mode, x: int) -> List[str]:
        return [
            "wlr-randr",
            "--output", vdd,
            "--on",
            "--custom-mode", f"{mode.width}x{mode.height}@{mode.refresh}Hz",
            "--pos", f"{x},0",
        ]

    def apply_headless(self, vdd: str, mode: Mode) -> None:
        self.runner.run(self._enable_args(vdd, mode, 0), timeout=15, check=True)
        for out in self.outputs():
            if out.name != vdd and out.enabled:
                self.runner.run(["wlr-randr", "--output", out.name, "--off"], timeout=15, check=True)

    def apply_dual(self, vdd: str, mode: Mode) -> None:
        edge = self.rightmost_edge([o for o in self.outputs() if o.name != vdd])
        self.runner.run(self._enable_args(vdd, mode, edge), timeout=15, check=True)

    def restore(self, payload: dict) -> None:
        for out in payload.get("outputs", []):
            args = ["wlr-randr", "--output", out["name"]]
            if out.get("enabled") and out.get("mode"):
                args += [
                    "--on",
                    "--mode", out["mode"],
                    "--pos", f"{out.get('x', 0)},{out.get('y', 0)}",
                    "--scale", str(out.get("scale", 1.0)),
                ]
            else:
                args += ["--off"]
            self.runner.run(args, timeout=15)
