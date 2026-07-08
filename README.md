<div align="center">
  <img src="sunshine.svg" alt="Zenith icon" width="200"/>
  <h1 align="center">Zenith</h1>
  <h4 align="center">Linux-first fork of Sunshine — a self-hosted game stream host for Moonlight.</h4>
</div>

<div align="center">
  <a href="https://github.com/jacksonpate/zenith/actions/workflows/zenith-ci.yml"><img src="https://github.com/jacksonpate/zenith/actions/workflows/zenith-ci.yml/badge.svg" alt="Zenith CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-8b30d9?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/platform-linux%20first-a855f7?style=flat-square" alt="Linux first">
  <a href="https://github.com/LizardByte/Sunshine"><img src="https://img.shields.io/badge/forked%20from-LizardByte%2FSunshine-6d1fb8?style=flat-square" alt="Upstream"></a>
</div>

<br/>

<div align="center">
  <img src="docs/images/zenith-fetch.svg" alt="Zenith at a glance" width="760"/>
</div>

## Why Zenith

The best Sunshine forks (Apollo, Sunshine-Foundation) are Windows-only because their headline
features are built on Windows virtual display drivers. Zenith ports the *ideas* everywhere — the
native way on Linux (PipeWire, KMS/DRM, Wayland) and with a bundled signed driver on Windows —
with NVIDIA **and** AMD as first-class citizens. Install it, pick **Headless** in Moonlight, and
a virtual display spins up at your client's exact resolution and refresh. No extra steps.

- 🖥️ **Plug-and-play virtual displays** — "Headless" and "Dual Display" apps work out of the
  box on Linux (KDE, GNOME, Sway/wlroots, Cinnamon — via a provider chain of forced-connector,
  compositor APIs, and a universal EVDI fallback that installs itself) and on Windows (bundled
  signed SudoVDA driver). The virtual display is born at the client's mode, so you stream at
  the right resolution the moment you connect. *Shipped.*
- 🎤 **Remote microphone** — your phone's mic shows up on the host as a real input device
  ("Zenith Mic") that Discord and games can use. On by default. *Shipped.*
- 📋 **Clipboard sync, both ways** — copy text or an image on either end, paste on the other.
  Large payloads move over the paired TLS connection. Wire-compatible with VoidLink and the
  Foundation-family clients. *Shipped.*
- 📁 **File transfer to the client** — push a file from the host to your connected device.
  *Beta.*
- ⚡ **Present-paced capture** — KMS capture wakes on real display vblanks instead of a
  timer: measured ~16ms → ~6-9ms host latency at high res on AMD. On by default
  (`capture_pacing = auto`); NVIDIA falls back to timer pacing automatically. *Shipped.*

See [ROADMAP.md](ROADMAP.md) for what's next.

## Install

Download the [latest release](https://github.com/jacksonpate/zenith/releases/latest) for your
platform:

| Platform | Package | Notes |
|----------|---------|-------|
| **Windows 10/11** | `Zenith-Windows-AMD64-installer.exe` | Bundles the virtual display driver; Secure Boot stays on. |
| **Ubuntu / Debian / Mint** | `zenith-*-amd64.deb` | `sudo apt install ./zenith-*.deb` |
| **Fedora / Nobara / Bazzite** | `zenith-fedora-*-x86_64.rpm` | `sudo dnf install ./zenith-*.rpm` |
| **Asahi Linux (Apple Silicon)** | `zenith-fedora-*-aarch64.rpm` | Fedora Asahi Remix. |

Then open `https://<host-ip>:47990`, set a username and password, and pair Moonlight/VoidLink.
Zenith installs as a drop-in replacement for a packaged Sunshine — same binary path, service
name, and config directory — so an existing Sunshine's pairings and settings carry over.

> **Windows SmartScreen**: the installer isn't code-signed yet, so Windows may show
> "Windows protected your PC." Click **More info → Run anyway**.

Prefer to build from source? See the [local build notes](docs/building_zenith_local.md).

## Credits & license

Zenith is a fork of [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine) and stands on
their work — go star them, sponsor them, and read their excellent
[documentation](https://docs.lizardbyte.dev/projects/sunshine/latest/), which applies to Zenith
for everything not listed above. Feature inspiration from
[Sunshine-Foundation](https://github.com/AlkaidLab/foundation-sunshine) and
[Apollo](https://github.com/ClassicOldSong/Apollo), reimplemented for Linux.

Licensed [GPL-3.0](LICENSE), same as upstream. Third-party notices: [NOTICE](NOTICE).
