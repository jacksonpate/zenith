"""EVDI bootstrap: the one provider that can create and destroy a display."""

import pytest
from conftest import FakeRunner

from zenith_display.detect import Environment
from zenith_display.providers import evdi
from zenith_display.runner import Result


def _env(**kw):
    defaults = dict(session_type="wayland", desktop="kde", distro="fedora",
                    tools={}, connectors=[], is_root=True, has_passwordless_sudo=True)
    defaults.update(kw)
    return Environment(**defaults)


def _runner_without_the_module(**responses):
    """A machine where `modprobe evdi` fails — i.e. the module is not installed.

    FakeRunner answers every command with success unless told otherwise, so
    without this modprobe "works", ensure() concludes it has nothing to do, and
    the install path is never reached: the test would pass while testing nothing.
    """
    canned = {("modprobe", "evdi"): Result(argv=["modprobe", "evdi"], returncode=1)}
    canned.update(responses)
    return FakeRunner(canned)


@pytest.fixture
def ostree(monkeypatch):
    """An image-based Fedora: rpm-ostree, and no dnf anywhere."""
    monkeypatch.setattr(evdi, "_is_ostree", lambda: True)
    monkeypatch.setattr(evdi, "_module_loaded", lambda: False)
    monkeypatch.setattr(evdi, "_add_writable", lambda: True)


def test_ostree_layers_the_module_instead_of_giving_up(ostree):
    """Silverblue has no dnf, so the package loop matched nothing and evdi was
    uninstallable — the reference machine had no provider at all as a result."""
    provider = evdi.EvdiProvider()
    runner = _runner_without_the_module()
    provider.ensure(_env(), runner)
    layered = [t for t in runner.trace if t[:2] == ["rpm-ostree", "install"]]
    assert layered, f"nothing was layered; ran: {runner.trace}"
    assert "akmod-evdi" in layered[0]


def test_a_layered_module_needs_a_reboot_and_says_so(ostree):
    """rpm-ostree writes a NEW deployment. Nothing changes in the running one,
    so the module cannot load until the machine reboots. Reporting plain failure
    would be a lie — the install worked."""
    provider = evdi.EvdiProvider()
    provider.ensure(_env(), _runner_without_the_module())
    assert provider.reboot_required is True


def test_a_normal_distro_does_not_use_rpm_ostree(monkeypatch):
    monkeypatch.setattr(evdi, "_is_ostree", lambda: False)
    monkeypatch.setattr(evdi, "_module_loaded", lambda: False)
    monkeypatch.setattr(evdi, "_add_writable", lambda: True)
    monkeypatch.setattr(evdi, "which", lambda tool: "/usr/bin/apt-get"
                        if tool == "apt-get" else None)
    provider = evdi.EvdiProvider()
    runner = _runner_without_the_module()
    provider.ensure(_env(), runner)
    assert any(t[:2] == ["apt-get", "install"] for t in runner.trace)
    assert not any(t[0] == "rpm-ostree" for t in runner.trace)
    assert provider.reboot_required is False


def test_setup_reports_a_pending_reboot_as_success(ostree, capsys, monkeypatch):
    """A layered module that only needs a reboot is not a failed setup. Printing
    'no provider is ready' sends the user hunting for a problem that does not
    exist.

    Pinned to a synthetic machine on purpose: `cmd_setup` detects the real host,
    so without this the result depends on whatever displays the developer
    happens to have plugged in.
    """
    from types import SimpleNamespace

    from zenith_display import cli, providers

    monkeypatch.setattr(cli.detect_mod, "detect", lambda runner=None: _env())
    monkeypatch.setattr(providers, "chain_for", lambda env: [evdi.EvdiProvider()])
    monkeypatch.setattr(cli, "Runner", lambda dry_run=False: _runner_without_the_module())

    rc = cli.cmd_setup(SimpleNamespace(dry_run=False, strict=False, verbose=False))
    out = capsys.readouterr().out
    assert "reboot" in out.lower(), out
    assert rc == cli.EXIT_OK
