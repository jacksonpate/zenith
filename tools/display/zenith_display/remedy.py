"""What is wrong with this machine, and the exact command that fixes it.

Every check here answers three questions at once: is something broken, what does
it cost the user, and what command repairs it.  That third part is the reason
this module exists separately from ``detect``.  A diagnosis the user has to
translate into an action is a diagnosis most people abandon — so ``doctor``
offers to run the commands, ``fix`` runs them without asking, and ``setup``
applies them at install time.  The same list drives all three, so a source build
ends up in the same state the rpm would have put it in.

The bug that motivated this: a source build installed to ``/usr/local/bin`` has
no file capabilities, because only the packaging applies them.  Without
``cap_sys_admin`` the KMS capture path cannot open a DRM framebuffer, so Zenith
falls back to the portal — silently, and with present-paced capture disabled,
because vblank pacing lives on the KMS path.  Nothing fails; the stream is just
slower forever.  Three lines in the log say ``Failed to gain CAP_SYS_ADMIN`` and
nothing tells the user that one ``setcap`` would undo all of it.
"""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from .runner import Runner

# Capability bit numbers, from linux/capability.h.
CAP_SYS_ADMIN = 21
CAP_SYS_NICE = 23

# What the packaging grants (cmake/packaging/linux.cmake, the Arch .install, and
# the AppImage AppRun all agree on this pair) — so a source build should too.
REQUIRED_CAPS = ("cap_sys_admin", "cap_sys_nice")
_REQUIRED_BITS = {"cap_sys_admin": CAP_SYS_ADMIN, "cap_sys_nice": CAP_SYS_NICE}

# The host binary, newest name first: the rename to `zenith` is recent enough
# that plenty of machines still have a `sunshine` from a local build.
_BINARY_NAMES = ("zenith", "sunshine")
_BINARY_DIRS = ("/usr/local/bin", "/usr/bin", "/opt/zenith/bin")


@dataclass
class Remedy:
    """One fixable problem, with the command that fixes it."""

    key: str  # stable identifier, for tests and for --only
    problem: str  # one line: what is wrong
    detail: str  # why it matters, in the user's terms
    commands: List[List[str]] = field(default_factory=list)
    needs_root: bool = True

    def shell(self) -> str:
        """The commands as the user would type them."""
        import shlex

        prefix = "sudo " if self.needs_root and os.geteuid() != 0 else ""
        return "\n".join(prefix + " ".join(shlex.quote(a) for a in cmd) for cmd in self.commands)

    def apply(self, env, runner: Runner) -> bool:
        """Run the commands, escalating only if we are not already root."""
        wrap = [] if (env.is_root or not self.needs_root) else ["sudo"]
        for cmd in self.commands:
            if not runner.run([*wrap, *cmd], timeout=120).ok:
                return False
        return True


@dataclass
class Advisory:
    """Something the user should know that no command can fix."""

    key: str
    problem: str
    detail: str


def find_binary(cwd: Optional[str] = None) -> Optional[str]:
    """The Zenith host binary that would actually run on this machine.

    Priority is what ``PATH`` resolves, because that is what a user or a systemd
    unit invokes.  A build tree is checked last so that running ``doctor`` from a
    fresh clone still finds something to talk about.
    """
    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return os.path.realpath(found)
    for name in _BINARY_NAMES:
        for directory in _BINARY_DIRS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
    # A clone that has been built but not installed.
    roots = [cwd or os.getcwd(),
             os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))]
    for root in roots:
        for name in _BINARY_NAMES:
            for build in ("build", "cmake-build-release", "cmake-build-debug"):
                candidate = os.path.join(root, build, name)
                if os.path.isfile(candidate):
                    return candidate
    return None


def missing_caps(path: str) -> Optional[List[str]]:
    """Which of the required capabilities this binary does not have.

    Read straight from the ``security.capability`` xattr rather than through
    ``getcap``: libcap's tools are not installed everywhere, and an unprivileged
    process can read the attribute perfectly well.  Returns None when the answer
    cannot be determined (a filesystem without xattr support, say) — which is not
    the same as "none are missing", and must not be reported as healthy.
    """
    import errno

    # ENODATA (== ENOATTR on Linux) is the definite answer "this file has no
    # capabilities". Anything else means we could not look, which is a different
    # thing and must not be reported as healthy.
    undetermined = {getattr(errno, name) for name in
                    ("EOPNOTSUPP", "ENOTSUP", "EPERM", "EACCES", "ENOSYS")
                    if hasattr(errno, name)}
    try:
        raw = os.getxattr(path, "security.capability")
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return list(REQUIRED_CAPS)  # no capability set at all
        if exc.errno in undetermined:
            return None
        return list(REQUIRED_CAPS)

    # struct vfs_cap_data: __le32 magic_etc, then permitted/inheritable pairs.
    # Every capability we care about is below 32, so the first permitted word is
    # the only one that has to be decoded.
    if len(raw) < 8:
        return None
    permitted_low, = struct.unpack_from("<I", raw, 4)
    return [cap for cap, bit in _REQUIRED_BITS.items() if not permitted_low & (1 << bit)]


