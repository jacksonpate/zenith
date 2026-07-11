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
        res = runner.query(["swaymsg", "-t", "get_outputs", "--raw"], timeout=5)
        if not res.ok:
            return set()
        try:
            return {o["name"] for o in json.loads(res.stdout)}
        except (ValueError, KeyError, TypeError):
            return set()

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        if not env.tools.get("swaymsg"):
            return False, "swaymsg not installed"
        if runner.query(["swaymsg", "-t", "get_version"], timeout=5).ok:
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

    def vdd_outputs(self, env, runner: Runner) -> Set[str]:
        # sway names every headless output HEADLESS-N and hands out a fresh N
        # each time, so a VDD leaked by a crashed session is *guaranteed* not to
        # match the current one by name. Match on what they all are instead.
        return {n for n in self._output_names(runner) if n.startswith("HEADLESS-")}
