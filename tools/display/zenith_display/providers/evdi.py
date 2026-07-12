"""Provider: EVDI virtual display (the universal Linux fallback).

EVDI (the DisplayLink kernel module) fabricates a *real* DRM display device,
so every display server — X11, KWin, Mutter, wlroots — sees an ordinary
monitor hot-plug.  Zenith acts as the EVDI userspace client: it connects a
generated EDID carrying exactly the client's mode, then holds the connection
open for the lifetime of the session (see ``evdi_hold.py``).

Privilege model: the package loads evdi at boot (modules-load.d) and a udev
rule makes ``/sys/devices/evdi/add`` session-writable, so the normal path
needs **no privileges at stream time**.  Root/passwordless-sudo is only a
fallback for hand-rolled installs, and package installation happens solely
in ``zenith-display setup``.
"""

from __future__ import annotations

import ctypes.util
import glob
import os
import shlex
import signal
import sys
import time
from typing import List, Optional, Tuple

from .. import edid as edid_mod
from ..detect import scan_connectors
from ..modes import Mode
from ..runner import Runner, which
from ..snapshot import state_dir
from . import VddProvider

_PACKAGE_CANDIDATES = {
    "apt-get": ["evdi-dkms"],
    "dnf": ["akmod-evdi", "kmod-evdi", "evdi"],
    "pacman": ["evdi-dkms"],
    "zypper": ["evdi"],
}

_ADD_PATH = "/sys/devices/evdi/add"


def _module_loaded() -> bool:
    return os.path.isdir("/sys/module/evdi")


def _libevdi() -> Optional[str]:
    return ctypes.util.find_library("evdi")


def _add_writable() -> bool:
    return os.access(_ADD_PATH, os.W_OK)


def _evdi_cards() -> list:
    """DRM card indices whose driver is evdi."""
    cards = []
    for card in glob.glob("/sys/class/drm/card[0-9]*"):
        if "-" in os.path.basename(card):
            continue  # connector, not card
        driver = os.path.realpath(os.path.join(card, "device", "driver"))
        if os.path.basename(driver) == "evdi":
            cards.append(int(os.path.basename(card)[4:]))
    return sorted(cards)


def _connector_names() -> set:
    return {c.name for c in scan_connectors()}


def _card_connectors(card: int) -> list:
    """(connector name, status) for one evdi card."""
    found = []
    for path in sorted(glob.glob(f"/sys/class/drm/card{card}-*")):
        name = os.path.basename(path).split("-", 1)[1]  # card1-DVI-I-1 -> DVI-I-1
        try:
            with open(os.path.join(path, "status"), encoding="utf-8") as fh:
                found.append((name, fh.read().strip()))
        except OSError:
            found.append((name, "unknown"))
    return found


def _connected_connector(card: int) -> Optional[str]:
    return next((n for n, status in _card_connectors(card) if status == "connected"), None)


def _idle_evdi_cards() -> list:
    """EVDI cards that exist but have nothing plugged into them.

    An evdi card cannot be removed individually — the module only offers
    ``remove_all``, which would also yank a user's real DisplayLink dock — so a
    card added for a session outlives it.  Adding a fresh one per session grows
    the list without bound (a day of streaming leaves dozens behind); an idle
    one is exactly as good as a new one, so take that instead.
    """
    return [card for card in _evdi_cards() if _connected_connector(card) is None]


