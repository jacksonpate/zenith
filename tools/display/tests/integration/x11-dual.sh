#!/usr/bin/env bash
# Dual, against a real X server with real RandR outputs.
#
# xf86-video-dummy exposes DUMMY0..DUMMY15, but reports an output as *connected*
# only when a Monitor section is bound to it — there is no NumHeads option, and
# Xvfb gives just one output, which is not enough to test dual at all.
set -euo pipefail

sudo tee /tmp/xorg-dummy.conf >/dev/null <<'EOF'
Section "ServerLayout"
    Identifier "lay"
    Screen 0 "scr" 0 0
EndSection
Section "Monitor"
    Identifier  "Mon0"
    HorizSync   5.0 - 1000.0
    VertRefresh 5.0 - 200.0
    Option "Enable" "true"
    Option "Primary" "true"
EndSection
Section "Monitor"
    Identifier  "Mon1"
    HorizSync   5.0 - 1000.0
    VertRefresh 5.0 - 200.0
    Option "Enable" "true"
    Option "RightOf" "DUMMY0"
EndSection
Section "Monitor"
    Identifier  "Mon2"
    HorizSync   5.0 - 1000.0
    VertRefresh 5.0 - 200.0
    Option "Enable" "true"
    Option "RightOf" "DUMMY1"
EndSection
Section "Device"
    Identifier "dummy"
    Driver     "dummy"
    VideoRam   256000
    Option     "monitor-DUMMY0" "Mon0"
    Option     "monitor-DUMMY1" "Mon1"
    Option     "monitor-DUMMY2" "Mon2"
EndSection
Section "Screen"
    Identifier "scr"
    Device     "dummy"
    Monitor    "Mon0"
    DefaultDepth 24
    SubSection "Display"
        Depth   24
        Modes   "1920x1080" "1280x720"
        Virtual 8192 4096
    EndSubSection
EndSection
EOF

sudo Xorg :99 -config /tmp/xorg-dummy.conf -noreset -ac -logfile /tmp/xorg.log &
export DISPLAY=:99
for _ in $(seq 1 60); do xrandr -q >/dev/null 2>&1 && break; sleep 0.3; done
[ "$(xrandr -q | grep -cE '^DUMMY[0-2] connected')" = "3" ] \
  || { echo "need 3 connected outputs"; xrandr -q; exit 1; }

# The user's desk: two monitors, DUMMY0 primary. DUMMY2 stands in for the VDD
# (X11 CI has no VDD provider), so the backend is driven directly.
xrandr --output DUMMY0 --mode 1920x1080 --pos 0x0 --primary \
       --output DUMMY1 --mode 1280x720  --pos 1920x0 \
       --output DUMMY2 --off

python3 - <<'PY'
import json, re, subprocess, sys
from zenith_display.layouts.xrandr import XrandrBackend
from zenith_display.modes import client_mode
from zenith_display.runner import Runner

VDD = "DUMMY2"
PHYS = ["DUMMY0", "DUMMY1"]
HEAD = re.compile(r"^(\S+) connected( primary)?(?: (\d+x\d+)\+(\d+)\+(\d+))?")


def state():
    out = {}
    for line in subprocess.run(["xrandr", "-q"], capture_output=True, text=True).stdout.splitlines():
        m = HEAD.match(line)
        if m:
            out[m.group(1)] = {
                "on": bool(m.group(3)), "primary": bool(m.group(2)),
                "geom": f"{m.group(3)}+{m.group(4)}+{m.group(5)}" if m.group(3) else None,
            }
    return out


def expect(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        print(subprocess.run(["xrandr", "-q"], capture_output=True, text=True).stdout)
        sys.exit(1)


backend, mode = XrandrBackend(Runner()), client_mode()
baseline = json.loads(json.dumps(backend.snapshot()))  # as snapshot.save/load round-trips it

backend.apply_headless(VDD, mode)
s = state()
expect(s[VDD]["on"] and not any(s[n]["on"] for n in PHYS),
       "headless lights only the VDD")

backend.apply_dual(VDD, mode, baseline)
s = state()
expect(s["DUMMY0"]["geom"] == "1920x1080+0+0", "dual relights DUMMY0 at its own mode/pos")
expect(s["DUMMY1"]["geom"] == "1280x720+1920+0", "dual relights DUMMY1 at its own mode/pos")
expect(s["DUMMY0"]["primary"] and not s[VDD]["primary"],
       "dual gives the primary back (headless handed it to the VDD)")
expect(s[VDD]["geom"] == "2420x1668+3200+0", "the VDD sits past their right edge")

# The fallback: no snapshot at all, which is what a crashed session leaves.
backend.apply_headless(VDD, mode)
backend.apply_dual(VDD, mode, None)
s = state()
expect(all(s[n]["on"] for n in PHYS), "dual with NO baseline still relights them")

backend.restore(baseline)
s = state()
expect(s["DUMMY0"]["primary"] and not s[VDD]["on"], "restore: desk back, VDD gone")
print("x11 dual integration OK")
PY
