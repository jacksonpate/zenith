"""VDD providers: ways to make a virtual display exist.

Ordered per environment; the first provider that probes available wins.
Each provider answers:

    probe(env, runner)   -> (available, human reason)      [read-only]
    ensure(env, runner)  -> try to become available (load module, install
                            package). Only invoked from `zenith-display
                            setup` — never during a stream handshake.
    create(env, runner, mode) -> exact output name (or prefix hint) that the
                            layout backend will see
    destroy(env, runner, state) -> tear down what create() made

Providers never arrange displays — that's the layout backend's job.
"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

from ..modes import Mode
from ..runner import Runner


class VddProvider:
    name = "abstract"
    description = ""

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        raise NotImplementedError

    def ensure(self, env, runner: Runner) -> bool:
        """Attempt to make the provider available; True if probe should be retried."""
        return False

    def create(self, env, runner: Runner, mode: Mode) -> str:
        """Bring the VDD into existence; return output name or prefix hint."""
        raise NotImplementedError

    def destroy(self, env, runner: Runner, state: dict) -> None:
        pass

    def vdd_outputs(self, env, runner: Runner) -> Set[str]:
        """Outputs present right now that are virtual displays, not real monitors.

        Anyone asking "is a monitor the user could actually look at lit?" needs
        this.  A VDD leaked by a crashed session is not one — and counting it as
        one is how `dual` ends up relighting a ghost and parking a second VDD
        beside it while the desk stays dark.

        The default covers every provider that fabricates a DRM connector.
        Compositor-native providers invent their own names and must say so.
        """
        return {c.name for c in env.vdd_connectors}


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
    chain: List[VddProvider] = []

    # Compositor-native first where the session offers one: no kernel module, no
    # root, and the display is theirs to create and destroy.
    if env.session_type == "wayland":
        if "hyprland" in desktop or env.tools.get("hyprctl"):
            chain.append(HyprlandProvider())
        if "sway" in desktop or env.tools.get("swaymsg"):
            chain.append(SwayProvider())
        if "kde" in desktop:
            chain.append(KwinVirtualProvider())

    # A spare port, if the machine has one. It beats a fabricated DRM device on
    # every axis that matters: the display lives on the GPU that will encode it
    # (a foreign device's buffer cannot be imported by the encoder — that is an
    # evdi VDD streaming as a black screen), and it needs no kernel module, so
    # no DKMS, no akmod, and no Secure Boot key for the user to enrol.
    chain.append(DrmDebugfsProvider())

    # Then evdi, for the machines with no port to spare — a laptop with every
    # output in use. It fabricates a display from nothing, at the cost of an
    # out-of-tree module.
    chain.append(EvdiProvider())

    # A forced connector is permanent hardware state: an EDID pinned to a real
    # port at boot. Nothing creates it and nothing can destroy it — `restore`
    # can only switch the output off, which is why it leaves a ghost monitor in
    # the display settings between sessions, and why there can only ever be one.
    # It stays supported for machines already provisioned that way; it is not
    # something a new machine should land on.
    chain.append(ForcedConnectorProvider())
    return chain


def choose(env, runner: Runner, bootstrap: bool = False):
    """Walk the chain; returns (provider, report) where report lists decisions.

    ``bootstrap=True`` (used by `zenith-display setup` only) lets a provider run
    its ensure() — module loads, package installs, helper + sudoers install — and
    only while no earlier provider has already qualified, so a machine that can
    borrow a spare port never gets a kernel module installed behind its back.

    Setup runs ensure() *whether or not* probe already passes, and that is the
    whole point rather than a detail. Setup runs as root; streaming does not. A
    provider can be perfectly usable by root and useless to the user who will
    actually start the stream — drm-debugfs is exactly that, because root writes
    the two kernel files directly while everyone else needs the helper and its
    sudoers rule. Gate ensure() on probe() and setup asks root "can you do this?",
    hears "yes", installs nothing, and reports success. The user then finds no
    helper and falls through to a kernel module, on hardware that needed none.

    So: ensure() is what setup is *for*. It is idempotent; call it.
    """
    report = []
    selected: Optional[VddProvider] = None
    for provider in chain_for(env):
        ok, reason = provider.probe(env, runner)
        if bootstrap and selected is None:
            provider.ensure(env, runner)
            ok, reason = provider.probe(env, runner)
        report.append({"provider": provider.name, "available": ok, "reason": reason,
                       "reboot_required": getattr(provider, "reboot_required", False)})
        if ok and selected is None:
            selected = provider
    return selected, report
