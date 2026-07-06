"""X11 layout backend (xrandr) — covers Cinnamon, XFCE, MATE, KDE-on-X11…

Unlike Wayland backends, X11 can attach a brand-new modeline to any output,
so this backend also knows how to inject the client's CVT-RB mode.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..modes import Mode, cvt_rb
from . import LayoutBackend, OutputState

_HEAD_RE = re.compile(
    r"^(?P<name>\S+) (?P<status>connected|disconnected)"
    r"(?P<primary> primary)?"
    r"(?: (?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+))?"
)
_MODE_RE = re.compile(r"^\s+(?P<w>\d+)x(?P<h>\d+)i?\s+(?P<rates>.+)$")


class XrandrBackend(LayoutBackend):
    name = "xrandr"

    def _query(self) -> str:
        res = self.runner.run(["xrandr", "--query"], timeout=10)
        if not res.ok:
            raise RuntimeError(f"xrandr --query failed: {res.stderr.strip()}")
        return res.stdout

    def parse(self, text: str) -> List[OutputState]:
        outs: List[OutputState] = []
        current: Optional[OutputState] = None
        for line in text.splitlines():
            head = _HEAD_RE.match(line)
            if head:
                current = OutputState(
                    name=head.group("name"),
                    connected=head.group("status") == "connected",
                    enabled=head.group("w") is not None,
                    width=int(head.group("w") or 0),
                    height=int(head.group("h") or 0),
                    x=int(head.group("x") or 0),
                    y=int(head.group("y") or 0),
                    primary=bool(head.group("primary")),
                )
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
        return outs

    def outputs(self) -> List[OutputState]:
        return self.parse(self._query())

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

    def apply_headless(self, vdd: str, mode: Mode) -> None:
        mode_name = self.ensure_mode(vdd, mode)
        args = ["xrandr", "--output", vdd, "--mode", mode_name, "--pos", "0x0", "--primary"]
        for out in self.outputs():
            if out.name != vdd and out.enabled:
                args += ["--output", out.name, "--off"]
        self.runner.run(args, timeout=15, check=True)

    def apply_dual(self, vdd: str, mode: Mode) -> None:
        mode_name = self.ensure_mode(vdd, mode)
        edge = self.rightmost_edge([o for o in self.outputs() if o.name != vdd])
        self.runner.run(
            ["xrandr", "--output", vdd, "--mode", mode_name, "--pos", f"{edge}x0"],
            timeout=15,
            check=True,
        )

    def restore(self, payload: dict) -> None:
        args = ["xrandr"]
        for out in payload.get("outputs", []):
            args += ["--output", out["name"]]
            if out.get("enabled") and out.get("mode"):
                args += ["--mode", out["mode"], "--pos", f"{out.get('x', 0)}x{out.get('y', 0)}"]
                if out.get("refresh"):
                    args += ["--rate", str(out["refresh"])]
                if out.get("primary"):
                    args += ["--primary"]
            else:
                args += ["--off"]
        self.runner.run(args, timeout=15, check=True)
