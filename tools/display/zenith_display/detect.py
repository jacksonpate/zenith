"""Environment fingerprinting: session, desktop, tools, DRM connector state."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import VDD_MONITOR_NAME
from . import edid as edid_mod
from .runner import Runner, which


@dataclass
class Connector:
    """One DRM connector as seen through sysfs."""

    sysfs: str  # e.g. /sys/class/drm/card1-DP-1
    name: str  # e.g. DP-1
    status: str  # connected | disconnected | unknown
    enabled: bool
    monitor: Optional[str] = None  # EDID product name if readable
    is_vdd: bool = False
    driver: str = ""  # DRM driver of the parent card (amdgpu, i915, evdi, ...)


@dataclass
class Environment:
    session_type: str = "unknown"  # x11 | wayland | tty | unknown
    desktop: str = ""  # lowercase XDG_CURRENT_DESKTOP
    distro: str = ""  # os-release ID
    tools: Dict[str, bool] = field(default_factory=dict)
    connectors: List[Connector] = field(default_factory=list)
    is_root: bool = False
    has_passwordless_sudo: bool = False

    @property
    def vdd_connectors(self) -> List[Connector]:
        return [c for c in self.connectors if c.is_vdd]

    @property
    def disconnected_connectors(self) -> List[Connector]:
        return [c for c in self.connectors if c.status == "disconnected"]

    def to_dict(self) -> dict:
        return {
            "session_type": self.session_type,
            "desktop": self.desktop,
            "distro": self.distro,
            "tools": self.tools,
            "is_root": self.is_root,
            "has_passwordless_sudo": self.has_passwordless_sudo,
            "connectors": [
                {
                    "name": c.name,
                    "status": c.status,
                    "enabled": c.enabled,
                    "monitor": c.monitor,
                    "is_vdd": c.is_vdd,
                }
                for c in self.connectors
            ],
        }


_TOOLS = (
    "kscreen-doctor",
    "xrandr",
    "wlr-randr",
    "swaymsg",
    "hyprctl",
    "gdbus",
    "modprobe",
)


def _session_type(environ) -> str:
    explicit = environ.get("XDG_SESSION_TYPE", "").lower()
    if explicit in ("x11", "wayland", "tty"):
        return explicit
    if environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _read(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(4096).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(512)
    except OSError:
        return b""


# Minimum NVIDIA driver the bundled FFmpeg's nvenc accepts. Tied to the
# build-deps nv-codec-headers pin (sdk/12.0 -> 520); older drivers make
# Zenith fall back to CPU encoding without any visible error.
NVENC_MIN_DRIVER = 520


def nvidia_driver_version(path: str = "/sys/module/nvidia/version") -> str:
    """Loaded NVIDIA driver version, or "" when the module isn't loaded."""
    return _read(path)


def nvenc_supported(version: str) -> bool:
    """Whether this driver version satisfies the bundled nvenc's minimum."""
    try:
        return int(version.split(".", 1)[0]) >= NVENC_MIN_DRIVER
    except ValueError:
        return True  # unrecognized format — don't warn on guesswork


def scan_connectors(drm_glob: str = "/sys/class/drm/card*-*") -> List[Connector]:
    connectors = []
    for sysfs in sorted(glob.glob(drm_glob)):
        base = os.path.basename(sysfs)  # cardN-DP-1
        card, _, name = base.partition("-")
        raw_edid = _read_bytes(os.path.join(sysfs, "edid"))
        monitor = edid_mod.monitor_name(raw_edid) if raw_edid else None
        driver_link = os.path.join(os.path.dirname(sysfs), card, "device", "driver")
        connectors.append(
            Connector(
                sysfs=sysfs,
                name=name or base,
                status=_read(os.path.join(sysfs, "status")) or "unknown",
                enabled=_read(os.path.join(sysfs, "enabled")) == "enabled",
                monitor=monitor,
                is_vdd=monitor == VDD_MONITOR_NAME,
                driver=os.path.basename(os.path.realpath(driver_link)) if os.path.exists(driver_link) else "",
            )
        )
    return connectors


def wait_connector_enabled(name: str, timeout: float = 5.0, settle: float = 0.5) -> bool:
    """Block until the DRM connector's CRTC actually lights (scanout committed).

    Compositors list an output before its modeset commits; Zenith enumerates
    capture displays immediately after the prep-command returns, so waiting
    for the compositor listing alone races a dark VDD.  Ported from the
    original zenith-display scripts, which existed to fix exactly that.
    Returns False when no matching sysfs connector exists (compositor-native
    virtual outputs have no DRM connector — nothing to wait on).
    """
    import time as _time

    paths = glob.glob(f"/sys/class/drm/card*-{name}/enabled")
    if not paths:
        return False
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if any(_read(p) == "enabled" for p in paths):
            _time.sleep(settle)  # let the first frames scan out
            return True
        _time.sleep(0.1)
    return False


def _distro() -> str:
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("ID="):
            return line.split("=", 1)[1].strip('"')
    return ""


def detect(environ=os.environ, runner: Optional[Runner] = None) -> Environment:
    runner = runner or Runner()
    env = Environment(
        session_type=_session_type(environ),
        desktop=environ.get("XDG_CURRENT_DESKTOP", "").lower(),
        distro=_distro(),
        tools={tool: which(tool) is not None for tool in _TOOLS},
        connectors=scan_connectors(),
        is_root=hasattr(os, "geteuid") and os.geteuid() == 0,
    )
    if not env.is_root and which("sudo"):
        env.has_passwordless_sudo = runner.query(["sudo", "-n", "true"], timeout=5).ok
    return env
