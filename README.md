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
features are built on Windows virtual display drivers. Zenith ports the *ideas* to Linux the
native way — PipeWire, KMS/DRM, Wayland — with NVIDIA **and** AMD as first-class citizens and
GNOME/Wayland as the reference desktop.

- 🎤 **Remote microphone** — your phone's mic shows up on the host as a real input device
  ("Zenith Mic") that Discord and games can use. Wire-compatible with the enhanced Moonlight
  clients. *Shipped.*
- 🖥️ **Per-client display profiles & virtual display lifecycle** — the "VDD manager" concept,
  Linux-native. *Roadmap.*
- ⚡ **Present-paced capture** — KMS capture wakes on real display vblanks instead of a
  timer: measured ~16ms → ~6-9ms host latency at high res on AMD. On by default
  (`capture_pacing = auto`); NVIDIA falls back to timer pacing automatically. *Shipped.*
- 🌈 **HDR pipeline work** — per-frame luminance metadata (MaxCLL/MaxFALL), HLG support.
  *Roadmap.*

See [ROADMAP.md](ROADMAP.md) for the full plan.

## Install

Grab a package from the latest [CI run artifacts](https://github.com/jacksonpate/zenith/actions/workflows/zenith-ci.yml):
`deb` for Ubuntu/Debian/Mint, `rpm` for Fedora (x86_64 and aarch64 — including Asahi Linux on
Apple Silicon), or build from source ([local build notes](docs/building_zenith_local.md),
[upstream build script](scripts/linux_build.sh)). Zenith installs as a drop-in replacement for
a packaged Sunshine: same binary path, service name, and config directory — your pairings and
settings carry over.

## Credits & license

Zenith is a fork of [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine) and stands on
their work — go star them, sponsor them, and read their excellent
[documentation](https://docs.lizardbyte.dev/projects/sunshine/latest/), which applies to Zenith
for everything not listed above. Feature inspiration from
[Sunshine-Foundation](https://github.com/AlkaidLab/foundation-sunshine) and
[Apollo](https://github.com/ClassicOldSong/Apollo), reimplemented for Linux.

Licensed [GPL-3.0](LICENSE), same as upstream. Third-party notices: [NOTICE](NOTICE).
