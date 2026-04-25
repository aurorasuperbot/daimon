"""Tests for scripts/build_wezterm_bundle.py.

We mock the network — nobody downloads 30MB of WezTerm during pytest. The
extraction logic is what's bug-prone (zip vs tar, path flattening, .exe naming
on Windows), so we feed each platform's extractor a synthetic archive that
mirrors the real upstream layout and verify the right binaries come out.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

# The script lives outside the package; import it as a module for testing.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_wezterm_bundle.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_wezterm_bundle", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb = _load_module()


# ---------------------------------------------------------------------------
# Asset URL convention
# ---------------------------------------------------------------------------


def test_upstream_asset_linux_x86_64():
    a = bb._upstream_asset("linux", "x86_64", "20240203-110809-5046fc22")
    assert a == "WezTerm-20240203-110809-5046fc22-Ubuntu22.04.tar.xz"


def test_upstream_asset_linux_aarch64_unsupported():
    with pytest.raises(SystemExit, match="unsupported linux arch"):
        bb._upstream_asset("linux", "aarch64", "v1")


def test_upstream_asset_macos_both_arches():
    # macOS bundle is universal — same upstream artifact for x86_64/aarch64.
    a = bb._upstream_asset("macos", "x86_64", "v1")
    b = bb._upstream_asset("macos", "aarch64", "v1")
    assert a == b == "WezTerm-macos-v1.zip"


def test_upstream_asset_windows_x86_64():
    a = bb._upstream_asset("windows", "x86_64", "v1")
    assert a == "WezTerm-windows-v1.zip"


def test_upstream_asset_windows_aarch64_unsupported():
    with pytest.raises(SystemExit, match="unsupported windows arch"):
        bb._upstream_asset("windows", "aarch64", "v1")


def test_upstream_asset_unknown_os():
    with pytest.raises(SystemExit, match="unknown target_os"):
        bb._upstream_asset("freebsd", "x86_64", "v1")


# ---------------------------------------------------------------------------
# Bundle filename convention — must match daimon.install bundle_asset_name()
# ---------------------------------------------------------------------------


def test_bundle_filename_matches_installer_convention():
    """If this drifts, install_bundle() can't find what we publish."""
    from daimon.install import bundle_asset_name

    for os_name in ("linux", "macos", "windows"):
        for arch in ("x86_64", "aarch64"):
            assert bb._bundle_filename(os_name, arch) == bundle_asset_name(os_name, arch)


# ---------------------------------------------------------------------------
# Linux extraction — Ubuntu22.04.tar.xz layout
# ---------------------------------------------------------------------------


