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


def _spare(name="DP-1", driver="nvidia", card="card1", vendor=""):
    return Connector(sysfs=f"/sys/class/drm/{card}-{name}", name=name,
                     status="disconnected", enabled=False, driver=driver,
                     card=card, vendor=vendor)


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


def test_it_borrows_a_port_on_the_gpu_that_encodes(helper):
    """A hybrid laptop's iGPU sorts first by connector name, and that is how the
    virtual display ended up on the card that was not encoding.

    Measured on an RTX 4050 + Radeon 680M laptop: the AMD iGPU owns DP-1..DP-5,
    the NVIDIA dGPU owns DP-6, and NVENC takes the session. Ranked by name alone
    the VDD lands on DP-1, and because a dma-buf from the iGPU cannot be imported
    into CUDA the whole stream silently drops to memory buffers — a full readback
    per frame, which holds up at 1080p and collapses at 2420x1668.
    """
    amd = [_spare(f"DP-{n}", driver="amdgpu", card="card1") for n in range(1, 6)]
    nvidia = _spare("DP-6", driver="nvidia", card="card0", vendor="0x10de")
    env = _env(connectors=[*amd, nvidia], encoder_card="card0")

    ok, reason = DrmDebugfsProvider().probe(env, FakeRunner())
    assert ok, reason
    assert "DP-6" in reason, f"must borrow the encoder GPU's port, got: {reason}"


def test_a_cross_gpu_port_still_works_but_says_so(helper):
    """Every port on the encoder's GPU occupied. Borrowing the other card's is
    still the right answer — a working stream beats no stream — but the cost is
    invisible from outside and reads as a host that cannot keep up, so name it."""
    env = _env(connectors=[_spare("DP-1", driver="amdgpu", card="card1")],
               encoder_card="card0")
    ok, reason = DrmDebugfsProvider().probe(env, FakeRunner())
    assert ok, reason
    assert "DP-1" in reason
    assert "system memory" in reason, f"the penalty must be stated: {reason}"


def test_no_encoder_card_falls_back_to_ranking_by_name(helper):
    """Software encoding, or a GPU we do not recognise: there is no card to
    prefer, so the old ordering stands rather than becoming arbitrary."""
    env = _env(connectors=[_spare("DP-2", card="card1"), _spare("DP-1", card="card1")],
               encoder_card="")
    ok, reason = DrmDebugfsProvider().probe(env, FakeRunner())
    assert ok and "DP-1" in reason


def test_reclaims_our_own_stale_vdd_before_taking_a_fresh_port(helper):
    """A forced connector we never released is not a spare — and left alone it
    becomes a permanent fixture showing the last client's resolution. Within the
    encoder's GPU, ours comes first."""
    ours = Connector(sysfs="/sys/class/drm/card0-DP-7", name="DP-7", status="connected",
                     enabled=True, monitor="Zenith-VDD", is_vdd=True, driver="nvidia",
                     card="card0", vendor="0x10de")
    fresh = _spare("DP-6", driver="nvidia", card="card0", vendor="0x10de")
    env = _env(connectors=[fresh, ours], encoder_card="card0")
    ok, reason = DrmDebugfsProvider().probe(env, FakeRunner())
    assert ok and "DP-7" in reason, f"must reclaim our own VDD first: {reason}"


def test_it_never_borrows_the_laptop_panel(helper):
    panel = Connector(sysfs="/sys/class/drm/card1-eDP-1", name="eDP-1",
                      status="disconnected", enabled=False, driver="nvidia")
    ok, _ = DrmDebugfsProvider().probe(_env(connectors=[panel]), FakeRunner())
    assert not ok


def test_setup_installs_the_helper_even_though_root_could_manage_without_it(monkeypatch):
    """`sudo zenith-display setup` printed "provider ready: drm-debugfs" and
    installed nothing.

    Setup runs as root; streaming does not. Root can write the two kernel files
    directly, so probe() said yes — and because ensure() only ran when probe()
    said no, the helper and its sudoers rule were never installed. The user, who
    is not root, then found no helper and fell through to evdi: a kernel module,
    on hardware that needed none.

    ensure() is what setup is *for*. It must run whether or not probe passes.
    """
    from zenith_display import providers
    from zenith_display.providers import drm_debugfs

    ensured = []

    class Fake(drm_debugfs.DrmDebugfsProvider):
        def probe(self, env, runner):
            # What root sees before setup: capable, but nothing installed.
            return (True, "can borrow DP-1") if ensured else (False, "no helper")

        def ensure(self, env, runner):
            ensured.append(True)
            return True

    monkeypatch.setattr(providers, "chain_for", lambda env: [Fake()])
    chosen, _report = providers.choose(_root_env(), FakeRunner({}), bootstrap=True)

    assert ensured, "setup must install the helper, not merely ask root if it could cope"
    assert chosen is not None


def test_root_without_the_helper_is_not_ready(monkeypatch):
    """`_run` drives the helper even as root, so "I am root" was never the same
    thing as "this will work" — it just looked like it."""
    from zenith_display.providers import drm_debugfs

    monkeypatch.setattr(drm_debugfs, "_helper", lambda: None)
    ok, reason = drm_debugfs.DrmDebugfsProvider().probe(_root_env(), FakeRunner({}))
    assert not ok
    assert "helper" in reason


def _root_env():
    from zenith_display.detect import Connector, Environment
    return Environment(
        session_type="wayland", desktop="kde", distro="fedora", tools={},
        connectors=[Connector(sysfs="/sys/class/drm/card1-DP-1", name="DP-1",
                              status="disconnected", enabled=False, monitor=None,
                              is_vdd=False, driver="nvidia")],
        is_root=True, has_passwordless_sudo=True)
