"""Provider: kernel-level EDID override on a spare connector (root required).

The generalization of the NVIDIA ``CustomEDID`` trick to every KMS driver:
write a generated EDID into ``/sys/kernel/debug/dri/*/<connector>/edid_override``
and force the connector on via its sysfs ``status``.  The compositor then
sees a hot-plugged "monitor" whose only mode is exactly what the client asked
for.  GPU-vendor agnostic (amdgpu, i915, nouveau, nvidia-drm), no packages —
but it needs a disconnected physical connector to borrow and root privileges,
so it sits near the end of the chain.  BETA.
"""

from __future__ import annotations

import glob
import os
from typing import Optional, Tuple

from .. import edid as edid_mod
from ..modes import Mode
from ..runner import Runner
from ..snapshot import state_dir
from . import VddProvider


class DrmDebugfsProvider(VddProvider):
    name = "drm-debugfs"
    description = "EDID override + forced status on a spare connector (root, beta)"

    @staticmethod
    def _debugfs_dir(connector_name: str) -> Optional[str]:
        for path in glob.glob(f"/sys/kernel/debug/dri/*/{connector_name}"):
            return path
        return None

    def _pick_connector(self, env):
        # Prefer DisplayPort connectors: most tolerant of forced EDIDs.
        spares = env.disconnected_connectors
        spares.sort(key=lambda c: (0 if c.name.startswith("DP") else 1, c.name))
        return spares[0] if spares else None

    def probe(self, env) -> Tuple[bool, str]:
        if not (env.is_root or env.has_passwordless_sudo):
            return False, "requires root (or passwordless sudo)"
        spare = self._pick_connector(env)
        if not spare:
            return False, "no disconnected connector to borrow"
        return True, f"can borrow {spare.name}"

    def _write(self, runner: Runner, env, path: str, data_file: str) -> None:
        if env.is_root:
            runner.run(["sh", "-c", f"cat '{data_file}' > '{path}'"], timeout=5, check=True)
        else:
            runner.run(["sudo", "-n", "sh", "-c", f"cat '{data_file}' > '{path}'"], timeout=5, check=True)

    def _write_str(self, runner: Runner, env, path: str, value: str) -> None:
        cmd = f"printf '%s' '{value}' > '{path}'"
        argv = ["sh", "-c", cmd] if env.is_root else ["sudo", "-n", "sh", "-c", cmd]
        runner.run(argv, timeout=5, check=True)

    def create(self, env, runner: Runner, mode: Mode) -> str:
        spare = self._pick_connector(env)
        if not spare:
            raise RuntimeError("spare connector vanished between probe and create")

        edid_path = os.path.join(state_dir(), "vdd.edid")
        with open(edid_path, "wb") as fh:
            fh.write(edid_mod.generate(mode))

        debugfs = self._debugfs_dir(spare.name)
        if debugfs:
            self._write(runner, env, os.path.join(debugfs, "edid_override"), edid_path)
        self._write_str(runner, env, os.path.join(spare.sysfs, "status"), "on")
        return spare.name

    def destroy(self, env, runner: Runner, state: dict) -> None:
        output = state.get("vdd_output")
        if not output:
            return
        for connector in env.connectors:
            if connector.name == output:
                debugfs = self._debugfs_dir(connector.name)
                if debugfs:
                    self._write_str(runner, env, os.path.join(debugfs, "edid_override"), "reset")
                self._write_str(runner, env, os.path.join(connector.sysfs, "status"), "detect")
                return