class EvdiProvider(VddProvider):
    name = "evdi"
    description = "EVDI virtual DRM display (universal fallback)"

    def _root_wrap(self, env, argv: List[str]) -> List[str]:
        return argv if env.is_root else ["sudo", "-n", *argv]

    def probe(self, env, runner: Runner) -> Tuple[bool, str]:
        if not _libevdi():
            return False, "libevdi not found (run `zenith-display setup`)"
        if not _module_loaded():
            if env.is_root or env.has_passwordless_sudo:
                return False, "evdi module not loaded (run `zenith-display setup`)"
            return False, "evdi module not loaded and no privileges to load it"
        if _add_writable() or env.is_root or env.has_passwordless_sudo:
            return True, "evdi module loaded" + (" (device add writable)" if _add_writable() else "")
        return False, "evdi loaded but /sys/devices/evdi/add is not writable (run `zenith-display setup`)"

    def ensure(self, env, runner: Runner) -> bool:
        """Full bootstrap — only ever called from `zenith-display setup`."""
        if not (env.is_root or env.has_passwordless_sudo):
            return False
        if not _module_loaded():
            if not runner.run(self._root_wrap(env, ["modprobe", "evdi"]), timeout=20).ok:
                # Module absent entirely: try the distro package, then modprobe again.
                for pm, packages in _PACKAGE_CANDIDATES.items():
                    if not which(pm):
                        continue
                    for pkg in packages:
                        install = {
                            "apt-get": [pm, "install", "-y", pkg],
                            "dnf": [pm, "install", "-y", pkg],
                            "pacman": [pm, "-S", "--noconfirm", pkg],
                            "zypper": [pm, "-n", "install", pkg],
                        }[pm]
                        if runner.run(self._root_wrap(env, install), timeout=600).ok:
                            if runner.run(self._root_wrap(env, ["modprobe", "evdi"]), timeout=20).ok:
                                break
                    break  # only ever one native package manager
        if _module_loaded() and not _add_writable():
            runner.run(self._root_wrap(env, ["chmod", "0666", _ADD_PATH]), timeout=5)
        return _module_loaded()

    def _spawn_holder(self, env, runner: Runner, card: int, edid_path: str, pidfile: str, area: int) -> None:
        pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base = [
            sys.executable, "-m", "zenith_display.providers.evdi_hold",
            "--card", str(card),
            "--edid", edid_path,
            "--pidfile", pidfile,
            "--area-limit", str(area),
        ]
        if env.is_root or _add_writable():
            # Normal path: run as ourselves; PYTHONPATH via environment.
            holder = base
            child_env = dict(os.environ)
            child_env["PYTHONPATH"] = pkg_root + os.pathsep + child_env.get("PYTHONPATH", "")
        else:
            # sudo strips PYTHONPATH (env_reset/env_delete); set it inside the
            # elevated shell instead so the module import always works.
            inner = "PYTHONPATH=%s exec %s" % (
                shlex.quote(pkg_root), " ".join(shlex.quote(a) for a in base))
            holder = ["sudo", "-n", "sh", "-c", inner]
            child_env = dict(os.environ)
        if runner.dry_run:
            runner.trace.append(holder)
            return
        import subprocess

        subprocess.Popen(
            holder, env=child_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def create(self, env, runner: Runner, mode: Mode) -> str:
        # EDID first: if the mode is un-representable we fail before touching
        # the system (no leaked DRM device).
        edid_path = os.path.join(state_dir(), "vdd.edid")
        edid_bytes = edid_mod.generate(mode)
        with open(edid_path, "wb") as fh:
            fh.write(edid_bytes)

        cards_before = set(_evdi_cards())

        # An evdi card outlives the session that made it — the module has no
        # per-device remove — so take an idle one back rather than minting
        # another. Otherwise every stream leaves a card behind for good.
        card = next(iter(_idle_evdi_cards()), None)
        if card is None:
            add_cmd = ["sh", "-c", f"echo 1 > {_ADD_PATH}"]
            if not _add_writable():
                add_cmd = self._root_wrap(env, add_cmd)
            runner.run(add_cmd, timeout=10, check=True)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and card is None:
                new = sorted(set(_evdi_cards()) - cards_before)
                if new:
                    card = new[0]
                else:
                    time.sleep(0.2)
        if card is None:
            if runner.dry_run:
                return "DVI-I-"
            raise RuntimeError("evdi device did not appear after add")

        pidfile = os.path.join(state_dir(), "evdi-hold.pid")
        self._spawn_holder(env, runner, card, edid_path, pidfile,
                           area=mode.width * mode.height)

        # Watch OUR card's own connectors for one coming up. Diffing sysfs for a
        # *newly appeared* name would be wrong twice over: a recycled card's
        # connector already exists (merely disconnected, so nothing new ever
        # appears and a working display reads as a failure), and a monitor
        # plugged in at the wrong moment would be mistaken for ours.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            connector = _connected_connector(card)
            if connector:
                return connector
            time.sleep(0.25)
        if runner.dry_run:
            return "DVI-I-"
        raise RuntimeError(f"evdi card{card} never reported a connected output "
                           "(the holder could not attach the EDID)")

    def destroy(self, env, runner: Runner, state: dict) -> None:
        pidfile = os.path.join(state_dir(), "evdi-hold.pid")
        try:
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return
        killed = False
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except PermissionError:
            # Holder runs as root (sudo spawn path): kill with the same wrap.
            killed = runner.run(self._root_wrap(env, ["kill", "-TERM", str(pid)]), timeout=5).ok
        except ProcessLookupError:
            killed = True  # already gone
        except OSError:
            pass
        if killed:
            try:
                os.unlink(pidfile)
            except OSError:
                pass
            # Wait for the connector to actually drop. The kernel unplugs it a
            # beat after the holder dies, and a card whose connector still reads
            # "connected" does not look idle — so the next session concludes it
            # has none to recycle and adds another, which is the leak all over
            # again, just slower.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if not any(_connected_connector(c) for c in _evdi_cards()):
                    break
                time.sleep(0.1)
        # On failure the pidfile is kept so a later (privileged) restore can retry.
