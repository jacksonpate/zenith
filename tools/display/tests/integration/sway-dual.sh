#!/usr/bin/env bash
# Dual, against a real wlroots compositor.
#
# Headless sway needs no GPU, so this runs anywhere. It boots two "monitors"
# plus a VDD, drives the real WlrBackend, and asks *sway* what happened.
set -euo pipefail

export WLR_BACKENDS=headless
export WLR_LIBINPUT_NO_DEVICES=1
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/xdg}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

sway --config /dev/null &
for _ in $(seq 1 50); do
  SOCK=$(ls "$XDG_RUNTIME_DIR"/sway-ipc.* 2>/dev/null | head -1) && [ -n "$SOCK" ] && break
  sleep 0.2
done
export SWAYSOCK="$SOCK"
export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=sway

# The user's desk: two monitors, side by side.
swaymsg create_output
swaymsg -- output HEADLESS-1 enable mode --custom 2560x1440@60Hz position 0 0
swaymsg -- output HEADLESS-2 enable mode --custom 1920x1080@60Hz position 2560 0

python3 - <<'PY'
import json, subprocess, sys
from zenith_display.layouts.wlr import WlrBackend
from zenith_display.modes import Mode
from zenith_display.runner import Runner

PHYS = ["HEADLESS-1", "HEADLESS-2"]


def sway(*a):
    return subprocess.run(["swaymsg", *a], capture_output=True, text=True)


def state():
    return {o["name"]: o for o in json.loads(sway("-t", "get_outputs", "--raw").stdout)}


def expect(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        print(json.dumps({n: {"active": o["active"], "rect": o["rect"]}
                          for n, o in state().items()}, indent=1, sort_keys=True))
        sys.exit(1)


backend, mode = WlrBackend(Runner()), Mode(2420, 1668, 60)
# A real snapshot, round-tripped through JSON exactly as it is persisted.
baseline = json.loads(json.dumps(backend.snapshot()))

before = set(state())
sway("create_output")
vdd = sorted(set(state()) - before)[0]

backend.apply_headless(vdd, mode)
lit = sorted(n for n, o in state().items() if o["active"])
expect(lit == [vdd], f"headless lights only the VDD (got {lit})")

backend.apply_dual(vdd, mode, baseline)
st = state()
dark = [n for n in PHYS if not st[n]["active"]]
expect(not dark, f"dual RELIGHTS the monitors headless turned off (still dark: {dark})")

edge = max(st[n]["rect"]["x"] + st[n]["rect"]["width"] for n in PHYS)
expect(st[vdd]["rect"]["x"] >= edge,
       f"the VDD sits past them, not on top at x=0 (x={st[vdd]['rect']['x']}, edge={edge})")

# The fallback: no snapshot at all, which is what a crashed session leaves.
backend.apply_headless(vdd, mode)
backend.apply_dual(vdd, mode, None)
st = state()
dark = [n for n in PHYS if not st[n]["active"]]
expect(not dark, f"dual with NO baseline still relights them (still dark: {dark})")

print("sway dual integration OK")
PY
