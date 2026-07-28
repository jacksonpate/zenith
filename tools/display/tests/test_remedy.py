"""Detecting the problems, and running the exact command that fixes each one.

The bug this exists for: a source build installed to /usr/local/bin has no file
capabilities, because only the packaging grants them. KMS capture then cannot
open a DRM framebuffer, capture falls back to the portal, present-paced capture
turns off with it, and nothing fails — the stream is simply slower forever. So
these tests care as much about *noticing* as about fixing.
"""

import errno
import os
import struct

import pytest
from conftest import FakeRunner

from zenith_display import remedy
from zenith_display.cli import EXIT_APPLY_FAILED, EXIT_NEEDS_FIXING, EXIT_OK, _block, _offer
from zenith_display.detect import VENDOR_AMD, VENDOR_NVIDIA, Connector, Environment


def _env(**kw):
    defaults = dict(session_type="wayland", desktop="kde", distro="fedora", tools={},
                    connectors=[], is_root=False, has_passwordless_sudo=False)
    defaults.update(kw)
    return Environment(**defaults)


def _cap_xattr(*bits) -> bytes:
    """A VFS v2 capability blob granting exactly these capability bits."""
    permitted = 0
    for bit in bits:
        permitted |= 1 << bit
    return struct.pack("<III II", 0x02000000, permitted, 0, 0, 0)


# ---------------------------------------------------------------- capabilities


def test_a_binary_with_no_capabilities_is_missing_all_of_them(monkeypatch):
    """The measured case: `getcap` prints nothing, and the log says
    'Failed to gain CAP_SYS_ADMIN' three times without ever saying why."""
    def no_xattr(path, name):
        raise OSError(errno.ENODATA, "no data available")

    monkeypatch.setattr(os, "getxattr", no_xattr)
    assert remedy.missing_caps("/usr/local/bin/zenith") == list(remedy.REQUIRED_CAPS)


def test_a_fully_capable_binary_is_missing_none(monkeypatch):
    monkeypatch.setattr(os, "getxattr", lambda p, n: _cap_xattr(
        remedy.CAP_SYS_ADMIN, remedy.CAP_SYS_NICE))
    assert remedy.missing_caps("/usr/local/bin/zenith") == []


def test_a_partially_capable_binary_names_only_what_is_missing(monkeypatch):
    """cap_sys_admin alone gets KMS capture back but leaves the encoder thread at
    ordinary priority, so the gap is worth reporting precisely."""
    monkeypatch.setattr(os, "getxattr", lambda p, n: _cap_xattr(remedy.CAP_SYS_ADMIN))
    assert remedy.missing_caps("/usr/local/bin/zenith") == ["cap_sys_nice"]


def test_unreadable_capabilities_are_unknown_not_healthy(monkeypatch):
    """A filesystem without xattr support cannot answer. Reporting that as "all
    present" would hide the one bug this module was written to catch."""
    def denied(path, name):
        raise OSError(errno.EOPNOTSUPP, "not supported")

    monkeypatch.setattr(os, "getxattr", denied)
    assert remedy.missing_caps("/usr/local/bin/zenith") is None


def test_a_truncated_capability_blob_is_unknown(monkeypatch):
    monkeypatch.setattr(os, "getxattr", lambda p, n: b"\x00\x00")
    assert remedy.missing_caps("/usr/local/bin/zenith") is None


def test_the_remedy_is_the_command_the_packaging_would_have_run(monkeypatch):
    """cmake/packaging/linux.cmake grants cap_sys_admin,cap_sys_nice+p. A source
    build must end up in the same state, by the same means."""
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: "/usr/local/bin/zenith")
    monkeypatch.setattr(remedy, "missing_caps", lambda p: list(remedy.REQUIRED_CAPS))
    rem = remedy.check_capabilities(_env())
    assert rem is not None
    assert rem.commands == [["setcap", "cap_sys_admin,cap_sys_nice+p", "/usr/local/bin/zenith"]]
    assert "setcap cap_sys_admin,cap_sys_nice+p" in rem.shell()


def test_no_remedy_when_the_binary_is_already_capable(monkeypatch):
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: "/usr/local/bin/zenith")
    monkeypatch.setattr(remedy, "missing_caps", lambda p: [])
    assert remedy.check_capabilities(_env()) is None


