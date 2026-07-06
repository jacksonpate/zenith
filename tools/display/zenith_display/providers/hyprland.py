"""Provider: Hyprland runtime headless outputs (``hyprctl output create``)."""

from __future__ import annotations

from typing import Tuple

from ..modes import Mode
from ..runner import Runner
from . import VddProvider

_OUTPUT_NAME = "zenith-vdd"


class HyprlandProvider(VddProvider):
    name = "hyprland"
    description = "Hyprland headless output (hyprctl output create)"

    def probe(self, env) -> Tuple[bool, str]:
        if not env.tools.get("hyprctl"):
            return False, "hyprctl not installed"
        res = Runner().run(["hyprctl", "version"], timeout=5)
        if res.ok:
            return True, "hyprland IPC reachable"
        return False, "no live hyprland session"

    def create(self, env, runner: Runner, mode: Mode) -> str:
        runner.run(
            ["hyprctl", "output", "create", "headless", _OUTPUT_NAME],
            timeout=10, check=True,
        )
        # Mode/position are applied by the wlr layout backend afterwards.
        return _OUTPUT_NAME

    def destroy(self, env, runner: Runner, state: dict) -> None:
        runner.run(
            ["hyprctl", "output", "remove", state.get("vdd_output", _OUTPUT_NAME)],
            timeout=10,
        )
