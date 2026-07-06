"""``zenith-display`` — the autopilot's command-line surface.

    zenith-display probe            machine-readable environment fingerprint
    zenith-display plan [MODE]      what would happen, changes nothing
    zenith-display headless         only the VDD stays lit, at the client mode
    zenith-display dual             VDD joins as one more monitor
    zenith-display restore          tear down the VDD, replay the snapshot
    zenith-display doctor           human diagnosis + bootstrap hints

``headless``/``dual`` are wired into Zenith's default apps.json as prep
commands, with ``restore`` as their undo — so a fresh install ships working
"Headless" and "Dual Display" entries with zero user configuration.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__, providers, snapshot
from . import detect as detect_mod
from .layouts import get_backend, gnome, kscreen, wlr, xrandr
from .modes import client_mode
from .runner import Runner

log = logging.getLogger("zenith-display")

_BACKENDS = {
    "kscreen": kscreen.KScreenBackend,
    "xrandr": xrandr.XrandrBackend,
    "wlr": wlr.WlrBackend,
    "gnome": gnome.GnomeBackend,
}

EXIT_OK = 0
EXIT_NO_PROVIDER = 3
EXIT_NO_BACKEND = 4
EXIT_APPLY_FAILED = 5


def _provider_by_name(name):
    return providers.get_provider(name)


def cmd_probe(args) -> int:
    env = detect_mod.detect()
    print(json.dumps(env.to_dict(), indent=2))
    return EXIT_OK


def cmd_plan(args) -> int:
    runner = Runner(dry_run=True)
    env = detect_mod.detect()
    backend = get_backend(env, runner)
    chosen, report = providers.choose(env, runner, bootstrap=False)
    plan = {
        "mode": str(client_mode()),
        "backend": backend.name if backend else None,
        "provider": chosen.name if chosen else None,
        "chain": report,
    }
    print(json.dumps(plan, indent=2))
    return EXIT_OK if (backend and chosen) else (EXIT_NO_PROVIDER if backend else EXIT_NO_BACKEND)


def _apply(kind: str, args) -> int:
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    mode = client_mode()

    backend = get_backend(env, runner)
    if backend is None:
        log.error("no layout backend for session=%s desktop=%s — run `zenith-display doctor`",
                  env.session_type, env.desktop)
        return EXIT_NO_BACKEND

    # Crash-safety: a leftover snapshot means a previous session never
    # restored. Put the user's layout back before doing anything new.
    stale = snapshot.load()
    if stale and not args.dry_run:
        log.warning("stale snapshot found — restoring previous layout first")
        _restore_from(stale, env, runner)

    payload = backend.snapshot()

    provider, report = providers.choose(env, runner, bootstrap=not args.no_bootstrap)
    if provider is None:
        log.error("no VDD provider available:")
        for entry in report:
            log.error("  %-18s %s", entry["provider"], entry["reason"])
        return EXIT_NO_PROVIDER
    log.info("mode=%s backend=%s provider=%s", mode, backend.name, provider.name)

    hint = provider.create(env, runner, mode)
    vdd = backend.wait_for_output(hint) if not args.dry_run else hint
    if vdd is None:
        log.error("VDD (hint %r) never appeared in %s", hint, backend.name)
        provider.destroy(env, runner, {"vdd_output": hint})
        return EXIT_APPLY_FAILED

    snapshot.save(backend.name, payload, provider=provider.name, vdd_output=vdd)
    try:
        if kind == "headless":
            backend.apply_headless(vdd, mode)
        else:
            backend.apply_dual(vdd, mode)
    except Exception as exc:  # roll back: never leave the user stranded
        log.error("apply failed (%s); rolling back", exc)
        try:
            backend.restore(payload)
        finally:
            provider.destroy(env, runner, {"vdd_output": vdd})
            snapshot.clear()
        return EXIT_APPLY_FAILED

    log.info("%s active on %s (%s)", kind, vdd, mode)
    return EXIT_OK


def _restore_from(doc: dict, env, runner: Runner) -> None:
    backend_cls = _BACKENDS.get(doc.get("backend", ""))
    if backend_cls is None:
        log.error("snapshot has unknown backend %r", doc.get("backend"))
        return
    provider = _provider_by_name(doc.get("provider", ""))
    if provider is not None:
        provider.destroy(env, runner, {"vdd_output": doc.get("vdd_output")})
    backend_cls(runner).restore(doc.get("payload", {}))
    snapshot.clear()


def cmd_restore(args) -> int:
    doc = snapshot.load()
    if doc is None:
        log.info("nothing to restore")
        return EXIT_OK
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    _restore_from(doc, env, runner)
    return EXIT_OK


def cmd_doctor(args) -> int:
    runner = Runner(dry_run=True)
    env = detect_mod.detect()
    backend = get_backend(env, runner)
    chosen, report = providers.choose(env, runner, bootstrap=False)

    print(f"zenith-display {__version__}")
    print(f"  session   : {env.session_type}  desktop: {env.desktop or '-'}  distro: {env.distro or '-'}")
    print(f"  layout    : {backend.name if backend else 'NONE — no supported display tool found'}")
    print("  connectors: " + ", ".join(
        f"{c.name}[{c.status}{'/VDD' if c.is_vdd else ''}]" for c in env.connectors) or "-")
    print("  providers :")
    for entry in report:
        mark = "*" if chosen and entry["provider"] == chosen.name else " "
        state = "ok " if entry["available"] else "-- "
        print(f"   {mark} {state}{entry['provider']:<18} {entry['reason']}")
    if not chosen:
        print("\n  No provider is ready. Quickest paths:")
        print("   - KDE/GNOME/wlroots users: update to a compositor with virtual outputs")
        print("   - any distro: install the evdi package (e.g. `evdi-dkms`) and libevdi,")
        print("     then re-run — zenith-display will do the rest")
    return EXIT_OK if chosen else EXIT_NO_PROVIDER


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="zenith-display", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="log commands without executing them")
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="never install packages / load modules automatically")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    sub.add_parser("plan")
    sub.add_parser("headless")
    sub.add_parser("dual")
    sub.add_parser("restore")
    sub.add_parser("doctor")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="zenith-display: %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    dispatch = {
        "probe": cmd_probe,
        "plan": cmd_plan,
        "headless": lambda a: _apply("headless", a),
        "dual": lambda a: _apply("dual", a),
        "restore": cmd_restore,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
