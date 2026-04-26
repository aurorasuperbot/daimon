"""Tests for the layered WezTerm resolver in :mod:`daimon.render.wezterm_bundle`.

Covers ``bundled_wezterm_dir``, the layered ``wezterm_bin`` /
``wezterm_gui_bin`` lookups, ``version_marker_path`` priority, and the
``status_summary`` source classification (embedded vs legacy vs missing).
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

from daimon.render import wezterm_bundle as wb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXE = ".exe" if platform.system() == "Windows" else ""


@pytest.fixture
def runtime_sandbox(monkeypatch, tmp_path: Path) -> Path:
    """Sandbox the legacy ~/.daimon root to a tmp dir."""
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path


def _make_legacy_wezterm(root: Path) -> Path:
    """Pretend ``daimon install`` populated ~/.daimon/bin/."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary = bin_dir / f"wezterm{EXE}"
    binary.write_bytes(b"#!fake-wezterm")
    if EXE == "":
        binary.chmod(binary.stat().st_mode | 0o111)
    (bin_dir / f"wezterm-gui{EXE}").write_bytes(b"#!fake-gui")
    return binary


def _make_embedded_dir(parent: Path) -> Path:
    """Pretend Nuitka packed wezterm into ``parent / daimon-bundled-wezterm/``."""
    d = parent / wb._BUNDLED_WEZTERM_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    (d / f"wezterm{EXE}").write_bytes(b"#!embedded-wezterm")
    (d / f"wezterm-gui{EXE}").write_bytes(b"#!embedded-gui")
    return d


# ---------------------------------------------------------------------------
# bundled_wezterm_dir — frozen detection
# ---------------------------------------------------------------------------

class TestBundledWezTermDir:
    def test_returns_none_for_source_install(self, runtime_sandbox, monkeypatch):
        # sys.frozen is unset on a normal interpreter run.
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert wb.bundled_wezterm_dir() is None

    def test_resolves_pyinstaller_meipass(self, tmp_path, monkeypatch):
        embedded = _make_embedded_dir(tmp_path / "meipass")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(embedded.parent), raising=False)
        assert wb.bundled_wezterm_dir() == embedded

    def test_resolves_nuitka_alongside_executable(self, tmp_path, monkeypatch):
        # Pretend sys.executable is at <tmp>/dist/daimon[.exe]
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"#!fake-daimon")
        embedded = _make_embedded_dir(dist)

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
        assert wb.bundled_wezterm_dir() == embedded

    def test_returns_none_when_frozen_but_dir_missing(self, tmp_path, monkeypatch):
        # Frozen flag set, but no embedded dir on disk → fall back to None.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "daimon"), raising=False)
        assert wb.bundled_wezterm_dir() is None


# ---------------------------------------------------------------------------
# Layered wezterm_bin / wezterm_gui_bin
# ---------------------------------------------------------------------------

class TestLayeredResolution:
    def test_legacy_only(self, runtime_sandbox, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        binary = _make_legacy_wezterm(runtime_sandbox)
        assert wb.wezterm_bin() == binary
        assert wb.wezterm_gui_bin() == runtime_sandbox / "bin" / f"wezterm-gui{EXE}"
        assert wb.is_installed()

    def test_embedded_takes_priority_over_legacy(self, runtime_sandbox, tmp_path, monkeypatch):
        # Both are populated; embedded must win.
        legacy = _make_legacy_wezterm(runtime_sandbox)
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"#!fake-daimon")
        embedded = _make_embedded_dir(dist)

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

        chosen = wb.wezterm_bin()
        assert chosen == embedded / f"wezterm{EXE}"
        assert chosen != legacy
        assert wb.wezterm_gui_bin() == embedded / f"wezterm-gui{EXE}"

    def test_falls_back_to_legacy_when_embedded_dir_present_but_missing_binary(
        self, runtime_sandbox, tmp_path, monkeypatch
    ):
        # Embedded *directory* exists but contains no wezterm binary —
        # treat as missing, fall through to the legacy path.
        legacy = _make_legacy_wezterm(runtime_sandbox)
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"#!fake-daimon")
        empty_embedded = dist / wb._BUNDLED_WEZTERM_DIRNAME
        empty_embedded.mkdir()
        # No wezterm binary inside.

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

        # bundled_wezterm_dir resolves to the empty dir...
        assert wb.bundled_wezterm_dir() == empty_embedded
        # ...but wezterm_bin still falls through to the legacy install.
        assert wb.wezterm_bin() == legacy

    def test_missing_everywhere_returns_legacy_path(self, runtime_sandbox, monkeypatch):
        # No embedded, no legacy → returns the *path* (which doesn't exist).
        monkeypatch.delattr(sys, "frozen", raising=False)
        cand = wb.wezterm_bin()
        assert cand == runtime_sandbox / "bin" / f"wezterm{EXE}"
        assert not cand.exists()
        assert not wb.is_installed()


# ---------------------------------------------------------------------------
# version_marker_path — same precedence
# ---------------------------------------------------------------------------

class TestVersionMarkerLayered:
    def test_embedded_marker_wins(self, runtime_sandbox, tmp_path, monkeypatch):
        # Legacy marker exists, but embedded marker also exists → embedded wins.
        bin_dir = runtime_sandbox / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / ".wezterm-version").write_text("legacy-1.0\n")

        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"")
        embedded = _make_embedded_dir(dist)
        (embedded / ".wezterm-version").write_text("embedded-2.0\n")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

        assert wb.version_marker_path() == embedded / ".wezterm-version"
        assert wb.installed_version() == "embedded-2.0"

    def test_falls_back_to_legacy_when_no_embedded_marker(self, runtime_sandbox, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        bin_dir = runtime_sandbox / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / ".wezterm-version").write_text("legacy-1.0\n")
        assert wb.version_marker_path() == bin_dir / ".wezterm-version"
        assert wb.installed_version() == "legacy-1.0"


# ---------------------------------------------------------------------------
# status_summary — source classification
# ---------------------------------------------------------------------------

class TestStatusSummarySource:
    def test_missing_when_nothing_installed(self, runtime_sandbox, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        s = wb.status_summary()
        assert s["source"] == "missing"
        assert s["embedded_dir"] is None

    def test_legacy_when_only_user_install(self, runtime_sandbox, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        _make_legacy_wezterm(runtime_sandbox)
        s = wb.status_summary()
        assert s["source"] == "legacy"
        assert s["embedded_dir"] is None
        assert s["is_installed"] is True

    def test_embedded_when_binary_distribution(self, runtime_sandbox, tmp_path, monkeypatch):
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"")
        embedded = _make_embedded_dir(dist)

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

        s = wb.status_summary()
        assert s["source"] == "embedded"
        assert s["embedded_dir"] == str(embedded)
        assert s["is_installed"] is True


# ---------------------------------------------------------------------------
# remove_bundle — only touches the legacy paths
# ---------------------------------------------------------------------------

class TestRemoveBundleSafety:
    def test_only_removes_legacy(self, runtime_sandbox, tmp_path, monkeypatch):
        legacy = _make_legacy_wezterm(runtime_sandbox)
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"")
        embedded = _make_embedded_dir(dist)

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

        removed = wb.remove_bundle()

        # Legacy dir is gone; embedded is untouched.
        assert legacy.parent in removed
        assert not (runtime_sandbox / "bin").exists()
        assert (embedded / f"wezterm{EXE}").exists()
