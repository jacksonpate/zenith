"""GNOME Wayland layout backend (Mutter DisplayConfig DBus via GObject).

python3-gi ships with every GNOME desktop, so requiring it here costs users
nothing; we still probe the import so `plan` degrades gracefully elsewhere.
Configs are applied with the *temporary* method — a crashed session reverts
on its own, which is exactly the failure mode we want.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from ..modes import Mode
from . import LayoutBackend, OutputState

log = logging.getLogger("zenith-display")

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

    def apply_headless(self, vdd: str, mode: Mode,
                       placement: Optional[dict] = None) -> None:  # pragma: no cover
        monitors = self._state().unpack()[1]
        want = float((placement or {}).get("scale") or 1.0)
        # Mutter validates scale per-resolution and rejects the whole config if
        # the two disagree, so the mode and the scale are resolved together.
        mode_id, scale = self._resolve(monitors, vdd, mode, want)
        if not mode_id:
            raise RuntimeError(f"no usable mode on {vdd}")
        self._apply([self._logical(0, 0, scale, True, vdd, mode_id)])

    def _resolve(self, monitors, connector: str, mode: Optional[Mode],
                 scale: float = 1.0):  # pragma: no cover
        """A mode id for `connector`, and a scale that mode actually permits.

        Mutter validates scale *per resolution* and rejects the entire config if
        they disagree — and `_mode_id` may quietly fall back to the preferred
        mode when the one asked for is gone, desyncing the two.  Resolve them
        together or not at all.
        """
        for (conn, *_id), modes, _props in monitors:
            if conn != connector:
                continue
            chosen = None
            if mode:
                for refresh in (mode.refresh, 120, 60):
                    chosen = next((m for m in modes if m[1] == mode.width
                                   and m[2] == mode.height and round(m[3]) == refresh), None)
                    if chosen:
                        break
            if chosen is None:
                chosen = next((m for m in modes if m[6].get("is-preferred")),
                              modes[0] if modes else None)
            if chosen is None:
                return None, scale
            supported = [float(s) for s in chosen[5]] or [1.0]
            return chosen[0], min(supported, key=lambda s: abs(s - (scale or 1.0)))
        return None, scale

    def apply_dual(self, vdd: str, mode: Mode, baseline: Optional[dict] = None,
                   placement: Optional[dict] = None) -> None:  # pragma: no cover
        """An output left out of ApplyMonitorsConfig is an output that is *off*,
        so a dual entered from headless must name the user's monitors again —
        reading them from the current state would only find the VDD.

        Mutter takes the config as a whole or not at all: one unknown monitor,
        one gap between logical monitors, one bad scale, no primary — and the
        user stays in headless.  `dual_targets` guarantees the layout is packed
        and has a primary; everything named here is checked against what mutter
        currently reports.
        """
        monitors = self._state().unpack()[1]
        vdd_mode_id, _scale = self._resolve(monitors, vdd, mode)
        if not vdd_mode_id:
            raise RuntimeError(f"no usable mode on {vdd}")

        targets = self._anchored(self.dual_targets(vdd, baseline))
        try:
            self._apply(self._layout(monitors, targets, vdd, vdd_mode_id, mode, placement))
        except Exception as exc:
            # Mutter rejects a config whole: a gap between logical monitors (we
            # dropped one it no longer has), an unusable scale, an arrangement it
            # dislikes. Rather than strand the user in headless, lay the same
            # monitors out plainly, left to right, and drop the remembered spot
            # for the virtual display — try once more with nothing to argue about.
            log.warning("mutter rejected the dual layout (%s); retrying packed", exc)
            self._apply(self._layout(monitors, self._packed(targets, repack=True),
                                     vdd, vdd_mode_id, mode, None))

    def _layout(self, monitors, targets, vdd: str, vdd_mode_id: str,
                mode: Optional[Mode] = None,
                placement: Optional[dict] = None):  # pragma: no cover
        layout = []
        for out in targets:
            if not out.enabled:
                continue  # omitted from the layout == off
            want = Mode(out.width, out.height, round(out.refresh) or 60) if out.width else None
            mode_id, scale = self._resolve(monitors, out.name, want, out.scale)
            if not mode_id:
                continue  # mutter no longer has it; naming it would sink the config
            layout.append(self._logical(out.x, out.y, scale, out.primary, out.name, mode_id))

        if mode is None:
            x, y, vdd_scale = self.rightmost_edge(targets), 0, 1.0
        else:
            x, y, want_scale = self.place_vdd(mode, targets, placement)
            _id, vdd_scale = self._resolve(monitors, vdd, mode, want_scale or 1.0)
        layout.append(self._logical(x, y, vdd_scale or 1.0, False, vdd, vdd_mode_id))
        return layout

    @staticmethod
    def _anchored(targets):
        """Mutter requires the logical layout to start at (0,0) — X11 and KDE do
        not, so a perfectly good baseline can hold negative coordinates."""
        lit = [o for o in targets if o.enabled]
        if lit:
            dx, dy = min(o.x for o in lit), min(o.y for o in lit)
            for out in lit:
                out.x, out.y = out.x - dx, out.y - dy
        return targets

    def relight(self, vdds: Iterable[str] = ()) -> None:  # pragma: no cover
        # Rebuild the layout from scratch at preferred modes, left to right. The
        # VDDs need no explicit teardown here: an output missing from the config
        # is an output off.
        monitors = self._state().unpack()[1]
        layout = []
        edge = 0
        for (connector, *_id), modes, _props in monitors:
            if connector in vdds or not modes:
                continue
            preferred = next((m for m in modes if m[6].get("is-preferred")), modes[0])
            layout.append(self._logical(edge, 0, 1.0, not layout, connector, preferred[0]))
            edge += preferred[1]
        if layout:  # no real monitor to fall back to — never blank the only output
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
