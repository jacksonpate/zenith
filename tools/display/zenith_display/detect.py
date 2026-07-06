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


def scan_connectors(drm_glob: str = "/sys/class/drm/card*-*") -> List[Connector]:
    connectors = []
    for sysfs in sorted(glob.glob(drm_glob)):
        base = os.path.basename(sysfs)  # cardN-DP-1
        name = base.split("-", 1)[1] if "-" in base else base
        raw_edid = _read_bytes(os.path.join(sysfs, "edid"))
        monitor = edid_mod.monitor_name(raw_edid) if raw_edid else None
        connectors.append(
            Connector(
                sysfs=sysfs,
                name=name,
                status=_read(os.path.join(sysfs, "status")) or "unknown",
                enabled=_read(os.path.join(sysfs, "enabled")) == "enabled",
                monitor=monitor,
                is_vdd=monitor == VDD_MONITOR_NAME,
            )
        )
    return connectors


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
        env.has_passwordless_sudo = runner.run(["sudo", "-n", "true"], timeout=5).ok
    return env
