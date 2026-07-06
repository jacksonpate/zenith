"""GNOME Wayland layout backend (Mutter DisplayConfig DBus via GObject).

python3-gi ships with every GNOME desktop, so requiring it here costs users
nothing; we still probe the import so `plan` degrades gracefully elsewhere.
Configs are applied with the *temporary* method — a crashed session reverts
on its own, which is exactly the failure mode we want.
"""

from __future__ import annotations

from typing import List, Optional

from ..modes import Mode
from . import LayoutBackend, OutputState

try:  # pragma: no cover - exercised only on GNOME machines
    from gi.repository import Gio, GLib

    _GI = True
except Exception:  # ImportError and gi's own failures
    _GI = False

_BUS = "org.gnome.Mutter.DisplayConfig"
_PATH = "/org/gnome/Mutter/DisplayConfig"
_METHOD_TEMPORARY = 1


def available() -> bool:
    return _GI


class GnomeBackend(LayoutBackend):
    name = "gnome"

    def _proxy(self):  # pragma: no cover - live DBus
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, _BUS, _PATH, _BUS, None
        )

    def _state(self):  # pragma: no cover - live DBus
        return self._proxy().call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, 30_000, None)

    def outputs(self) -> List[OutputState]:  # pragma: no cover - live DBus
        _serial, monitors, logical, _props = self._state().unpack()
        placed = {}
        for x, y, scale, _transform, primary, mons, _props in logical:
            for connector, *_ in mons:
                placed[connector] = (x, y, scale, primary)
        outs = []
        for (connector, *_id), modes, _props in monitors:
            current = next((m for m in modes if m[6].get("is-current", False)), None)
            x, y, scale, primary = placed.get(connector, (0, 0, 1.0, False))
            outs.append(
                OutputState(
                    name=connector,
                    enabled=connector in placed,
                    width=current[1] if current else 0,
                    height=current[2] if current else 0,
                    refresh=current[3] if current else 0.0,
                    x=x, y=y, scale=scale, primary=primary,
                    modes=[f"{m[1]}x{m[2]}@{round(m[3])}" for m in modes],
                )
            )
        return outs

    def _mode_id(self, monitors, connector: str, mode: Optional[Mode]) -> Optional[str]:
        for (conn, *_id), modes, _props in monitors:
            if conn != connector:
                continue
            if mode:
                for refresh in (mode.refresh, 120, 60):
                    for m in modes:
                        if m[1] == mode.width and m[2] == mode.height and round(m[3]) == refresh:
                            return m[0]
            preferred = next((m for m in modes if m[6].get("is-preferred")), modes[0] if modes else None)
            return preferred[0] if preferred else None
        return None

    def _apply(self, logical_layout) -> None:  # pragma: no cover - live DBus
        serial = self._state().unpack()[0]
        variant = GLib.Variant(
            "(uua(iiduba(ssa{sv}))a{sv})",
            (serial, _METHOD_TEMPORARY, logical_layout, {}),
        )
        self._proxy().call_sync("ApplyMonitorsConfig", variant, Gio.DBusCallFlags.NONE, 30_000, None)

    def _logical(self, x: int, y: int, scale: float, primary: bool, connector: str, mode_id: str):
        return (x, y, scale, 0, primary, [(connector, mode_id, {})])

    def apply_headless(self, vdd: str, mode: Mode) -> None:  # pragma: no cover
        monitors = self._state().unpack()[1]
        mode_id = self._mode_id(monitors, vdd, mode)
        if not mode_id:
            raise RuntimeError(f"no usable mode on {vdd}")
        self._apply([self._logical(0, 0, 1.0, True, vdd, mode_id)])

    def apply_dual(self, vdd: str, mode: Mode) -> None:  # pragma: no cover
        monitors = self._state().unpack()[1]
        mode_id = self._mode_id(monitors, vdd, mode)
        if not mode_id:
            raise RuntimeError(f"no usable mode on {vdd}")
        layout = []
        edge = 0
        for out in self.outputs():
            if out.enabled and out.name != vdd:
                current = self._mode_id(monitors, out.name, None)
                layout.append(self._logical(out.x, out.y, out.scale, out.primary, out.name, current))
                edge = max(edge, out.x + int(out.width / (out.scale or 1.0)))
        layout.append(self._logical(edge, 0, 1.0, False, vdd, mode_id))
        self._apply(layout)

    def snapshot(self) -> dict:
        outputs = []
        for out in self.outputs():
            outputs.append(
                {
                    "name": out.name,
                    "enabled": out.enabled,
                    "mode": f"{out.width}x{out.height}@{round(out.refresh)}" if out.enabled else None,
                    "x": out.x, "y": out.y,
                    "scale": out.scale,
                    "primary": out.primary,
                }
            )
        return {"outputs": outputs}

    def restore(self, payload: dict) -> None:  # pragma: no cover
        monitors = self._state().unpack()[1]
        layout = []
        for out in payload.get("outputs", []):
            if not out.get("enabled"):
                continue
            mode = None
            if out.get("mode"):
                wh, refresh = out["mode"].split("@")
                w, h = wh.split("x")
                mode = Mode(int(w), int(h), int(refresh))
            mode_id = self._mode_id(monitors, out["name"], mode)
            if mode_id:
                layout.append(
                    self._logical(out.get("x", 0), out.get("y", 0), out.get("scale", 1.0),
                                  out.get("primary", False), out["name"], mode_id)
                )
        if layout:
            self._apply(layout)
