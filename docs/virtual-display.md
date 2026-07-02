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

Install the session script and wire two apps into Zenith (web UI → Applications,
or `apps.json`):

```sh
sudo install -m 0755 scripts/zenith-display /usr/local/bin/zenith-display
```

| app name | do command | undo command |
|---|---|---|
| Headless | `/usr/local/bin/zenith-display headless` | `/usr/local/bin/zenith-display restore` |
| Dual Monitor | `/usr/local/bin/zenith-display dual` | `/usr/local/bin/zenith-display restore` |

- **Headless**: your real monitors turn off; the VDD becomes the only display,
  at the connecting client's exact resolution and refresh (Zenith passes
  `SUNSHINE_CLIENT_WIDTH/HEIGHT/FPS` to prep-commands).
- **Dual Monitor**: the VDD appears as an extra monitor to the right of your
  real layout.
- **Quit the app in Moonlight**: your exact pre-session layout comes back
  (mirror groups included) and the VDD goes dark.

The layout snapshot lives in `$XDG_RUNTIME_DIR/zenith-display-state.json` and
every change is applied as a *temporary* Mutter configuration — a crash or
reboot always falls back to your saved monitor layout, so you can't get
stranded on an invisible display.

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
