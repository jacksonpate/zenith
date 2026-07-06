"""VDD providers: ways to make a virtual display exist.

Ordered per environment; the first provider that probes available (after a
best-effort ``ensure()`` bootstrap) wins.  Each provider answers:

    probe(env)          -> (available, human reason)
    ensure(env, runner) -> try to become available (load module, install pkg)
    create(env, runner, mode) -> output-name hint the layout backend will see
    destroy(env, runner, state) -> tear down what create() made

Providers never arrange displays — that's the layout backend's job.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..modes import Mode
from ..runner import Runner


class VddProvider:
    name = "abstract"
    description = ""

    def probe(self, env) -> Tuple[bool, str]:
        raise NotImplementedError

    def ensure(self, env, runner: Runner) -> bool:
        """Attempt to make the provider available; True if probe should be retried."""
        return False

    def create(self, env, runner: Runner, mode: Mode) -> str:
        """Bring the VDD into existence; return output name or prefix hint."""
        raise NotImplementedError

    def destroy(self, env, runner: Runner, state: dict) -> None:
        pass


def _classes():
    from .drm_debugfs import DrmDebugfsProvider
    from .evdi import EvdiProvider
    from .forced_connector import ForcedConnectorProvider
    from .hyprland import HyprlandProvider
    from .kwin_virtual import KwinVirtualProvider
    from .sway import SwayProvider

    return (ForcedConnectorProvider, HyprlandProvider, SwayProvider,
            KwinVirtualProvider, EvdiProvider, DrmDebugfsProvider)


def get_provider(name: str) -> Optional[VddProvider]:
    """Instantiate a provider by name — used when restoring a snapshot."""
    for cls in _classes():
        if cls.name == name:
            return cls()
    return None


def chain_for(env) -> List[VddProvider]:
    """Ordered provider candidates for this environment."""
    (ForcedConnectorProvider, HyprlandProvider, SwayProvider,
     KwinVirtualProvider, EvdiProvider, DrmDebugfsProvider) = _classes()

    desktop = env.desktop
    chain: List[VddProvider] = [ForcedConnectorProvider()]  # existing fleet VDDs first

    if env.session_type == "wayland":
        if "hyprland" in desktop or env.tools.get("hyprctl"):
            chain.append(HyprlandProvider())
        if "sway" in desktop or env.tools.get("swaymsg"):
            chain.append(SwayProvider())
        if "kde" in desktop:
            chain.append(KwinVirtualProvider())

    chain.append(EvdiProvider())
    chain.append(DrmDebugfsProvider())
    return chain


def choose(env, runner: Runner, bootstrap: bool = True):
    """Walk the chain; returns (provider, report) where report lists decisions."""
    report = []
    selected: Optional[VddProvider] = None
    for provider in chain_for(env):
        ok, reason = provider.probe(env)
        if not ok and bootstrap and provider.ensure(env, runner):
            ok, reason = provider.probe(env)
        report.append({"provider": provider.name, "available": ok, "reason": reason})
        if ok and selected is None:
            selected = provider
    return selected, report
