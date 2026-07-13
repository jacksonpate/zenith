"""wlroots layout backend — Sway, Hyprland, river, Wayfire…

Two transports, picked at runtime:

* **sway** (``swaymsg``): JSON IPC has been stable for a decade and ships
  wherever sway does, so it is preferred when a sway socket answers.
* **wlr-randr**: for every other wlr-output-management compositor.  Old
  distro builds predate ``--json``, so parsing failures degrade loudly.
"""

from __future__ import annotations

import json
from typing import Iterable, List, Optional

from ..modes import Mode
from . import LayoutBackend, OutputState


class WlrBackend(LayoutBackend):
    name = "wlr"

    _use_sway: Optional[bool] = None

    def _sway(self) -> bool:
        if self._use_sway is None:
            self._use_sway = self.runner.query(["swaymsg", "-t", "get_version"], timeout=5).ok
        return self._use_sway

    # -- state ------------------------------------------------------------

    def outputs(self) -> List[OutputState]:
        return self._outputs_sway() if self._sway() else self._outputs_wlr_randr()

    def _outputs_sway(self) -> List[OutputState]:
        res = self.runner.query(["swaymsg", "-t", "get_outputs", "--raw"], timeout=10)
        if not res.ok:
            raise RuntimeError(f"swaymsg get_outputs failed: {res.stderr.strip()}")
        outs = []
        for raw in json.loads(res.stdout or "[]"):
            current = raw.get("current_mode") or {}
            outs.append(
                OutputState(
                    name=raw.get("name", ""),
                    enabled=bool(raw.get("active")),
                    width=current.get("width", 0),
                    height=current.get("height", 0),
                    refresh=(current.get("refresh", 0) or 0) / 1000.0,  # sway: mHz
                    x=raw.get("rect", {}).get("x", 0),
                    y=raw.get("rect", {}).get("y", 0),
                    scale=float(raw.get("scale") or 1.0),
                    modes=[
                        f"{m.get('width')}x{m.get('height')}@{round((m.get('refresh', 0) or 0) / 1000.0)}"
                        for m in raw.get("modes", [])
                    ],
                )
            )
        return outs

    def _outputs_wlr_randr(self) -> List[OutputState]:
        res = self.runner.query(["wlr-randr", "--json"], timeout=10)
        if not res.ok:
            raise RuntimeError(
                "wlr-randr --json failed (build too old?) and no sway IPC: "
                + res.stderr.strip()
            )
        outs = []
        for raw in json.loads(res.stdout):
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
                    "width": out.width,
                    "height": out.height,
                    "refresh": round(out.refresh) if out.refresh else 0,
                    "x": out.x,
                    "y": out.y,
                    "scale": out.scale,
                }
            )
        return {"outputs": outputs}

    # -- mutations ----------------------------------------------------------

    def _enable(self, name: str, mode: Mode, x: int, y: int = 0,
                scale: Optional[float] = None) -> None:
        if self._sway():
            # `--` stops swaymsg's own getopt from eating the command's --custom.
            argv = ["swaymsg", "--", "output", name, "enable", "mode", "--custom",
                    f"{mode.width}x{mode.height}@{mode.refresh}Hz",
                    "position", str(x), str(y)]
            if scale:
                argv += ["scale", str(scale)]
        else:
            argv = ["wlr-randr", "--output", name, "--on",
                    "--custom-mode", f"{mode.width}x{mode.height}@{mode.refresh}Hz",
                    "--pos", f"{x},{y}"]
            if scale:
                argv += ["--scale", str(scale)]
        self.runner.run(argv, timeout=15, check=True)

    def _disable(self, name: str) -> None:
        if self._sway():
            self.runner.run(["swaymsg", "output", name, "disable"], timeout=15, check=True)
        else:
            self.runner.run(["wlr-randr", "--output", name, "--off"], timeout=15, check=True)

    def apply_headless(self, vdd: str, mode: Mode,
                       placement: Optional[dict] = None) -> None:
        # Position is meaningless when it is the only display; the zoom is not.
        self._enable(vdd, mode, 0, 0, (placement or {}).get("scale"))
        for out in self.outputs():
            if out.name != vdd and out.enabled:
                self._disable(out.name)

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None,
                   placement: Optional[dict] = None) -> None:
        """Relight the user's monitors, then put the VDD where they left it —
        entering dual from a headless session, the monitors are all off."""
        targets = self.dual_targets(vdd, baseline)
        for out in targets:
            if out.enabled:
                self._enable(out.name, Mode(out.width, out.height, round(out.refresh) or 60),
                             out.x, out.y, out.scale)
            else:
                self._disable(out.name)
        x, y, scale = self.place_vdd(mode, targets, placement)
        self._enable(vdd, mode, x, y, scale)

    def relight(self, vdds: Iterable[str] = ()) -> None:
        outs = self.outputs()
        monitors = [o for o in outs if o.connected and o.name not in vdds]
        if not monitors:
            return  # nothing real to fall back to — never blank the only display
        # No mode given: the compositor falls back to the output's preferred one.
        for out in monitors:
            if out.enabled:
                continue
            if self._sway():
                self.runner.run(["swaymsg", "output", out.name, "enable"], timeout=15, check=True)
            else:
                self.runner.run(["wlr-randr", "--output", out.name, "--on"],
                                timeout=15, check=True)
        for out in outs:
            if out.name in vdds and out.enabled:
                self._disable(out.name)

    def restore(self, payload: dict) -> None:
        for out in payload.get("outputs", []):
            if out.get("enabled") and out.get("width"):
                mode = Mode(out["width"], out["height"], out.get("refresh") or 60)
                self._enable(out["name"], mode, out.get("x", 0), out.get("y", 0))
            else:
                self._disable(out["name"])
