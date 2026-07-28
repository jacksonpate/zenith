"""Provider: a spare connector, borrowed for the length of a session.

Write a generated EDID into ``/sys/kernel/debug/dri/*/<connector>/edid_override``
and force the connector on via its sysfs ``status``.  The compositor sees a
monitor hot-plug whose only mode is exactly what the client asked for; setting
the status back to ``detect`` makes it vanish again — not disabled, *gone*.

Where a machine has a free port this beats every other provider, and it is worth
being precise about why:

* **The display lives on the GPU that will encode it.**  A fabricated DRM device
  (evdi) does not, and KMS capture can only import a buffer from the same GPU as
  the encoder — so on any discrete-GPU machine an evdi VDD streams as a black
  screen, and via the compositor's own capture path it streams with repaint
  artifacts.  A borrowed connector has neither problem: it is an ordinary output
  on the ordinary card.
* **No kernel module.**  No DKMS, no akmod, no COPR, no AUR — and no Secure Boot
  key enrolment, which is a step most users will never complete.
* **No reboot.**  The old way of forcing a connector was a kernel command line
  (``drm.edid_firmware=``), which meant provisioning a machine and rebooting it,
  and left the display present forever afterwards.

The cost: it needs a disconnected connector to borrow.  A laptop with every port
occupied has none, and that is where evdi still earns its place.

Root is required to write the two kernel files, but *only* those two — so the
work is done by a tiny helper (``zenith-drm-vdd``) installed by
``zenith-display setup`` with a NOPASSWD sudoers rule scoped to it alone.  A
blanket ``sudo sh -c 'cat > /sys/...'`` would be both unrunnable under such a
rule and a hole wide enough to write any file on the system.
"""

from __future__ import annotations

import glob
import os
import shutil
from typing import List, Optional, Tuple

from .. import edid as edid_mod
from ..modes import Mode
from ..runner import Runner
from ..snapshot import state_dir
from . import VddProvider

_HELPER_NAMES = ("zenith-drm-vdd",)
_HELPER_DIRS = ("/usr/local/bin", "/usr/libexec/zenith", "/usr/bin")


def _helper() -> Optional[str]:
    """The privileged helper, if it is installed.

    The only thing on this path that needs root, kept as small as it can be: it
    validates the connector name and touches nothing but that connector's own
    two kernel files.
    """
    for name in _HELPER_NAMES:
        found = shutil.which(name)
        if found:
            return found
        for directory in _HELPER_DIRS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
    return None

# Never borrow internal panels or fake outputs (same policy as
# scripts/zenith-vdd-setup suggest()): forcing an EDID onto these either
# fights the laptop panel or targets a virtual GPU with no real scanout.
_EXCLUDED_NAME_PREFIXES = ("eDP", "LVDS", "DSI", "Writeback")
_EXCLUDED_DRIVERS = {"bochs-drm", "bochs", "virtio_gpu", "vkms", "cirrus", "qxl", "evdi"}


