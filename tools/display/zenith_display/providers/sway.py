"""Provider: sway runtime headless outputs (``swaymsg create_output``)."""

from __future__ import annotations

import json
from typing import Set, Tuple

from ..modes import Mode
from ..runner import Runner
from . import VddProvider


class SwayProvider(VddProvider):
    name = "sway"
    description = "sway headless output (swaymsg create_output)"

    def _output_names(self, runner: Runner) -> Set[str]:
        res = runner.run(["swaymsg", "-t", "get_outputs", "--raw"], timeout=5)
        if not res.ok:
            return set()
        try:
            return {o["name"] for o in json.loads(res.stdout)}
        except (ValueError, KeyError, TypeError):
            return set()

    def probe(self, env) -> Tuple[bool, str]:
        if not env.tools.get("swaymsg"):
            return False, "swaymsg not installed"
        res = Runner().run(["swaymsg", "-t", "get_version"], timeout=5)
        if res.ok:
            return True, "sway IPC reachable"
        return False, "no live sway session"

    def create(self, env, runner: Runner, mode: Mode) -> str:
        before = self._output_names(runner)
        runner.run(["swaymsg", "create_output"], timeout=10, check=True)
        after = self._output_names(runner)
        created = sorted(after - before)
        if created:
            return created[0]
        return "HEADLESS-"  # dry-run / racy fallback: prefix hint

    def destroy(self, env, runner: Runner, state: dict) -> None:
        output = state.get("vdd_output")
        if output:
            runner.run(["swaymsg", "output", output, "unplug"], timeout=10)
