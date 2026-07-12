"""``zenith-display`` — the autopilot's command-line surface.

    zenith-display probe            machine-readable environment fingerprint
    zenith-display plan             what would happen, changes nothing
    zenith-display headless         only the VDD stays lit, at the client mode
    zenith-display dual             VDD joins as one more monitor
    zenith-display restore          tear down the VDD, replay the snapshot
    zenith-display remember         pin the desk as it is now as the layout to
                                    come back to (learned automatically too)
    zenith-display forget           drop it and relearn on the next desktop
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
from typing import Optional

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

    provider, report = providers.choose(env, runner)
    if provider is None:
        for entry in report:
            log.info("  %-18s %s", entry["provider"], entry["reason"])
        return _degrade(args, EXIT_NO_PROVIDER,
                        "no VDD provider available (try `sudo zenith-display setup`)")
    log.info("mode=%s backend=%s provider=%s", mode, backend.name, provider.name)

    # Crash-safety: a leftover snapshot means a previous session never
    # restored. Put the user's layout back before doing anything new.
    stale = snapshot.load()
    if stale and not args.dry_run:
        log.warning("stale snapshot found — restoring previous layout first")
        _restore_from(stale, env, runner)

    # Every virtual display currently up, ours or a dead session's. A leaked VDD
    # is not a monitor: counted as one, it convinces us the desk is lit, gets
    # recorded into the baseline, and dual then relights the ghost instead.
    vdds = _known_vdds(env, runner, provider, stale)
    if not args.dry_run:
        _destroy_orphans(env, runner, provider, vdds, backend)
        # Let the restore land. Compositors reconfigure asynchronously, so the
        # layout we are about to record as "the user's" is still the old
        # session's for a beat — and recording a dark desk as the baseline is
        # what leaves the monitors dark forever after.
        backend.wait_for_user_layout(exclude=vdds)

    payload = _strip(backend.snapshot(), vdds)
    baseline = payload
    if snapshot.is_user_layout(payload):
        # This is a desk somebody is sitting at. Learn it, so a future session
        # that finds no snapshot can put it back exactly — rather than lighting
        # up every monitor it can find, including the ones deliberately off.
        if not args.dry_run:  # a dry run inspects; it never writes
            snapshot.remember(backend.name, payload)
    else:
        desk = snapshot.remembered()
        if desk:
            log.warning("no monitor is lit — falling back to the last desktop we saw")
            baseline = desk["payload"]
        else:
            log.warning("no monitor is lit and no remembered desktop — dual will light "
                        "every connected output")

    try:
        hint = provider.create(env, runner, mode)
    except Exception as exc:
        log.error("provider %s could not create a VDD: %s", provider.name, exc)
        return _degrade(args, EXIT_APPLY_FAILED, "VDD creation failed")

    # The provider made a display; the session may still need to adopt it. On
    # X11 a new DRM card is inert until it is sourced from the GPU, so without
    # this the display exists, the kernel has it, and nothing can see it.
    if not args.dry_run:
        backend.attach_new_outputs()

    vdd = backend.wait_for_output(hint) if not args.dry_run else hint
    if vdd is None:
        log.error("VDD (hint %r) never appeared in %s", hint, backend.name)
        provider.destroy(env, runner, {"vdd_output": hint})
        return _degrade(args, EXIT_APPLY_FAILED, "VDD did not appear")

    if not args.dry_run:
        # Record that this one is ours *before* touching the layout: the file is
        # how a later run tells our virtual display from the user's monitors, and
        # a crash between here and teardown is exactly when it gets consulted.
        snapshot.track_vdd(vdd)
        # Save the layout we intend to go *back* to, which is not always the one
        # in front of us: entering dual from an already-dark desk, it is the
        # remembered desktop that has to survive the session, not the darkness.
        snapshot.save(backend.name, baseline, provider=provider.name, vdd_output=vdd)
    try:
        if kind == "headless":
            backend.apply_headless(vdd, mode)
        else:
            backend.apply_dual(vdd, mode, baseline)
    except Exception as exc:  # roll back: never leave the user stranded
        log.error("apply failed (%s); rolling back", exc)
        restored = False
        try:
            backend.restore(baseline)
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


def _known_vdds(env, runner: Runner, provider, stale: Optional[dict]) -> set:
    """Every output that is a virtual display rather than one of the user's.

    Erring in either direction hurts: miss one and a leaked VDD gets counted as
    a lit monitor, so dual relights the ghost and the desk stays dark; claim one
    that is not ours and we destroy somebody's actual screen.  So this is drawn
    from what we *recorded creating*, plus what the display stack itself flags as
    virtual — never from what an output happens to be called.
    """
    names = set(snapshot.tracked_vdds())
    try:
        names |= provider.vdd_outputs(env, runner)
    except Exception as exc:  # a provider that cannot answer must not be fatal
        log.warning("provider %s could not list its VDDs (%s)", provider.name, exc)
    names |= {c.name for c in env.vdd_connectors}
    if stale and stale.get("vdd_output"):
        names.add(stale["vdd_output"])
    return names


def _destroy_orphans(env, runner: Runner, provider, vdds: set, backend) -> None:
    """Tear down virtual displays no session is using any more.

    A crash leaves one lit with no snapshot naming it. It is not the user's, so
    it must not survive into the baseline — but it must not be mistaken for the
    only lit output either, so anything still up gets destroyed before we look.
    """
    live = {o.name for o in backend.outputs() if o.enabled}
    for name in sorted(vdds & live):
        log.warning("tearing down an orphaned virtual display: %s", name)
        try:
            provider.destroy(env, runner, {"vdd_output": name})
            snapshot.untrack_vdd(name)
        except Exception as exc:
            log.warning("could not destroy %s: %s", name, exc)


def _strip(payload: dict, vdds: set) -> dict:
    """The user's own monitors: no virtual display belongs in their baseline."""
    return {"outputs": [o for o in payload.get("outputs", []) if o.get("name") not in vdds]}