class DrmDebugfsProvider(VddProvider):
    name = "drm-debugfs"
    description = "EDID override + forced status on a spare connector (root, beta)"

    @staticmethod
    def _debugfs_dir(connector_name: str) -> Optional[str]:
        for path in glob.glob(f"/sys/kernel/debug/dri/*/{connector_name}"):
            return path
        return None

    def _pick_connector(self, env):
        def borrowable(c):
            return (not c.name.startswith(_EXCLUDED_NAME_PREFIXES)
                    and (c.driver or "") not in _EXCLUDED_DRIVERS)

        # Which GPU will encode. A spare port on *that* card is worth more than
        # any other property of a connector, because it is the difference between
        # a zero-copy capture and one that round-trips every frame through system
        # memory: KMS capture can only import a buffer from the encoder's own GPU,
        # and a dma-buf from a foreign GPU cannot enter CUDA at all.
        #
        # Left unranked this is decided by connector name, which on a hybrid
        # laptop reliably picks the wrong card — the iGPU's DP-1 sorts before the
        # dGPU's DP-6, so the display lands on the GPU that is not encoding and
        # the stream quietly drops to memory buffers. That survives 1080p and
        # falls apart at tablet resolutions, where it reads as "the host is too
        # slow" rather than "the display is on the wrong card".
        encoder_card = getattr(env, "encoder_card", "") or ""

        def affinity(c):
            return 0 if encoder_card and c.card == encoder_card else 1

        # A connector we forced up in an earlier session and never let go of —
        # it is still "connected", carrying the EDID we wrote to it.
        #
        # Reclaim it. Leaving it be looks harmless and is not: a forced connector
        # is not a *spare* connector, so this provider stands aside, and the one
        # that picks it up instead is the one whose teardown is a deliberate no-op
        # (it believes the connector is permanent hardware). So it never gets torn
        # down, which means it is still there next session, which means the same
        # thing happens again. Once a machine falls into that loop it never leaves:
        # the display stops being spawned per client and becomes a stale fixture
        # showing the last client's resolution.
        ours = [c for c in env.vdd_connectors if borrowable(c)]
        reclaimable = {id(c) for c in ours}
        spares = ours + [c for c in env.disconnected_connectors if borrowable(c)]
        spares.sort(key=lambda c: (
            affinity(c),  # the encoder's own GPU, above all else
            0 if id(c) in reclaimable else 1,  # reclaim ours before taking a new port
            0 if c.name.startswith("DP") else 1,  # DP tolerates forced EDIDs best
            c.name,
        ))
        return spares[0] if spares else None

    def _cross_gpu(self, env, spare) -> bool:
        """Is the only port we can borrow on a GPU that will not encode?

        Not a failure — the stream works, and on a machine whose every dGPU port
        is occupied it is the only option there is. It is a *cost*, and one worth
        naming, because it is invisible from the outside and looks exactly like a
        host that cannot keep up.
        """
        encoder_card = getattr(env, "encoder_card", "") or ""
        return bool(spare and encoder_card and spare.card and spare.card != encoder_card)

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        spare = self._pick_connector(env)
        if not spare:
            return False, "no borrowable disconnected connector"
        # The helper is required even for root — `_run` drives it either way — so
        # "I am root, therefore I can do this" was never true, merely plausible.
        # It made `sudo zenith-display setup` report a provider it had not
        # installed, and left the user, who is not root, with nothing.
        if not _helper():
            return False, "the privileged helper is not installed (run `sudo zenith-display setup`)"
        if self._cross_gpu(env, spare):
            return True, (f"can borrow {spare.name}, but it is on {spare.card} "
                          f"({spare.driver or 'unknown'}) and {env.encoder_card} encodes "
                          f"— every frame will cross GPUs through system memory")
        return True, f"can borrow {spare.name}"

    def ensure(self, env, runner: Runner) -> bool:
        """Install the helper and the one sudoers rule that lets it run.

        Only ever called from `zenith-display setup`. This is what makes the
        spare-connector path plug-and-play: nothing to package, nothing to sign,
        no reboot — a script and a single scoped NOPASSWD line.
        """
        if not (env.is_root or env.has_passwordless_sudo):
            return False
        if _helper():
            return True

        source = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "helpers", "zenith-drm-vdd")
        if not os.path.isfile(source):
            return False

        target = "/usr/local/bin/zenith-drm-vdd"
        wrap = [] if env.is_root else ["sudo", "-n"]
        if not runner.run([*wrap, "install", "-m", "0755", source, target], timeout=20).ok:
            return False

        # Scoped to this one command. A blanket rule here would be a root
        # file-write primitive for every user on the machine.
        rule = f"ALL ALL=(root) NOPASSWD: {target}\n"
        rule_path = "/etc/sudoers.d/zenith-drm-vdd"
        tmp = os.path.join(state_dir(), "zenith-drm-vdd.sudoers")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(rule)
        # visudo -c refuses to install a file that would break sudo entirely.
        if not runner.run([*wrap, "visudo", "-c", "-f", tmp], timeout=10).ok:
            return False
        if not runner.run([*wrap, "install", "-m", "0440", tmp, rule_path], timeout=10).ok:
            return False
        return _helper() is not None

    def _run(self, env, runner: Runner, args: List[str]) -> None:
        """Drive the helper — the only command the sudoers rule permits."""
        helper = _helper()
        if helper is None:
            raise RuntimeError("zenith-drm-vdd is not installed (run `sudo zenith-display setup`)")
        argv = [helper, *args] if env.is_root else ["sudo", "-n", helper, *args]
        runner.run(argv, timeout=10, check=True)

    def create(self, env, runner: Runner, mode: Mode) -> str:
        spare = self._pick_connector(env)
        if not spare:
            raise RuntimeError("spare connector vanished between probe and create")

        edid_path = os.path.join(state_dir(), "vdd.edid")
        with open(edid_path, "wb") as fh:
            fh.write(edid_mod.generate(mode))

        self._run(env, runner, ["on", spare.name, edid_path])
        return spare.name

    def destroy(self, env, runner: Runner, state: dict) -> None:
        output = state.get("vdd_output")
        if not output:
            return
        self._run(env, runner, ["off", output])
