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
import logging
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

log = logging.getLogger("zenith-display")

_PACKAGE_CANDIDATES = {
    "apt-get": ["evdi-dkms"],
    "dnf": ["akmod-evdi", "kmod-evdi", "evdi"],
    "pacman": ["evdi"],      # AUR-only in practice; the source build is the real path
    "zypper": ["evdi"],
}

# Where the module comes from when the distro does not ship one. Arch has it only
# in the AUR, which pacman cannot reach and which no unattended installer should
# be driving anyway; plenty of others have nothing at all. Pinned rather than
# tracking master: evdi follows the kernel's DRM API closely and a bad day to
# discover a breaking change is the day a user first clones the repo.
_SOURCE_URL = "https://github.com/DisplayLink/evdi"
_SOURCE_TAG = "v1.14.10"

# Build-time needs, per package manager. Without headers there is nothing to
# compile against, and the failure is a wall of make errors rather than a sentence.
_BUILD_DEPS = {
    "pacman": ["dkms", "base-devel", "git"],
    "apt-get": ["dkms", "build-essential", "git", "libdrm-dev"],
    "dnf": ["dkms", "gcc", "make", "git", "libdrm-devel", "kernel-devel"],
    "zypper": ["dkms", "gcc", "make", "git", "libdrm-devel"],
}

_ADD_PATH = "/sys/devices/evdi/add"

# Image-based Fedora (Silverblue, Kinoite, Bazzite). Ordered best-first: an
# akmod rebuilds itself against each new kernel, a plain kmod does not.
_OSTREE_PACKAGES = ["akmod-evdi", "kmod-evdi", "evdi"]


def _is_ostree() -> bool:
    """An image-based Fedora, where there is no dnf to install anything with.

    Without this the package loop below matches no package manager at all, and
    evdi is uninstallable — so the machine falls through to a provider that
    cannot create a display, or to nothing at all.
    """
    return os.path.exists("/run/ostree-booted") and which("rpm-ostree") is not None


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
    reboot_required = False  # set by _layer_on_ostree: installed, but not live yet

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
                # Module absent entirely: get it from the distro, then modprobe again.
                if _is_ostree():
                    self._layer_on_ostree(env, runner)
                else:
                    self._install_from_repo(env, runner)
        # Plenty of distros package no evdi at all — Arch has it only in the AUR,
        # which pacman cannot reach. "Clone the repo and it works" has to mean
        # something on those machines too, so build it.
        if not (_module_loaded() and _libevdi()):
            self._build_from_source(env, runner)
        if _module_loaded() and not _add_writable():
            runner.run(self._root_wrap(env, ["chmod", "0666", _ADD_PATH]), timeout=5)
        return bool(_module_loaded() and _libevdi())

    def _build_from_source(self, env, runner: Runner) -> None:
        """Build and install the module (via DKMS) and the userspace library.

        Both, because the provider needs both: DKMS gets the kernel module — and
        rebuilds it against every future kernel, which a one-off `make` would not
        — while the library is what Zenith actually dlopens to create a display.
        Install one without the other and setup reports success onto a machine
        that still cannot make a virtual display.
        """
        pm = next((p for p in _BUILD_DEPS if which(p)), None)
        if pm is None:
            log.warning("no known package manager — cannot install evdi's build deps")
            return
        if not which("dkms"):
            install = {"pacman": [pm, "-S", "--noconfirm", "--needed"],
                       "apt-get": [pm, "install", "-y"],
                       "dnf": [pm, "install", "-y"],
                       "zypper": [pm, "-n", "install"]}[pm]
            runner.run(self._root_wrap(env, [*install, *_BUILD_DEPS[pm]]), timeout=900)
        if not which("dkms"):
            log.warning("dkms is unavailable; evdi cannot be built here")
            return

        version = _SOURCE_TAG.lstrip("v")
        checkout = f"/usr/src/zenith-evdi-{version}"
        log.info("building evdi %s from source (no packaged module on this distro)", version)

        if not os.path.isdir(checkout):
            clone = ["git", "clone", "--depth", "1", "--branch", _SOURCE_TAG,
                     _SOURCE_URL, checkout]
            if not runner.run(self._root_wrap(env, clone), timeout=600).ok:
                log.warning("could not fetch the evdi source")
                return

        # DKMS wants dkms.conf at the top of /usr/src/<name>-<version>. evdi keeps
        # it one level down, under module/ — so the module subtree, not the repo,
        # is what gets handed over. Point DKMS at the repo root and it simply
        # reports that dkms.conf does not exist.
        src = f"/usr/src/evdi-{version}"
        if not os.path.isfile(f"{src}/dkms.conf"):
            copy = ["cp", "-rT", f"{checkout}/module", src]
            if not runner.run(self._root_wrap(env, copy), timeout=120).ok:
                log.warning("could not stage the evdi module tree for dkms")
                return

        runner.run(self._root_wrap(env, ["dkms", "add", "-m", "evdi", "-v", version]), timeout=60)
        built = runner.run(self._root_wrap(env, ["dkms", "install", "-m", "evdi",
                                                 "-v", version, "--force"]), timeout=1800)
        if not built.ok:
            log.warning("evdi failed to build against this kernel — are the kernel "
                        "headers for %s installed?", os.uname().release)
            return
        runner.run(self._root_wrap(env, ["modprobe", "evdi"]), timeout=30)

        if not _libevdi():
            self._install_library(env, runner, checkout)

    def _install_library(self, env, runner: Runner, src: str) -> None:
        """Build libevdi and put it where the dynamic linker will actually find it.

        `make` leaves a versioned `libevdi.so.N.M.K` sitting in the source tree,
        which `find_library` will never see. It needs the soname symlink and a
        cached ldconfig entry, or Zenith reports evdi ready and then fails to load
        it at the moment a user starts a stream.
        """
        if not runner.run(self._root_wrap(env, ["make", "-C", f"{src}/library"]), timeout=600).ok:
            log.warning("libevdi failed to build")
            return
        libdir = "/usr/lib64" if os.path.isdir("/usr/lib64") else "/usr/lib"
        built = sorted(glob.glob(f"{src}/library/libevdi.so*"))
        real = next((p for p in built if not os.path.islink(p)), None)
        if real is None:
            log.warning("libevdi built but produced no shared object")
            return
        runner.run(self._root_wrap(env, ["install", "-m", "0755", real,
                                         f"{libdir}/libevdi.so.1"]), timeout=30)
        runner.run(self._root_wrap(env, ["ln", "-sf", f"{libdir}/libevdi.so.1",
                                         f"{libdir}/libevdi.so"]), timeout=10)
        runner.run(self._root_wrap(env, ["ldconfig"]), timeout=60)

    def _layer_on_ostree(self, env, runner: Runner) -> None:
        """Layer the module into the next deployment.

        rpm-ostree does not touch the running system — it writes a new
        deployment — so the module cannot be loaded until the machine reboots.
        Record that, because reporting a plain failure would be a lie: the
        install worked, it just is not live yet.
        """
        for pkg in _OSTREE_PACKAGES:
            res = runner.run(
                self._root_wrap(env, ["rpm-ostree", "install", "-y", "--idempotent", pkg]),
                timeout=900,
            )
            if res.ok:
                self.reboot_required = True
                return

    def _install_from_repo(self, env, runner: Runner) -> None:
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
