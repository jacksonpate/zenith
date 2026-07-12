"""Provider: KWin runtime virtual outputs (Plasma 6, probed via DBus).

KWin gained a DBus surface for creating virtual outputs on recent Plasma;
availability differs by version, so everything is discovered at runtime and
this provider simply reports unavailable on older Plasma — the chain then
falls through to EVDI or a forced connector.
"""

from __future__ import annotations

from typing import Optional, Set, Tuple

from ..modes import Mode
from ..runner import Runner
from . import VddProvider

_CANDIDATE_PATHS = ("/VirtualOutputs", "/org/kde/KWin/VirtualOutputs")
_OUTPUT_NAME = "zenith-vdd"


class KwinVirtualProvider(VddProvider):
    name = "kwin-virtual"
    description = "KWin runtime virtual output (DBus)"

    _path: Optional[str] = None  # cached across probe/create/destroy

    def _find_interface(self, runner: Runner) -> Optional[str]:
        if self._path:
            return self._path
        for path in _CANDIDATE_PATHS:
            res = runner.query(
                ["gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                 "--object-path", path],
                timeout=5,
            )
            if res.ok and "addOutput" in res.stdout:
                self._path = path
                return path
        return None

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        if not env.tools.get("gdbus"):
            return False, "gdbus not installed"
        path = self._find_interface(runner)
        if path:
            return True, f"KWin addOutput at {path}"
        return False, "this KWin exposes no virtual-output DBus API"

    def create(self, env, runner: Runner, mode: Mode) -> str:
        path = self._find_interface(runner)
        if not path:
            raise RuntimeError("KWin virtual-output API disappeared between probe and create")
        res = runner.run(
            ["gdbus", "call", "--session", "--dest", "org.kde.KWin",
             "--object-path", path, "--method", "org.kde.KWin.VirtualOutputs.addOutput",
             _OUTPUT_NAME, str(mode.width), str(mode.height), "1.0"],
            timeout=10,
        )
        if not res.ok:  # try the (name, w, h) arity
            runner.run(
                ["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                 "--object-path", path, "--method", "org.kde.KWin.VirtualOutputs.addOutput",
                 _OUTPUT_NAME, str(mode.width), str(mode.height)],
                timeout=10, check=True,
            )
        return _OUTPUT_NAME

    def destroy(self, env, runner: Runner, state: dict) -> None:
        path = self._find_interface(runner)
        if path:
            runner.run(
                ["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                 "--object-path", path, "--method", "org.kde.KWin.VirtualOutputs.removeOutput",
                 state.get("vdd_output", _OUTPUT_NAME)],
                timeout=10,
            )

    def vdd_outputs(self, env, runner: Runner) -> Set[str]:
        return {_OUTPUT_NAME}
