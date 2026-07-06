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

    def probe(self, env) -> Tuple[bool, str]:
        override = os.environ.get("ZENITH_VDD_OUTPUT")
        if override:
            return True, f"ZENITH_VDD_OUTPUT={override}"
        vdds = env.vdd_connectors
        if vdds:
            return True, f"found {', '.join(c.name for c in vdds)}"
        return False, "no connector with a ZenithVDD EDID"

    def create(self, env, runner: Runner, mode: Mode) -> str:
        override = os.environ.get("ZENITH_VDD_OUTPUT")
        if override:
            return override
        return env.vdd_connectors[0].name

    # destroy(): intentionally nothing — the connector is permanent hardware
    # state; restore() re-disabling it via the layout backend is the teardown.