def test_no_remedy_when_no_binary_is_installed_yet(monkeypatch):
    """Running `doctor` from a clone before building anything is fine, and must
    not invent a fix for a file that does not exist."""
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: None)
    assert remedy.check_capabilities(_env()) is None


def test_applying_a_remedy_escalates_only_when_not_root():
    monkeypatch_env = _env(is_root=False)
    runner = FakeRunner()
    rem = remedy.Remedy(key="k", problem="p", detail="d",
                        commands=[["setcap", "x", "/usr/local/bin/zenith"]])
    assert rem.apply(monkeypatch_env, runner)
    assert runner.trace[-1][0] == "sudo"

    runner = FakeRunner()
    assert rem.apply(_env(is_root=True), runner)
    assert runner.trace[-1][0] == "setcap", "root does not need sudo"


# ------------------------------------------------------------- GPU affinity


def _conn(name, card, driver, vendor, status="disconnected"):
    return Connector(sysfs=f"/sys/class/drm/{card}-{name}", name=name, status=status,
                     enabled=False, driver=driver, card=card, vendor=vendor)


def test_it_warns_when_no_free_port_is_on_the_encoding_gpu():
    """Not fixable by any command — if a port on the encoder's card were free the
    provider would already have taken it. But the user is owed the reason, or
    they conclude the host is too slow and go looking in the wrong place."""
    env = _env(connectors=[_conn("DP-1", "card1", "amdgpu", VENDOR_AMD),
                           _conn("HDMI-A-1", "card0", "nvidia", VENDOR_NVIDIA,
                                 status="connected")],
               encoder_card="card0")
    adv = remedy.check_gpu_affinity(env)
    assert adv is not None
    assert "card1" in adv.problem and "card0" in adv.problem
    assert "system memory" in adv.detail


def test_no_warning_when_the_encoding_gpu_has_a_free_port():
    env = _env(connectors=[_conn("DP-6", "card0", "nvidia", VENDOR_NVIDIA),
                           _conn("DP-1", "card1", "amdgpu", VENDOR_AMD)],
               encoder_card="card0")
    assert remedy.check_gpu_affinity(env) is None


def test_no_warning_when_nothing_will_hardware_encode():
    env = _env(connectors=[_conn("DP-1", "card1", "amdgpu", VENDOR_AMD)], encoder_card="")
    assert remedy.check_gpu_affinity(env) is None


# ------------------------------------------------------------------ the offer


@pytest.fixture
def capable(monkeypatch):
    """A machine whose only problem is the missing capabilities."""
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: "/usr/local/bin/zenith")
    monkeypatch.setattr(remedy, "missing_caps", lambda p: list(remedy.REQUIRED_CAPS))


def test_outside_a_terminal_it_says_what_to_run_and_changes_nothing(capable, monkeypatch):
    """A package hook, CI, or a pipe: there is nobody to ask, so it must not
    block on input — and it must exit with something a script can branch on."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    runner = FakeRunner()
    rem = remedy.check_capabilities(_env())
    assert _offer([rem], _env(), runner, assume_yes=False) == EXIT_NEEDS_FIXING
    assert runner.trace == [], "must not run anything without consent"


def test_yes_applies_without_asking(capable):
    runner = FakeRunner()
    rem = remedy.check_capabilities(_env())
    assert _offer([rem], _env(), runner, assume_yes=True) == EXIT_OK
    assert any("setcap" in " ".join(argv) for argv in runner.trace)


def test_declining_the_offer_runs_nothing(capable, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    runner = FakeRunner()
    rem = remedy.check_capabilities(_env())
    assert _offer([rem], _env(), runner, assume_yes=False) == EXIT_NEEDS_FIXING
    assert runner.trace == []


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes"])
def test_accepting_the_offer_applies_it(capable, monkeypatch, answer):
    """Enter means yes: this is the one prompt standing between a fresh clone and
    a working host, so the default has to be the one that fixes it."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    runner = FakeRunner()
    rem = remedy.check_capabilities(_env())
    assert _offer([rem], _env(), runner, assume_yes=False) == EXIT_OK
    assert any("setcap" in " ".join(argv) for argv in runner.trace)