def _restore_from(doc: dict, env, runner: Runner) -> None:
    backend_cls = _BACKENDS.get(doc.get("backend", ""))
    if backend_cls is None:
        log.error("snapshot has unknown backend %r — keeping snapshot for manual recovery",
                  doc.get("backend"))
        return
    provider = providers.get_provider(doc.get("provider", ""))
    if provider is not None:
        provider.destroy(env, runner, {"vdd_output": doc.get("vdd_output")})
        if doc.get("vdd_output") and not runner.dry_run:
            snapshot.untrack_vdd(doc["vdd_output"])
    elif doc.get("provider"):
        log.warning("unknown provider %r in snapshot — its VDD may need manual teardown",
                    doc.get("provider"))

    backend = backend_cls(runner)

    # Switch the VDD's output off explicitly. Destroying it is not the same
    # thing — a forced connector is a real port and stays lit — and the baseline
    # deliberately holds only the user's own monitors, so nothing else will.
    #
    # Drop anything the display stack no longer has, too: a layout can outlive
    # the monitor it names (undock, swap a cable), and one stale entry is enough
    # to fail the whole replay and leave the desk dark.
    live = {o.name for o in backend.outputs()}
    outputs = [o for o in doc.get("payload", {}).get("outputs", []) if o.get("name") in live]
    vdd = doc.get("vdd_output")

    # Filtering can eat the only monitor the layout lit — undock the laptop and
    # the remembered desk becomes "eDP-1: off, HDMI-A-1: <gone>", which replays
    # as a black screen. A layout that lights nothing is not a layout to apply;
    # it is a reason to fall back to one that does.
    if not snapshot.is_user_layout({"outputs": outputs}):
        log.warning("this layout lights nothing here (the monitor it named is gone) — "
                    "bringing up what this machine actually has instead")
        try:
            backend.relight({vdd} if vdd else ())
        except Exception as exc:
            log.error("could not relight (%s)", exc)
            return
        if not runner.dry_run:
            snapshot.clear()
        return

    if vdd:
        outputs.append({"name": vdd, "enabled": False})
    try:
        backend.restore({"outputs": outputs})
    except Exception as exc:
        log.error("restore failed (%s) — snapshot kept for retry", exc)
        return
    if not runner.dry_run:
        snapshot.clear()


