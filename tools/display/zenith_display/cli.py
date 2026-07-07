"""``zenith-display`` — the autopilot's command-line surface.

    zenith-display probe            machine-readable environment fingerprint
    zenith-display plan             what would happen, changes nothing
    zenith-display headless         only the VDD stays lit, at the client mode
    zenith-display dual             VDD joins as one more monitor
    zenith-display restore          tear down the VDD, replay the snapshot
    zenith-display setup            one-time privileged bootstrap (module,
                                    package, permissions) — run at install
    zenith-display doctor           human diagnosis + bootstrap hints

``headless``/``dual`` are wired into Zenith's default apps.json as prep
commands, with ``restore`` as their undo.  When no VDD provider is available
they *degrade* instead of failing: the stream simply shows the normal
desktop (exit 0), because a degraded session always beats a launch error.
Pass ``--strict`` to turn degrade conditions into nonzero exits (CI).
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


def cmd_probe(args) -> int:
    env = detect_mod.detect()
    print(json.dumps(env.to_dict(), indent=2))
    return EXIT_OK


def cmd_plan(args) -> int:
    runner = Runner(dry_run=True)
    env = detect_mod.detect(runner=runner)
    backend = get_backend(env, runner)
    chosen, report = providers.choose(env, runner)
    plan = {
        "mode": str(client_mode()),
        "backend": backend.name if backend else None,
        "provider": chosen.name if chosen else None,
        "chain": report,
    }
    print(json.dumps(plan, indent=2))
    return EXIT_OK if (backend and chosen) else (EXIT_NO_PROVIDER if backend else EXIT_NO_BACKEND)


def _degrade(args, code: int, message: str) -> int:
    """No-provider/no-backend outcome: stream the plain desktop instead of
    failing the launch — unless --strict asked for hard errors."""
    if args.strict:
        log.error("%s", message)
        return code
    log.warning("%s — continuing with the normal desktop (degraded)", message)
    return EXIT_OK


def _apply(kind: str, args) -> int:
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    mode = client_mode()

    backend = get_backend(env, runner)
    if backend is None:
        return _degrade(args, EXIT_NO_BACKEND,
                        f"no layout backend for session={env.session_type} "
                        f"desktop={env.desktop or '-'} (see `zenith-display doctor`)")

    # Crash-safety: a leftover snapshot means a previous session never
    # restored. Put the user's layout back before doing anything new.
    stale = snapshot.load()
    if stale and not args.dry_run:
        log.warning("stale snapshot found — restoring previous layout first")
        _restore_from(stale, env, runner)

    payload = backend.snapshot()

    provider, report = providers.choose(env, runner)
    if provider is None:
        for entry in report:
            log.info("  %-18s %s", entry["provider"], entry["reason"])
        return _degrade(args, EXIT_NO_PROVIDER,
                        "no VDD provider available (try `sudo zenith-display setup`)")
    log.info("mode=%s backend=%s provider=%s", mode, backend.name, provider.name)

    try:
        hint = provider.create(env, runner, mode)
    except Exception as exc:
        log.error("provider %s could not create a VDD: %s", provider.name, exc)
        return _degrade(args, EXIT_APPLY_FAILED, "VDD creation failed")

    vdd = backend.wait_for_output(hint) if not args.dry_run else hint
    if vdd is None:
        log.error("VDD (hint %r) never appeared in %s", hint, backend.name)
        provider.destroy(env, runner, {"vdd_output": hint})
        return _degrade(args, EXIT_APPLY_FAILED, "VDD did not appear")

    if not args.dry_run:
        snapshot.save(backend.name, payload, provider=provider.name, vdd_output=vdd)
    try:
        if kind == "headless":
            backend.apply_headless(vdd, mode)
        else:
            backend.apply_dual(vdd, mode)
    except Exception as exc:  # roll back: never leave the user stranded
        log.error("apply failed (%s); rolling back", exc)
        restored = False
        try:
            backend.restore(payload)
            restored = True
        finally:
            provider.destroy(env, runner, {"vdd_output": vdd})
            if restored and not args.dry_run:
                # Keep the snapshot when the rollback itself failed — it is
                # the only remaining record of the user's layout.
                snapshot.clear()
        return EXIT_APPLY_FAILED

    if not args.dry_run:
        # Wait for real scanout when the VDD is a DRM connector: Zenith
        # enumerates capture displays the moment this process exits.
        detect_mod.wait_connector_enabled(vdd)
    log.info("%s active on %s (%s)", kind, vdd, mode)
    return EXIT_OK


def _restore_from(doc: dict, env, runner: Runner) -> None:
    backend_cls = _BACKENDS.get(doc.get("backend", ""))
    if backend_cls is None:
        log.error("snapshot has unknown backend %r — keeping snapshot for manual recovery",
                  doc.get("backend"))
        return
    provider = providers.get_provider(doc.get("provider", ""))
    if provider is not None:
        provider.destroy(env, runner, {"vdd_output": doc.get("vdd_output")})
    elif doc.get("provider"):
        log.warning("unknown provider %r in snapshot — its VDD may need manual teardown",
                    doc.get("provider"))
    try:
        backend_cls(runner).restore(doc.get("payload", {}))
    except Exception as exc:
        log.error("restore failed (%s) — snapshot kept for retry", exc)
        return
    if not runner.dry_run:
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


def cmd_setup(args) -> int:
    """One-time bootstrap: load/install whatever the best provider needs.

    Heavy work (package installs, module loads, permission fixes) lives here
    — run from the package postinst or manually with sudo — so stream
    handshakes never block on it.
    """
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    chosen, report = providers.choose(env, runner, bootstrap=True)
    for entry in report:
        state = "ok" if entry["available"] else "--"
        print(f"  {state} {entry['provider']:<18} {entry['reason']}")
    if chosen:
        print(f"setup complete — provider ready: {chosen.name}")
        return EXIT_OK
    print("setup finished but no provider is ready; see `zenith-display doctor`")
    return EXIT_NO_PROVIDER


def cmd_doctor(args) -> int:
    runner = Runner(dry_run=True)
    env = detect_mod.detect(runner=runner)
    backend = get_backend(env, runner)
    chosen, report = providers.choose(env, runner)

    print(f"zenith-display {__version__}")
    print(f"  session   : {env.session_type}  desktop: {env.desktop or '-'}  distro: {env.distro or '-'}")
    print(f"  layout    : {backend.name if backend else 'NONE — no supported display tool found'}")
    connectors = ", ".join(
        f"{c.name}[{c.status}{'/VDD' if c.is_vdd else ''}]" for c in env.connectors) or "-"
    print(f"  connectors: {connectors}")
    print("  providers :")
    for entry in report:
        mark = "*" if chosen and entry["provider"] == chosen.name else " "
        state = "ok " if entry["available"] else "-- "
        print(f"   {mark} {state}{entry['provider']:<18} {entry['reason']}")
    if not chosen:
        print("\n  No provider is ready. Run `sudo zenith-display setup` — it loads or")
        print("  installs whatever this machine needs (evdi module, permissions).")
    return EXIT_OK if chosen else EXIT_NO_PROVIDER


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="zenith-display", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview: queries run, mutations are only logged")
    parser.add_argument("--strict", action="store_true",
                        help="exit nonzero instead of degrading to the plain desktop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "plan", "headless", "dual", "restore", "setup", "doctor"):
        sub.add_parser(name)
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
        "setup": cmd_setup,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
