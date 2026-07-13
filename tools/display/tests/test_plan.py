"""Backend/provider selection matrix — the "which path does X's box take"."""

from conftest import FakeRunner

from zenith_display.detect import Connector, Environment
from zenith_display.layouts import get_backend
from zenith_display.providers import chain_for
from zenith_display.providers.forced_connector import ForcedConnectorProvider


def _env(**kw):
    defaults = dict(session_type="wayland", desktop="kde", distro="fedora",
                    tools={}, connectors=[], is_root=False, has_passwordless_sudo=False)
    defaults.update(kw)
    return Environment(**defaults)


def vdd_connector(name="DP-1"):
    return Connector(sysfs=f"/sys/class/drm/card1-{name}", name=name,
                     status="connected", enabled=True, monitor="ZenithVDD", is_vdd=True)


def test_silverblue_shape_picks_kscreen():
    env = _env(desktop="kde", tools={"kscreen-doctor": True})
    assert get_backend(env, FakeRunner()).name == "kscreen"


def test_cinnamon_mint_shape_picks_xrandr():
    env = _env(session_type="x11", desktop="x-cinnamon", tools={"xrandr": True})
    assert get_backend(env, FakeRunner()).name == "xrandr"


def test_sway_shape_picks_wlr():
    env = _env(desktop="sway", tools={"wlr-randr": True})
    assert get_backend(env, FakeRunner()).name == "wlr"


def test_tty_has_no_backend():
    env = _env(session_type="tty", desktop="", tools={"xrandr": True})
    assert get_backend(env, FakeRunner()) is None


def test_evdi_outranks_the_forced_connector():
    """A forced connector is permanent hardware: it cannot be created, cannot be
    destroyed, and there can only ever be one. evdi can do all three. Preferring
    the forced connector is why a VDD never died when the app quit."""
    names = [p.name for p in chain_for(_env())]
    assert names.index("evdi") < names.index("forced-connector")
    # ...and a borrowed port beats a fabricated device, so it comes first of all:
    # same GPU as the encoder, and no kernel module to install or sign.
    assert names.index("drm-debugfs") < names.index("evdi")


def test_a_provisioned_connector_is_still_a_fallback():
    """Machines already provisioned with a forced connector keep working — it is
    demoted, not deleted."""
    assert "forced-connector" in [p.name for p in chain_for(_env())]


def test_chain_includes_compositor_native_for_sway():
    names = [p.name for p in chain_for(_env(desktop="sway", tools={"swaymsg": True}))]
    assert "sway" in names


def test_forced_connector_probe_uses_edid_scan():
    provider = ForcedConnectorProvider()
    ok, reason = provider.probe(_env(connectors=[vdd_connector()]), FakeRunner())
    assert ok and "DP-1" in reason
    ok, _ = provider.probe(_env(connectors=[]), FakeRunner())
    assert not ok


def test_forced_connector_create_returns_connector_name():
    provider = ForcedConnectorProvider()
    env = _env(connectors=[vdd_connector("DP-3")])
    assert provider.create(env, FakeRunner(), None) == "DP-3"


def test_nvenc_supported_thresholds():
    from zenith_display import detect

    assert detect.nvenc_supported("570.86.16")
    assert detect.nvenc_supported("550.163.01")  # Debian 13 stable
    assert detect.nvenc_supported("535.216.01")  # Ubuntu 24.04 GA
    assert detect.nvenc_supported("520.56.06")   # SDK 12.0 floor
    assert not detect.nvenc_supported("515.86.01")
    assert not detect.nvenc_supported("470.256.02")
    assert detect.nvenc_supported("weird-vendor-string")  # never warn on guesswork


def test_nvidia_driver_version_missing(tmp_path):
    from zenith_display import detect

    assert detect.nvidia_driver_version(str(tmp_path / "nope")) == ""
    p = tmp_path / "version"
    p.write_text("550.163.01\n")
    assert detect.nvidia_driver_version(str(p)) == "550.163.01"


def test_forced_connector_does_not_steal_evdis_leftover_connector():
    """An evdi connector keeps the ZenithVDD EDID long after its session ends,
    so it looks provisioned — but it is dead without a holder, and this provider
    cannot start one. Claiming it (from the front of the chain) would hand every
    session after the first a display that can never produce a frame."""
    provider = ForcedConnectorProvider()
    leftover = Connector(sysfs="/sys/class/drm/card1-DVI-I-1", name="DVI-I-1",
                         status="disconnected", enabled=False, monitor="ZenithVDD",
                         is_vdd=True, driver="evdi")
    ok, reason = provider.probe(_env(connectors=[leftover]), FakeRunner())
    assert not ok and "evdi" in reason


def test_forced_connector_still_claims_a_real_provisioned_connector():
    provider = ForcedConnectorProvider()
    real = Connector(sysfs="/sys/class/drm/card0-DP-1", name="DP-1", status="connected",
                     enabled=True, monitor="ZenithVDD", is_vdd=True, driver="amdgpu")
    ok, reason = provider.probe(_env(connectors=[real]), FakeRunner())
    assert ok and "DP-1" in reason
