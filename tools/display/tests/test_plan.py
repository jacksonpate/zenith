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


def test_forced_connector_always_first_in_chain():
    chain = chain_for(_env())
    assert chain[0].name == "forced-connector"
    assert chain[-2].name == "evdi"
    assert chain[-1].name == "drm-debugfs"


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
