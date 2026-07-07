"""Zenith Display Autopilot.

Plug-and-play virtual display (VDD) management for Zenith hosts.

The autopilot turns "Headless" and "Dual Display" from hand-rolled per-machine
scripts into a zero-configuration feature:

    detect  -> fingerprint the session (X11/Wayland, DE, tools, DRM state)
    plan    -> choose a layout backend + an ordered VDD provider chain
    ensure  -> bootstrap whatever the chosen provider needs (module, package)
    apply   -> snapshot the current layout, spin the VDD at the exact mode the
               Moonlight client asked for, rearrange outputs
    restore -> tear the VDD down and replay the snapshot byte-for-byte

Everything is driven from ``zenith-display`` (see ``cli.py``).  No external
Python dependencies: the tool must run on a freshly installed distro.
"""

__version__ = "0.1.0"

VDD_MONITOR_NAME = "ZenithVDD"