def check_capabilities(env, cwd: Optional[str] = None) -> Optional[Remedy]:
    """The host binary needs cap_sys_admin for KMS capture, cap_sys_nice for priority."""
    binary = find_binary(cwd)
    if binary is None:
        return None
    lacking = missing_caps(binary)
    if lacking is None or not lacking:
        return None
    return Remedy(
        key="capabilities",
        problem=f"{binary} is missing {', '.join(lacking)}",
        detail=(
            "Without cap_sys_admin the KMS capture path cannot open a DRM framebuffer, so\n"
            "capture silently falls back to the desktop portal — and present-paced capture\n"
            "goes with it, because vblank pacing only exists on the KMS path. The stream\n"
            "still works; it is just slower, for as long as the machine stays this way.\n"
            "The deb/rpm set these at install time. A build installed by hand does not."
        ),
        commands=[["setcap", f"{','.join(REQUIRED_CAPS)}+p", binary]],
    )


def check_helper(env, provider_report=None) -> Optional[Remedy]:
    """The privileged helper the spare-connector provider drives."""
    from .providers import drm_debugfs

    if drm_debugfs._helper() is not None:
        return None
    # Only worth raising where the provider could actually be used.
    if not any(c.status == "disconnected" and not c.name.startswith(("eDP", "LVDS", "DSI", "Writeback"))
               for c in env.connectors):
        return None
    return Remedy(
        key="helper",
        problem="the privileged display helper is not installed",
        detail=(
            "Zenith borrows a spare port to make a virtual display, which means writing two\n"
            "kernel files. `setup` installs a small helper plus one scoped sudoers rule so\n"
            "that streaming never needs root itself."
        ),
        commands=[["zenith-display", "setup"]],
    )


def check_evdi(env) -> Optional[Remedy]:
    """evdi is only worth installing when no port can be borrowed."""
    from .providers import drm_debugfs

    borrowable = [c for c in env.connectors
                  if c.status == "disconnected"
                  and not c.name.startswith(("eDP", "LVDS", "DSI", "Writeback"))
                  and (c.driver or "") not in drm_debugfs._EXCLUDED_DRIVERS]
    if borrowable:
        return None  # a spare port beats a kernel module every time
    if os.path.exists("/dev/evdi0") or _libevdi_present():
        return None
    return Remedy(
        key="evdi",
        problem="every port is occupied and libevdi is not installed",
        detail=(
            "With no spare connector to borrow, the virtual display needs the evdi kernel\n"
            "module. `setup` installs it from your distro where it is packaged and builds\n"
            "it from source where it is not."
        ),
        commands=[["zenith-display", "setup"]],
    )


def _libevdi_present() -> bool:
    import ctypes.util

    return ctypes.util.find_library("evdi") is not None


def check_gpu_affinity(env) -> Optional[Advisory]:
    """Warn when the display cannot be put on the GPU that encodes.

    Nothing to run here — if a spare port existed on the encoder's card the
    provider would already have chosen it.  This is the case where none does, and
    the user deserves to know why the ceiling is where it is rather than
    concluding the host is too slow.
    """
    encoder_card = getattr(env, "encoder_card", "") or ""
    if not encoder_card:
        return None
    from .providers import drm_debugfs

    def borrowable(c):
        return (not c.name.startswith(("eDP", "LVDS", "DSI", "Writeback"))
                and (c.driver or "") not in drm_debugfs._EXCLUDED_DRIVERS)

    spares = [c for c in env.connectors
              if borrowable(c) and (c.status == "disconnected" or c.is_vdd)]
    if not spares or any(c.card == encoder_card for c in spares):
        return None
    other = spares[0]
    return Advisory(
        key="gpu-affinity",
        problem=(f"every free port is on {other.card} ({other.driver or 'unknown'}), "
                 f"but {encoder_card} will encode"),
        detail=(
            "The virtual display has to go on the card that is not encoding, so each frame\n"
            "crosses GPUs through system memory instead of staying put. That is survivable\n"
            "at 1080p and it is the ceiling you hit at tablet resolutions.\n"
            "Freeing a port on the encoding GPU — unplug a monitor from it — removes the copy."
        ),
    )


def collect(env, cwd: Optional[str] = None):
    """Every fixable problem, and every advisory, for this machine."""
    remedies = [r for r in (check_capabilities(env, cwd),
                            check_helper(env),
                            check_evdi(env)) if r is not None]
    advisories = [a for a in (check_gpu_affinity(env),) if a is not None]
    return remedies, advisories
