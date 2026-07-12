"""Provider: a pre-existing forced connector VDD (the original fleet trick).

Machines provisioned with an EDID-forced connector (NVIDIA ``CustomEDID`` /
kernel ``drm.edid_firmware``) permanently expose a connector whose EDID says
``ZenithVDD``.  Nothing to create or destroy — we simply hand its name to the
layout backend.  Highest priority: if an operator went to the trouble of
provisioning one, it is the intended VDD.
"""

from __future__ import annotations

import os
from typing import Tuple

from ..modes import Mode
from ..runner import Runner
from . import VddProvider


class ForcedConnectorProvider(VddProvider):
    name = "forced-connector"
    description = "pre-provisioned EDID-forced connector (ZenithVDD)"

    @staticmethod
    def _provisioned(env) -> list:
        """VDD connectors that are actually permanent hardware state.

        An evdi connector keeps the ZenithVDD EDID we wrote to it long after
        its session is over, so it looks exactly like a provisioned one — but it
        is dead without a holder process feeding it, and this provider has no
        way to start one.  Claiming it (from the front of the chain, no less)
        would hand every session after the first a display that can never
        produce a frame.  It belongs to the evdi provider; leave it there.
        """
        return [c for c in env.vdd_connectors if c.driver != "evdi"]

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        override = os.environ.get("ZENITH_VDD_OUTPUT")
        if override:
            return True, f"ZENITH_VDD_OUTPUT={override}"
        vdds = self._provisioned(env)
        if vdds:
            return True, f"found {', '.join(c.name for c in vdds)}"
        if env.vdd_connectors:
            return False, "the only ZenithVDD connector is evdi's — that is evdi's to drive"
        return False, "no connector with a ZenithVDD EDID"

    def create(self, env, runner: Runner, mode: Mode) -> str:
        override = os.environ.get("ZENITH_VDD_OUTPUT")
        if override:
            return override
        return self._provisioned(env)[0].name

    # destroy(): intentionally nothing — the connector is permanent hardware
    # state; restore() re-disabling it via the layout backend is the teardown.
