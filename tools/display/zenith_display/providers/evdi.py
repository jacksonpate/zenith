"""Provider: EVDI virtual display (the universal Linux fallback).

EVDI (the DisplayLink kernel module) fabricates a *real* DRM display device,
so every display server — X11, KWin, Mutter, wlroots — sees an ordinary
monitor hot-plug.  Zenith acts as the EVDI userspace client: it connects a
generated EDID carrying exactly the client's mode, then holds the connection
open for the lifetime of the session (see ``evdi_hold.py``).

``ensure()`` bootstraps the module when missing: ``modprobe`` first, then a
best-effort distro-package install (``evdi-dkms`` & friends).  Needs root or
passwordless sudo for the module/device plumbing — probe reports exactly
what's missing so ``doctor`` can tell the user.
"""

from __future__ import annotations

import ctypes.util
import glob
import os
import signal
import sys
import time
from typing import Optional, Tuple

from .. import edid as edid_mod
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


def _module_loaded() -> bool:
    return os.path.isdir("/sys/module/evdi")


def _libevdi() -> Optional[str]:
    return ctypes.util.find_library("evdi")


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


class EvdiProvider(VddProvider):
    name = "evdi"
    description = "EVDI virtual DRM display (universal fallback)"

    def _root_wrap(self, env, argv: list) -> list:
        return argv if env.is_root else ["sudo", "-n", *argv]

    def probe(self, env) -> Tuple[bool, str]:
        if not (env.is_root or env.has_passwordless_sudo):
            return False, "requires root (or passwordless sudo) for module/device setup"
        if not _libevdi():
            return False, "libevdi not found (install libevdi/evdi package)"
        if _module_loaded():
            return True, "evdi module loaded"
        if which("modprobe"):
            return False, "evdi module not loaded (ensure() will try modprobe/install)"
        return False, "no modprobe available"

    def ensure(self, env, runner: Runner) -> bool:
        if not (env.is_root or env.has_passwordless_sudo):
            return False
        if _module_loaded():
            return True
        if runner.run(self._root_wrap(env, ["modprobe", "evdi"]), timeout=20).ok:
            return True
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
                        return True
            break  # only ever one native package manager
        return _module_loaded()

    def create(self, env, runner: Runner, mode: Mode) -> str:
        before = set(_evdi_cards())
        runner.run(
            self._root_wrap(env, ["sh", "-c", "echo 1 > /sys/devices/evdi/add"]),
            timeout=10, check=True,
        )
        card = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and card is None:
            new = sorted(set(_evdi_cards()) - before)
            if new:
                card = new[0]
            else:
                time.sleep(0.2)
        if card is None and not runner.dry_run:
            raise RuntimeError("evdi device did not appear after add")

        edid_path = os.path.join(state_dir(), "vdd.edid")
        with open(edid_path, "wb") as fh:
            fh.write(edid_mod.generate(mode))
        pidfile = os.path.join(state_dir(), "evdi-hold.pid")

        holder = self._root_wrap(env, [
            sys.executable, "-m", "zenith_display.providers.evdi_hold",
            "--card", str(card if card is not None else 0),
            "--edid", edid_path,
            "--pidfile", pidfile,
            "--area-limit", str(mode.width * mode.height),
        ])
        env_with_path = dict(os.environ)
        pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_with_path["PYTHONPATH"] = pkg_root + os.pathsep + env_with_path.get("PYTHONPATH", "")
        if not runner.dry_run:
            import subprocess

            subprocess.Popen(
                holder, env=env_with_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        # The connector materializes as DVI-I-N/DPI-N depending on kernel;
        # let the layout backend match by prefix on whatever appears.
        return "DVI-I-"

    def destroy(self, env, runner: Runner, state: dict) -> None:
        pidfile = os.path.join(state_dir(), "evdi-hold.pid")
        try:
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.unlink(pidfile)
        except OSError:
            pass
