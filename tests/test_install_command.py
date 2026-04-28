"""Tests for the WezTerm bundle installer + ``daimon install`` CLI command.

We don't hit the real GitHub API or download the real bundle — every
network call is monkeypatched. The tests verify:

  * Platform detection covers linux/macos/windows × x86_64/aarch64.
  * ``install_bundle()`` short-circuits when the marker matches latest.
  * ``--force`` defeats the short-circuit.
  * SHA mismatch refuses to install (test by mutating the verified digest).
  * ``daimon install`` exits 2 on installer error and 0 on success.
  * ``daimon doctor`` reports installed / not-installed states correctly.
"""

from __future__ import annotations

import io
import os
import platform
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from daimon.cli import main
from daimon.install import installer
from daimon.render import wezterm_bundle as wb
from daimon.update.api import ReleaseInfo


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Redirect ~/.daimon to tmp_path."""
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers — fake tarball + fake release object
# ---------------------------------------------------------------------------


def _make_fake_bundle_bytes() -> bytes:
    """Tar.gz containing wezterm + wezterm-gui scripts that exit 0 and print version."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in ("wezterm", "wezterm-gui"):
            content = (
                b"#!/bin/sh\n"
                b"if [ \"$1\" = \"--version\" ]; then\n"
                b"  echo 'wezterm 99.99.99-fakerelease'\n"
                b"  exit 0\n"
                b"fi\n"
                b"echo unknown_arg\n"
                b"exit 1\n"
            )
            ti = tarfile.TarInfo(name=name)
            ti.size = len(content)
            ti.mode = 0o755
            tf.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def _release(tag="wezterm-bundle-v1.0", asset_name=None) -> ReleaseInfo:
    asset = asset_name or installer.bundle_asset_name()
    return ReleaseInfo(
        tag=tag,
        version=(1, 0),
        published_at="2026-04-25T00:00:00Z",
        asset_url=f"https://example.invalid/{asset}",
        asset_api_url="",
        asset_size=0,
        sha256_url=f"https://example.invalid/{asset}.sha256",
        sha256_api_url="",
        body="",
    )


