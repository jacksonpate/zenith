"""EVDI connection holder.

A tiny daemon: open the EVDI card, connect our generated EDID, then drain
events until told to die.  While this process lives, the kernel reports the
virtual connector as connected and the compositor treats it as a monitor.
All event handlers are left NULL — libevdi checks before invoking, and mere
liveness is all a connected connector requires; Zenith captures the output
through its normal capture pipeline (KMS/kwin/portal), not through EVDI.

Run as ``python3 -m zenith_display.providers.evdi_hold`` — spawned detached
by the evdi provider, terminated (SIGTERM) by ``destroy()``.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import select
import signal
import sys


class EvdiEventContext(ctypes.Structure):
    _fields_ = [
        ("dpms_handler", ctypes.c_void_p),
        ("mode_changed_handler", ctypes.c_void_p),
        ("update_ready_handler", ctypes.c_void_p),
        ("crtc_state_handler", ctypes.c_void_p),
        ("cursor_set_handler", ctypes.c_void_p),
        ("cursor_move_handler", ctypes.c_void_p),
        ("ddcci_data_handler", ctypes.c_void_p),
        ("user_data", ctypes.c_void_p),
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="evdi-hold")
    parser.add_argument("--card", type=int, required=True)
    parser.add_argument("--edid", required=True)
    parser.add_argument("--pidfile", required=True)
    parser.add_argument("--area-limit", type=int, default=0)
    args = parser.parse_args(argv)

    lib_name = ctypes.util.find_library("evdi")
    if not lib_name:
        print("libevdi not found", file=sys.stderr)
        return 1
    lib = ctypes.CDLL(lib_name)
    lib.evdi_open.restype = ctypes.c_void_p
    lib.evdi_open.argtypes = [ctypes.c_int]
    lib.evdi_connect.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint32,
    ]
    lib.evdi_disconnect.argtypes = [ctypes.c_void_p]
    lib.evdi_get_event_ready.restype = ctypes.c_int
    lib.evdi_get_event_ready.argtypes = [ctypes.c_void_p]
    lib.evdi_handle_events.argtypes = [ctypes.c_void_p, ctypes.POINTER(EvdiEventContext)]

    with open(args.edid, "rb") as fh:
        edid = fh.read()

    handle = lib.evdi_open(args.card)
    if not handle:
        print(f"evdi_open(card{args.card}) failed", file=sys.stderr)
        return 1

    area = args.area_limit or (3840 * 2160)
    lib.evdi_connect(handle, edid, len(edid), area)

    with open(args.pidfile, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    stop = {"flag": False}

    def _terminate(_sig, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    context = EvdiEventContext()  # all-NULL handlers: presence is the product
    fd = lib.evdi_get_event_ready(handle)
    while not stop["flag"]:
        try:
            readable, _, _ = select.select([fd], [], [], 1.0)
        except InterruptedError:
            continue
        if readable:
            lib.evdi_handle_events(handle, ctypes.byref(context))

    lib.evdi_disconnect(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