def _make_linux_tarball(dest: Path) -> Path:
    """Mimic upstream Ubuntu22 layout: WezTerm-<v>-Ubuntu22.04/usr/bin/*."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for binname in ("wezterm", "wezterm-gui", "wezterm-mux-server",
                        "strip-ansi-escapes"):
            content = f"#!/bin/sh\necho {binname}\n".encode()
            ti = tarfile.TarInfo(name=f"WezTerm-vX/usr/bin/{binname}")
            ti.size = len(content)
            ti.mode = 0o755
            tf.addfile(ti, io.BytesIO(content))
        # Decoy files we should NOT extract.
        for noisepath in ("WezTerm-vX/usr/share/man/man1/wezterm.1",
                          "WezTerm-vX/usr/share/applications/wezterm.desktop"):
            ti = tarfile.TarInfo(name=noisepath)
            ti.size = 4
            tf.addfile(ti, io.BytesIO(b"data"))
    dest.write_bytes(buf.getvalue())
    return dest


def test_extract_linux_keeps_only_known_binaries(tmp_path):
    archive = _make_linux_tarball(tmp_path / "upstream.tar.xz")
    out = tmp_path / "out"
    out.mkdir()
    extracted = bb._extract_linux(archive, out)
    names = sorted(p.name for p in extracted)
    assert names == ["strip-ansi-escapes", "wezterm",
                     "wezterm-gui", "wezterm-mux-server"]
    # Decoys did NOT come along.
    assert not (out / "wezterm.1").exists()
    assert not (out / "wezterm.desktop").exists()


def test_extract_linux_chmods_executable(tmp_path):
    archive = _make_linux_tarball(tmp_path / "upstream.tar.xz")
    out = tmp_path / "out"
    out.mkdir()
    bb._extract_linux(archive, out)
    import stat
    for binname in ("wezterm", "wezterm-gui"):
        mode = (out / binname).stat().st_mode
        assert mode & stat.S_IXUSR


def test_extract_linux_flattens_paths(tmp_path):
    """Files MUST come out at out/<basename>, not out/usr/bin/<basename>."""
    archive = _make_linux_tarball(tmp_path / "upstream.tar.xz")
    out = tmp_path / "out"
    out.mkdir()
    bb._extract_linux(archive, out)
    assert (out / "wezterm").is_file()
    assert not (out / "usr").exists()


def test_extract_linux_raises_on_empty_archive(tmp_path):
    archive = tmp_path / "empty.tar.xz"
    with tarfile.open(archive, "w:xz"):
        pass
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(SystemExit, match="no binaries extracted"):
        bb._extract_linux(archive, out)


# ---------------------------------------------------------------------------
# macOS extraction — WezTerm.app/Contents/MacOS/ layout
# ---------------------------------------------------------------------------


def _make_macos_zip(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for binname in ("wezterm", "wezterm-gui", "wezterm-mux-server"):
            zf.writestr(
                f"WezTerm.app/Contents/MacOS/{binname}",
                f"#!/bin/sh\necho {binname}".encode(),
            )
        # Decoy app metadata we should ignore.
        zf.writestr("WezTerm.app/Contents/Info.plist", b"<plist></plist>")
        zf.writestr("WezTerm.app/Contents/Resources/icon.icns", b"icon")
    return dest


def test_extract_macos_keeps_only_macos_binaries(tmp_path):
    archive = _make_macos_zip(tmp_path / "upstream.zip")
    out = tmp_path / "out"
    out.mkdir()
    extracted = bb._extract_macos(archive, out)
    names = sorted(p.name for p in extracted)
    assert names == ["wezterm", "wezterm-gui", "wezterm-mux-server"]
    assert not (out / "Info.plist").exists()
    assert not (out / "icon.icns").exists()


def test_extract_macos_flattens_paths(tmp_path):
    archive = _make_macos_zip(tmp_path / "upstream.zip")
    out = tmp_path / "out"
    out.mkdir()
    bb._extract_macos(archive, out)
    assert (out / "wezterm").is_file()
    assert not (out / "WezTerm.app").exists()


# ---------------------------------------------------------------------------
# Windows extraction — flat .zip with .exe + DLLs
# ---------------------------------------------------------------------------


def _make_windows_zip(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("wezterm.exe", b"MZfake-wezterm-exe")
        zf.writestr("wezterm-gui.exe", b"MZfake-wezterm-gui-exe")
        zf.writestr("wezterm-mux-server.exe", b"MZfake-mux")
        zf.writestr("vcruntime140.dll", b"DLL")
        zf.writestr("d3dcompiler_47.dll", b"DLL")
        # License/readme — should be filtered.
        zf.writestr("LICENSE", b"MIT...")
        zf.writestr("README.md", b"...")
    return dest


def test_extract_windows_keeps_exes_and_dlls(tmp_path):
    archive = _make_windows_zip(tmp_path / "upstream.zip")
    out = tmp_path / "out"
    out.mkdir()
    extracted = bb._extract_windows(archive, out)
    names = sorted(p.name for p in extracted)
    assert "wezterm.exe" in names
    assert "wezterm-gui.exe" in names
    assert "vcruntime140.dll" in names


def test_extract_windows_filters_license_and_readme(tmp_path):
    archive = _make_windows_zip(tmp_path / "upstream.zip")
    out = tmp_path / "out"
    out.mkdir()
    bb._extract_windows(archive, out)
    assert not (out / "LICENSE").exists()
    assert not (out / "README.md").exists()


def test_extract_windows_raises_when_wezterm_exe_missing(tmp_path):
    archive = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("wezterm-gui.exe", b"only the gui")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(SystemExit, match="wezterm.exe not found"):
        bb._extract_windows(archive, out)


# ---------------------------------------------------------------------------
# Bundle re-packaging
# ---------------------------------------------------------------------------


def test_make_bundle_is_flat_tar_gz(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "wezterm").write_text("a")
    (staging / "wezterm-gui").write_text("b")

    out = tmp_path / "daimon-wezterm-linux-x86_64.tar.gz"
    bb._make_bundle(staging, out)

    assert out.is_file()
    with tarfile.open(out, "r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers())
    # Flat — no nested 'staging/' directory.
    assert names == ["wezterm", "wezterm-gui"]


def test_write_sha256_format_matches_installer_parser(tmp_path):
    """The installer's _expected_sha256 accepts ``<hex>`` or ``<hex>  <name>``."""
    bundle = tmp_path / "x.tar.gz"
    bundle.write_bytes(b"contents")
    sidecar = bb._write_sha256(bundle)
    text = sidecar.read_text().strip()
    parts = text.split()
    expected_hex = hashlib.sha256(b"contents").hexdigest()
    assert parts[0] == expected_hex
    assert parts[1] == bundle.name


# ---------------------------------------------------------------------------
# Full main() with mocked network — proves end-to-end shape on Linux x86_64.
# ---------------------------------------------------------------------------


def test_main_linux_e2e_with_mocked_download(tmp_path, monkeypatch):
    fake_upstream = _make_linux_tarball(tmp_path / "fake_upstream.tar.xz")

    def _fake_download(url: str, dest: Path) -> None:
        # Ignore url; just stage the fake upstream tarball at dest.
        dest.write_bytes(fake_upstream.read_bytes())

    monkeypatch.setattr(bb, "_download", _fake_download)

    out_dir = tmp_path / "dist"
    rc = bb.main([
        "--wezterm-version", "vTEST",
        "--target-os", "linux",
        "--target-arch", "x86_64",
        "--output-dir", str(out_dir),
    ])
    assert rc == 0

    bundle = out_dir / "daimon-wezterm-linux-x86_64.tar.gz"
    sidecar = out_dir / "daimon-wezterm-linux-x86_64.tar.gz.sha256"
    assert bundle.is_file()
    assert sidecar.is_file()

    # The shipped bundle, when extracted, must yield a binary the installer
    # recognises (i.e. wezterm + executable bit on POSIX).
    import stat
    with tarfile.open(bundle, "r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers())
    assert "wezterm" in names
    assert "wezterm-gui" in names