def test_an_interrupted_prompt_is_a_no(capable, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def interrupt(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    runner = FakeRunner()
    assert _offer([remedy.check_capabilities(_env())], _env(), runner,
                  assume_yes=False) == EXIT_NEEDS_FIXING
    assert runner.trace == []


def test_a_failing_fix_reports_which_one_and_keeps_going(capable, monkeypatch):
    """Half-fixed is a real state, and the user needs to know which half."""
    from zenith_display.runner import Result

    class Failing(FakeRunner):
        def run(self, argv, timeout=15.0, check=False, mutating=True):
            self.trace.append(list(argv))
            return Result(argv=argv, returncode=1)

    runner = Failing()
    assert _offer([remedy.check_capabilities(_env())], _env(), runner,
                  assume_yes=True) == EXIT_APPLY_FAILED


def test_nothing_to_offer_is_success():
    assert _offer([], _env(), FakeRunner(), assume_yes=False) == EXIT_OK


def test_detail_paragraphs_are_indented_as_one_block():
    assert _block("first\nsecond", "  ") == "  first\n  second"


# ------------------------------------------------------------------- CUDA


def _nvidia_env(**kw):
    return _env(connectors=[_conn("DP-6", "card0", "nvidia", VENDOR_NVIDIA)],
                encoder_card="card0", **kw)


def _fake_binary(tmp_path, *markers) -> str:
    path = tmp_path / "zenith"
    path.write_bytes(b"padding" * 500 + b"".join(markers) + b"tail" * 500)
    return str(path)


def test_a_build_without_cuda_is_reported_on_an_nvidia_host(tmp_path, monkeypatch):
    """kmsgrab refuses NVENC outright without CUDA and reverts to GPU -> RAM ->
    GPU, so zero-copy is unreachable however well the VDD is placed. The marker
    string exists only inside `#ifndef SUNSHINE_BUILD_CUDA`, which makes its
    presence proof rather than inference."""
    binary = _fake_binary(tmp_path, remedy._KMS_MARKER, remedy._NO_CUDA_MARKER)
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: binary)
    adv = remedy.check_cuda(_nvidia_env())
    assert adv is not None
    assert "without CUDA" in adv.problem
    assert "GPU -> RAM -> GPU" in adv.detail


def test_a_build_with_cuda_says_nothing(tmp_path, monkeypatch):
    binary = _fake_binary(tmp_path, remedy._KMS_MARKER)
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: binary)
    assert remedy.check_cuda(_nvidia_env()) is None


def test_a_build_without_the_kms_path_is_not_diagnosed_as_missing_cuda(tmp_path, monkeypatch):
    """No kmsgrab means the marker could not be there whatever CUDA did, so its
    absence says nothing. Reporting "built with CUDA" here would be a guess."""
    binary = _fake_binary(tmp_path, b"unrelated content")
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: binary)
    assert remedy.check_cuda(_nvidia_env()) is None


def test_an_amd_host_is_not_asked_for_cuda(tmp_path, monkeypatch):
    """vaapi needs no CUDA, so the warning would be pure noise."""
    binary = _fake_binary(tmp_path, remedy._KMS_MARKER, remedy._NO_CUDA_MARKER)
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: binary)
    env = _env(connectors=[_conn("DP-1", "card1", "amdgpu", VENDOR_AMD)], encoder_card="card1")
    assert remedy.check_cuda(env) is None


def test_a_marker_split_across_two_reads_is_still_found(tmp_path, monkeypatch):
    """The file is scanned a megabyte at a time; a marker straddling a boundary
    must not fall through the gap."""
    path = tmp_path / "zenith"
    chunk = 1 << 20
    head = b"x" * (chunk - len(remedy._NO_CUDA_MARKER) // 2)
    path.write_bytes(head + remedy._NO_CUDA_MARKER + b"y" * 64 + remedy._KMS_MARKER)
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: str(path))
    assert remedy.check_cuda(_nvidia_env()) is not None


def test_an_unreadable_binary_is_not_diagnosed(tmp_path, monkeypatch):
    monkeypatch.setattr(remedy, "find_binary", lambda cwd=None: str(tmp_path / "nope"))
    assert remedy.check_cuda(_nvidia_env()) is None