class _FakeResp:
    """Minimal stand-in for a urllib response object."""
    def __init__(self, payload: bytes):
        self._payload = payload
        self._read = False

    def read(self, n: int = -1) -> bytes:
        if self._read:
            return b""
        if n < 0 or n >= len(self._payload):
            self._read = True
            return self._payload
        # Single-shot for simplicity — real downloader handles partial reads via loop.
        out = self._payload[:n]
        self._payload = self._payload[n:]
        if not self._payload:
            self._read = True
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def test_detect_platform_linux_x86_64(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert installer.detect_platform() == ("linux", "x86_64")


def test_detect_platform_macos_arm64(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert installer.detect_platform() == ("macos", "aarch64")


def test_detect_platform_windows_amd64(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert installer.detect_platform() == ("windows", "x86_64")


def test_detect_platform_unsupported_os(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    with pytest.raises(installer.BundleInstallError, match="unsupported OS"):
        installer.detect_platform()


def test_detect_platform_unsupported_arch(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    with pytest.raises(installer.BundleInstallError, match="unsupported arch"):
        installer.detect_platform()


def test_bundle_asset_name_format(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert installer.bundle_asset_name() == "daimon-wezterm-linux-x86_64.tar.gz"


def test_bundle_asset_name_explicit():
    assert installer.bundle_asset_name("macos", "aarch64") == "daimon-wezterm-macos-aarch64.tar.gz"


# ---------------------------------------------------------------------------
# Repo / pin overrides
# ---------------------------------------------------------------------------


def test_bundle_repo_default():
    assert installer.bundle_repo() == "aurorasuperbot/daimon"


def test_bundle_repo_env_override(monkeypatch):
    monkeypatch.setenv("DAIMON_BUNDLE_REPO", "fork/repo")
    assert installer.bundle_repo() == "fork/repo"


def test_pinned_bundle_version_env(monkeypatch):
    monkeypatch.setenv("DAIMON_PIN_BUNDLE", "wezterm-bundle-v1.2.3")
    assert installer.pinned_bundle_version() == "wezterm-bundle-v1.2.3"


def test_pinned_bundle_version_unset(monkeypatch):
    monkeypatch.delenv("DAIMON_PIN_BUNDLE", raising=False)
    assert installer.pinned_bundle_version() is None


# ---------------------------------------------------------------------------
# is_up_to_date short-circuit
# ---------------------------------------------------------------------------


def test_is_up_to_date_false_when_not_installed(fake_root):
    rel = _release("wezterm-bundle-v1.0")
    assert installer.is_up_to_date(rel) is False


def test_is_up_to_date_true_when_marker_matches(fake_root, tmp_path):
    bin_path = wb.wezterm_bin()
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n")
    if platform.system() != "Windows":
        bin_path.chmod(0o755)
    wb.version_marker_path().write_text("wezterm-bundle-v1.0\n")
    rel = _release("wezterm-bundle-v1.0")
    assert installer.is_up_to_date(rel) is True


def test_is_up_to_date_false_when_marker_mismatches(fake_root):
    bin_path = wb.wezterm_bin()
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n")
    if platform.system() != "Windows":
        bin_path.chmod(0o755)
    wb.version_marker_path().write_text("wezterm-bundle-v0.9\n")
    rel = _release("wezterm-bundle-v1.0")
    assert installer.is_up_to_date(rel) is False


# ---------------------------------------------------------------------------
# install_bundle — happy path with monkeypatched network
# ---------------------------------------------------------------------------


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="POSIX shell scripts in fake tarball")
def test_install_bundle_happy_path(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()

    # Real sha256 of our bundle.
    import hashlib
    real_sha = hashlib.sha256(bundle_bytes).hexdigest()
    sidecar_text = f"{real_sha}  daimon-wezterm-linux-x86_64.tar.gz\n"

    rel = _release("wezterm-bundle-v1.0")

    def fake_resolve(version):
        return rel

    def fake_http_open(url, octet_stream=False):
        if url.endswith(".sha256"):
            return _FakeResp(sidecar_text.encode("utf-8"))
        return _FakeResp(bundle_bytes)

    monkeypatch.setattr(installer, "_resolve_release", fake_resolve)
    monkeypatch.setattr(installer, "_http_open", fake_http_open)

    report = installer.install_bundle()
    assert report.tag == "wezterm-bundle-v1.0"
    assert report.skipped_download is False
    assert report.sha256 == real_sha
    assert wb.is_installed()
    assert wb.installed_version() == "wezterm-bundle-v1.0"
    assert "wezterm 99.99.99-fakerelease" in report.smoke_test


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="POSIX shell scripts in fake tarball")
def test_install_bundle_short_circuits_when_up_to_date(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()
    import hashlib
    real_sha = hashlib.sha256(bundle_bytes).hexdigest()
    sidecar_text = f"{real_sha}  asset.tar.gz\n"
    rel = _release("wezterm-bundle-v1.0")

    monkeypatch.setattr(installer, "_resolve_release", lambda v: rel)

    def fake_http_open(url, octet_stream=False):
        if url.endswith(".sha256"):
            return _FakeResp(sidecar_text.encode("utf-8"))
        return _FakeResp(bundle_bytes)

    monkeypatch.setattr(installer, "_http_open", fake_http_open)

    # First install: downloads.
    r1 = installer.install_bundle()
    assert r1.skipped_download is False

    # Second install: short-circuits (no download flag, marker matches).
    r2 = installer.install_bundle()
    assert r2.skipped_download is True
    assert r2.bytes_downloaded == 0


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="POSIX shell scripts in fake tarball")
def test_install_bundle_force_redownloads(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()
    import hashlib
    real_sha = hashlib.sha256(bundle_bytes).hexdigest()
    sidecar = f"{real_sha}  asset.tar.gz\n"
    rel = _release("wezterm-bundle-v1.0")

    monkeypatch.setattr(installer, "_resolve_release", lambda v: rel)

    def fake_http_open(url, octet_stream=False):
        if url.endswith(".sha256"):
            return _FakeResp(sidecar.encode("utf-8"))
        return _FakeResp(bundle_bytes)

    monkeypatch.setattr(installer, "_http_open", fake_http_open)

    installer.install_bundle()
    r = installer.install_bundle(force=True)
    assert r.skipped_download is False


def test_install_bundle_rejects_sha_mismatch(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()
    bad_sha = "0" * 64  # definitely not the real digest
    sidecar = f"{bad_sha}  asset.tar.gz\n"
    rel = _release("wezterm-bundle-v1.0")

    monkeypatch.setattr(installer, "_resolve_release", lambda v: rel)

    def fake_http_open(url, octet_stream=False):
        if url.endswith(".sha256"):
            return _FakeResp(sidecar.encode("utf-8"))
        return _FakeResp(bundle_bytes)

    monkeypatch.setattr(installer, "_http_open", fake_http_open)

    with pytest.raises(installer.BundleInstallError, match="sha256 mismatch"):
        installer.install_bundle(verify_smoke_test=False)
    # Bundle did NOT get installed.
    assert not wb.is_installed()


def test_install_bundle_refuses_unverifiable_release(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()

    # Release with NO sidecar URL.
    rel = ReleaseInfo(
        tag="wezterm-bundle-v1.0",
        version=(1, 0),
        published_at="",
        asset_url="https://example.invalid/asset.tar.gz",
        asset_api_url="",
        asset_size=0,
        sha256_url=None,
        sha256_api_url=None,
        body="",
    )

    monkeypatch.setattr(installer, "_resolve_release", lambda v: rel)
    monkeypatch.setattr(installer, "_http_open",
                        lambda url, octet_stream=False: _FakeResp(bundle_bytes))

    with pytest.raises(installer.BundleInstallError, match="no .sha256 sidecar"):
        installer.install_bundle(verify_smoke_test=False)


# ---------------------------------------------------------------------------
# CLI integration — `daimon install` and `daimon doctor`
# ---------------------------------------------------------------------------


def test_cli_install_exits_2_on_installer_error(fake_root, monkeypatch):
    def boom(**kw):
        raise installer.BundleInstallError("simulated network failure")
    monkeypatch.setattr("daimon.install.install_bundle", boom)

    runner = CliRunner()
    result = runner.invoke(main, ["install"])
    assert result.exit_code == 2
    assert "simulated network failure" in result.output


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="POSIX shell scripts in fake tarball")
def test_cli_install_happy_path(fake_root, monkeypatch):
    bundle_bytes = _make_fake_bundle_bytes()
    import hashlib
    real_sha = hashlib.sha256(bundle_bytes).hexdigest()
    sidecar = f"{real_sha}  asset.tar.gz\n"
    rel = _release("wezterm-bundle-v1.0")

    monkeypatch.setattr(installer, "_resolve_release", lambda v: rel)

    def fake_http_open(url, octet_stream=False):
        if url.endswith(".sha256"):
            return _FakeResp(sidecar.encode("utf-8"))
        return _FakeResp(bundle_bytes)

    monkeypatch.setattr(installer, "_http_open", fake_http_open)

    runner = CliRunner()
    result = runner.invoke(main, ["install"])
    assert result.exit_code == 0, result.output
    assert "wezterm-bundle-v1.0" in result.output
    assert wb.is_installed()


def test_cli_doctor_reports_uninstalled(fake_root, monkeypatch):
    monkeypatch.delenv("DAIMON_PIN_ART", raising=False)
    monkeypatch.delenv("DAIMON_PIN_BUNDLE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "installed:  no" in result.output
    assert "daimon install" in result.output  # hint shown


def test_cli_doctor_json(fake_root):
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    import json as _json
    payload = _json.loads(result.output)
    assert "bundle" in payload
    assert "art" in payload
    assert "identity" in payload
    assert payload["bundle"]["is_installed"] is False


def test_cli_install_in_art_pure_commands():
    """``daimon install`` MUST be in ART_PURE_COMMANDS so the auto-update
    hook (which itself needs the art pack) doesn't trigger before install."""
    from daimon.cli import ART_PURE_COMMANDS
    assert "install" in ART_PURE_COMMANDS
    assert "doctor" in ART_PURE_COMMANDS
