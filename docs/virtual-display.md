# Virtual displays (VDD) on Zenith

Zenith gives you Apollo/Sunshine-Foundation-style virtual displays on Linux: a
phantom monitor that lights up **at your Moonlight device's exact resolution**
when you start a stream, and disappears when you quit it.

Two pieces:

| piece | what it does | when it runs |
|---|---|---|
| `scripts/zenith-vdd-setup` | one-time host setup: forces an unused GPU output "connected" with a generated EDID | once, as root, then reboot |
| `scripts/zenith-display` | per-session logic: activates the VDD at the client's resolution, restores your real layout after | automatically, as a Zenith app prep-command |

Requirements: a GPU with a free connector (almost every desktop GPU exposes
unused DP outputs), and a **GNOME/Wayland** session (Mutter backend — KDE and
wlroots backends are on the roadmap).

## 1. One-time setup

```sh
sudo zenith-vdd-setup detect      # shows connectors, suggests a free one
sudo zenith-vdd-setup install     # generates EDID, installs it, sets kernel args
# reboot
zenith-vdd-setup verify           # confirms the phantom monitor is alive
```

`install` picks a free DisplayPort connector automatically (or take
`--connector DP-2`). It generates an EDID advertising a broad mode list —
1080p/1440p/4K at 60 and 120 Hz, iPad Pro 11"/13" natives, iPhone natives,
common Android and 16:10 modes — so "exact resolution" works for real devices
out of the box. Custom list: `--modes 2420x1668@120,1920x1080@60` (first entry
becomes the preferred mode; anything up to the 655 MHz DTD pixel-clock limit
encodes, which means 4K60 yes, 4K120 no).

It handles the boot plumbing per distro:

- **GRUB distros** (Ubuntu/Debian/Mint/Fedora Workstation): appends
  `drm.edid_firmware=<CONN>:edid/zenith-vdd.bin video=<CONN>:e` (plus
  `nvidia-drm.modeset=1` on NVIDIA) to `/etc/default/grub` and runs the grub
  updater.
- **rpm-ostree** (Silverblue/Kinoite/Asahi Fedora Remix): firmware goes to
  `/etc/firmware` (immutable `/usr`) with `firmware_class.path=/etc/firmware`,
  kargs via `rpm-ostree kargs`.
- **anything else**: prints the exact args for you to add manually.

After reboot, GNOME's display settings show a new monitor ("ZenithVDD"). You
can leave it disabled — `zenith-display` turns it on only during sessions.

## 2. Per-session apps

Nothing to install: every Zenith package ships **Headless** and **Dual
Display** apps backed by the built-in `zenith-display` autopilot (installed
to `/usr/bin`). It detects your session (KDE, GNOME, X11, wlroots), spins a
virtual display at the connecting client's exact resolution and refresh
(`SUNSHINE_CLIENT_WIDTH/HEIGHT/FPS`), and restores your exact layout when
the stream ends.

- **Headless**: your real monitors turn off; the VDD becomes the only display.
- **Dual Display**: the VDD appears as an extra monitor to the right of your
  real layout.
- **Quit the app in Moonlight**: your pre-session layout comes back and the
  VDD goes dark. Crash-safe: a stale snapshot is restored on the next run.
- If no VDD mechanism is available the app still launches (you stream the
  normal desktop); run `sudo zenith-display setup` once to bootstrap the
  universal EVDI fallback, and `zenith-display doctor` to see what the
  machine supports.

> **Migrating from the old scripts:** remove any hand-installed copies —
> `sudo rm -f /usr/local/bin/zenith-display /usr/local/bin/zenith-display-kde
> /usr/local/bin/zenith-display-auto` — they shadow the packaged autopilot on
> PATH and use an incompatible state file.

If the virtual connector isn't named `DP-1`, set `ZENITH_VDD_CONNECTOR` in the
app's environment (or Zenith's global env) to match.

## Troubleshooting

- `verify` says disconnected → check `cat /proc/cmdline` contains the args;
  some distros need the firmware blob inside the initramfs if the GPU driver
  does early modesetting (`sudo update-initramfs -u` after adding a hook, or
  `install_items+=" /usr/lib/firmware/edid/zenith-vdd.bin "` in a dracut conf).
- A client resolution isn't matched exactly → it's not in the EDID mode list;
  re-run `install` with `--modes` including it (reboot applies).
- Wrong monitor captured during sessions → pin `output_name` in
  `sunshine.conf` to the VDD, or reorder via GNOME display settings.

## Windows

The Windows installer bundles the signed ZakoVDD indirect display driver
(Sunshine-Foundation lineage) and installs it during setup, so **Headless**
and **Dual Display** work out of the box: the default apps run
`scripts\ZenithDisplay.ps1`, which creates/destroys the virtual monitor over
the driver's IOCTL control interface and switches the display topology.

- The driver never installed (setup skipped, older package)? Run
  `powershell -File "C:\Program Files\Zenith\scripts\ZenithDisplay.ps1" ensure`
  once as admin.
- Resolution/refresh follow the Moonlight client through Zenith's display
  device options (`dd_resolution_option = auto`); the mode list the driver
  advertises lives in `config\vdd_settings.xml`.
- `ZenithDisplay.ps1 probe` prints a JSON diagnosis (driver present, control
  interface reachable, monitor count).
- Topology switching currently uses DisplaySwitch (external/extend/internal);
  exact multi-monitor CCD control lands with the native integration.