def cmd_restore(args) -> int:
    doc = snapshot.load()  # discards a poisoned snapshot rather than replaying it
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    if doc is not None:
        _restore_from(doc, env, runner)
        return EXIT_OK

    # No baseline: none was ever taken, or the one on disk was poison and has
    # just been dropped.  Restore is the *undo* command — if the desk is dark
    # it is on us to fix that, and a plausible desktop beats no desktop.
    backend = get_backend(env, runner)
    if backend is None:
        log.info("nothing to restore")
        return EXIT_OK

    provider, _report = providers.choose(env, runner)
    vdds = _known_vdds(env, runner, provider, None) if provider else {
        c.name for c in env.vdd_connectors}

    # `restore` is how a session *ends*, so it owes the user two things: their
    # monitors lit, and no virtual display left on the desk. With no snapshot to
    # replay, do both from scratch rather than shrug — a leftover VDD is a
    # phantom monitor, and a dark desk is the bug this whole path exists for.
    lit_vdds = {o.name for o in backend.outputs() if o.enabled and o.name in vdds}
    if _monitor_is_lit(backend, env, vdds) and not lit_vdds:
        log.info("nothing to restore")
        return EXIT_OK

    if args.dry_run:
        return EXIT_OK

    # Prefer the desk we last saw the user at: it knows which panel they keep
    # dark, where the monitors sit relative to each other, and which one is
    # primary. Lighting everything up is a last resort, not a restore.
    desk = snapshot.remembered()
    if desk:
        log.warning("no snapshot — putting back the last desktop we saw, and removing the VDD")
        _restore_from({"backend": desk["backend"], "payload": desk["payload"],
                       "provider": provider.name if provider else None,
                       "vdd_output": next(iter(sorted(lit_vdds)), None)}, env, runner)
        return EXIT_OK

    log.warning("no snapshot and no remembered desktop — lighting every connected monitor")
    backend.relight(vdds)  # monitors back on, and the orphaned VDD switched off

    # Switching the VDD's output off is not the same as releasing whatever
    # conjured it (an evdi device, a kwin virtual output). The snapshot that
    # named the provider is exactly what we no longer have, so guess — but only
    # once a real monitor is lit, so a failure here cannot leave a dark desk.
    if provider and vdds and _monitor_is_lit(backend, env, vdds):
        _destroy_orphans(env, runner, provider, vdds, backend)
    return EXIT_OK


def _monitor_is_lit(backend, env, vdds: set) -> bool:
    """Is anything the user could actually be looking at switched on?"""
    try:
        return any(o.enabled and o.name not in vdds for o in backend.outputs())
    except Exception as exc:  # a backend that cannot answer is not evidence of darkness
        log.warning("could not read the current layout (%s)", exc)
        return True


def cmd_remember(args) -> int:
    """Pin the desk as it is right now as the layout to come back to.

    Zenith learns this on its own every time it sees a lit desk, but learning it
    from whatever happens to be on screen is not always what the user means —
    so let them say it outright.
    """
    runner = Runner(dry_run=args.dry_run)
    env = detect_mod.detect(runner=runner)
    backend = get_backend(env, runner)
    if backend is None:
        log.error("no layout backend here (see `zenith-display doctor`)")
        return EXIT_NO_BACKEND

    provider, _report = providers.choose(env, runner)
    vdds = _known_vdds(env, runner, provider, None) if provider else {
        c.name for c in env.vdd_connectors}
    payload = _strip(backend.snapshot(), vdds)
    if not snapshot.is_user_layout(payload):
        log.error("no monitor is lit — refusing to remember a dark desk")
        return EXIT_APPLY_FAILED

    if not args.dry_run:
        snapshot.remember(backend.name, payload)
    for out in payload["outputs"]:
        state = f"{out.get('mode') or ''} at {out.get('x', 0)},{out.get('y', 0)}" \
            if out.get("enabled") else "off"
        print(f"  {out['name']:<12} {state}{'  (primary)' if out.get('primary') else ''}")
    print("remembered — restore and dual will put this back, exactly this.")
    return EXIT_OK


def cmd_forget(args) -> int:
    if not args.dry_run:
        snapshot.forget()
    print("forgotten — Zenith will relearn your desktop the next time it sees one.")
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

    pending = [e["provider"] for e in report if e.get("reboot_required")]
    if pending:
        # The install worked; it is simply not live yet. Saying "no provider is
        # ready" here would send the user hunting for a problem that does not
        # exist.
        print(f"\nsetup installed {', '.join(pending)}, but an image-based system "
              "only picks up a kernel module at boot.")
        print("Reboot, then re-run `zenith-display doctor` to confirm.")
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
    nv_version = detect_mod.nvidia_driver_version()
    if nv_version and not detect_mod.nvenc_supported(nv_version):
        print(f"\n  encoder   : NVIDIA driver {nv_version} is older than "
              f"{detect_mod.NVENC_MIN_DRIVER} — nvenc will refuse and streams")
        print("  silently fall back to CPU encoding (libx264). Update the driver")
        print("  to get hardware encoding back.")
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
    for name in ("probe", "plan", "headless", "dual", "restore", "remember", "forget",
                 "setup", "doctor"):
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
        "remember": cmd_remember,
        "forget": cmd_forget,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
