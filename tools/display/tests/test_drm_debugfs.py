"""The spare-connector VDD: a real port, borrowed for the length of a session.

Strictly better than evdi wherever a machine has a free connector. The display
lives on the GPU that will encode it — so no cross-device buffer import, which
is what turns an evdi VDD into a black screen on a discrete GPU — and it needs
no kernel module, so no packaging, no DKMS, and no Secure Boot enrollment.
"""

import pytest
from conftest import FakeRunner

from zenith_display.detect import Connector, Environment
from zenith_display.modes import Mode
from zenith_display.providers import chain_for
from zenith_display.providers.drm_debugfs import DrmDebugfsProvider


def _env(**kw):
    defaults = dict(session_type="wayland", desktop="kde", distro="fedora", tools={},
                    connectors=[], is_root=False, has_passwordless_sudo=False)
    defaults.update(kw)
    return Environment(**defaults)


def _spare(name="DP-1", driver="nvidia"):
    return Connector(sysfs=f"/sys/class/drm/card1-{name}", name=name,
                     status="disconnected", enabled=False, driver=driver)


@pytest.fixture
def helper(monkeypatch):
    """The machine has the privileged helper installed."""
    from zenith_display.providers import drm_debugfs
    monkeypatch.setattr(drm_debugfs, "_helper", lambda: "/usr/local/bin/zenith-drm-vdd")


def test_the_helper_is_enough_no_blanket_root_needed(helper):
    """It used to demand passwordless sudo for *everything*, so on a machine
    that only grants the one narrow helper it reported 'requires root' and never
    fired — which is exactly the machine it was designed for."""
    ok, reason = DrmDebugfsProvider().probe(_env(connectors=[_spare()]), FakeRunner())
    assert ok, reason
    assert "DP-1" in reason


def test_no_spare_connector_means_no(helper):
    ok, reason = DrmDebugfsProvider().probe(_env(connectors=[]), FakeRunner())
    assert not ok and "borrowable" in reason


def test_create_drives_the_helper_not_a_shell(helper, tmp_path, monkeypatch):
    """The sudoers rule permits exactly one command. Anything that shells out to
    `sudo sh -c 'cat > /sys/...'` is both unrunnable here and a hole wide enough
    to write any file on the system."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    runner = FakeRunner()
    name = DrmDebugfsProvider().create(_env(connectors=[_spare()]), runner, Mode(2420, 1668, 120))
    assert name == "DP-1"
    argv = runner.trace[-1]
    assert argv[:2] == ["sudo", "-n"]
    assert argv[2].endswith("zenith-drm-vdd")
    assert argv[3:5] == ["on", "DP-1"]
    assert not any(a == "sh" for a in argv), f"must not shell out: {argv}"


def test_destroy_tears_the_connector_back_down(helper):
    runner = FakeRunner()
    DrmDebugfsProvider().destroy(_env(connectors=[_spare()]), runner, {"vdd_output": "DP-1"})
    argv = runner.trace[-1]
    assert argv[3:5] == ["off", "DP-1"]


def test_a_spare_connector_outranks_evdi():
    """No kernel module, no packaging, no Secure Boot — and the display lives on
    the GPU that encodes it, so the buffer never has to cross devices."""
    names = [p.name for p in chain_for(_env(connectors=[_spare()]))]
    assert names.index("drm-debugfs") < names.index("evdi")


def test_it_never_borrows_the_laptop_panel(helper):
    panel = Connector(sysfs="/sys/class/drm/card1-eDP-1", name="eDP-1",
                      status="disconnected", enabled=False, driver="nvidia")
    ok, _ = DrmDebugfsProvider().probe(_env(connectors=[panel]), FakeRunner())
    assert not ok
