"""Tests for the bundled-WezTerm path layout, status, and launcher.

We can't actually run a GUI WezTerm in CI, so the launcher tests use the
``build_launch_argv`` helper to verify the argv we WOULD spawn carries the
three guarantees (absolute path + --config-file + --always-new-process).
"""

from __future__ import annotations

import os
import platform
import stat
from pathlib import Path

import pytest

from daimon.render import wezterm_bundle as wb


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Redirect ~/.daimon to tmp_path via DAIMON_ART_DIR.

    All bundle paths flow through ``runtime_root() == art_root()`` so this
    one env var fully isolates the test from a real install on the host.
    """
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Path layout
# ---------------------------------------------------------------------------


def test_runtime_root_follows_art_root_env(fake_root):
    assert wb.runtime_root() == fake_root


def test_bin_dir_under_runtime_root(fake_root):
    assert wb.bin_dir() == fake_root / "bin"


def test_etc_dir_under_runtime_root(fake_root):
    assert wb.etc_dir() == fake_root / "etc"


def test_wezterm_bin_extension_per_os(fake_root):
    p = wb.wezterm_bin()
    if platform.system() == "Windows":
        assert p.name == "wezterm.exe"
    else:
        assert p.name == "wezterm"


def test_wezterm_config_path(fake_root):
    assert wb.wezterm_config_path() == fake_root / "etc" / "wezterm.lua"


def test_version_marker_path(fake_root):
    assert wb.version_marker_path() == fake_root / "bin" / ".wezterm-version"


# ---------------------------------------------------------------------------
# Status checks
# ---------------------------------------------------------------------------


def test_is_installed_false_when_no_binary(fake_root):
    assert wb.is_installed() is False


def test_is_installed_true_when_binary_present(fake_root):
    bin_path = wb.wezterm_bin()
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\necho fake")
    bin_path.chmod(bin_path.stat().st_mode | 0o111)
    assert wb.is_installed() is True


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="POSIX-only execute-bit check")
def test_is_installed_false_when_not_executable(fake_root):
    bin_path = wb.wezterm_bin()
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\necho fake")
    bin_path.chmod(0o644)  # NOT executable
    assert wb.is_installed() is False


def test_installed_version_returns_marker(fake_root):
    bin_path = wb.wezterm_bin()
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\necho fake")
    if platform.system() != "Windows":
        bin_path.chmod(0o755)
    wb.version_marker_path().write_text("wezterm-bundle-v1.2.3\n")
    assert wb.installed_version() == "wezterm-bundle-v1.2.3"


def test_installed_version_none_when_no_install(fake_root):
    assert wb.installed_version() is None


# ---------------------------------------------------------------------------
# Locked config writer
# ---------------------------------------------------------------------------


def test_locked_config_text_loads_from_package():
    text = wb.locked_config_text()
    assert "DAIMON locked WezTerm config" in text
    assert "config.font_size" in text
    assert "format-window-title" in text


def test_write_locked_config_creates_file(fake_root):
    p = wb.write_locked_config()
    assert p.is_file()
    assert p == fake_root / "etc" / "wezterm.lua"
    assert "DAIMON locked WezTerm config" in p.read_text()


def test_write_locked_config_overwrites_user_edits(fake_root):
    """User edits MUST be discarded — every player's render is identical."""
    p = wb.write_locked_config()
    p.write_text("-- I edited this!\n")
    wb.write_locked_config()
    text = p.read_text()
    assert "I edited this" not in text
    assert "DAIMON locked WezTerm config" in text


def test_write_locked_config_to_custom_dest(fake_root, tmp_path):
    dest = tmp_path / "custom" / "weird.lua"
    p = wb.write_locked_config(dest=dest)
    assert p == dest
    assert dest.is_file()


# ---------------------------------------------------------------------------
# Launcher (argv-only — we can't spawn WezTerm in CI)
# ---------------------------------------------------------------------------


def test_launch_raises_when_not_installed(fake_root):
    with pytest.raises(wb.WezTermNotInstalledError):
        wb.launch(["daimon", "shop"])


def test_build_launch_argv_three_guarantees(fake_root):
    argv = wb.build_launch_argv(["daimon", "shop"])
    # Guarantee 1: absolute path to OUR binary.
    assert argv[0] == str(wb.wezterm_bin())
    assert os.path.isabs(argv[0])
    # Guarantee 2: --config-file points at our locked config.
    assert "--config-file" in argv
    cfg_idx = argv.index("--config-file")
    assert argv[cfg_idx + 1] == str(wb.wezterm_config_path())
    # Guarantee 3: --always-new-process so we don't attach to a stale mux.
    assert "--always-new-process" in argv
    # The user command appears after `--`.
    sep = argv.index("--")
    assert argv[sep + 1:] == ["daimon", "shop"]


def test_build_launch_argv_with_cwd(fake_root, tmp_path):
    argv = wb.build_launch_argv(["daimon", "play"], cwd=tmp_path)
    assert "--cwd" in argv
    cwd_idx = argv.index("--cwd")
    assert argv[cwd_idx + 1] == str(tmp_path)


# ---------------------------------------------------------------------------
# Tarball install (uses install_from_tarball helper)
# ---------------------------------------------------------------------------


def _make_fake_bundle_tarball(dest: Path) -> Path:
    """Create a minimal tarball with a fake wezterm + wezterm-gui binary."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in ("wezterm", "wezterm-gui"):
            content = f"#!/bin/sh\necho {name}-fake\n".encode("utf-8")
            ti = tarfile.TarInfo(name=name)
            ti.size = len(content)
            ti.mode = 0o644  # extracted then chmod +x by install_from_tarball
            tf.addfile(ti, io.BytesIO(content))
    dest.write_bytes(buf.getvalue())
    return dest


def test_install_from_tarball_extracts_and_chmods(fake_root, tmp_path):
    tar = _make_fake_bundle_tarball(tmp_path / "bundle.tar.gz")
    out = wb.install_from_tarball(tar, version="wezterm-bundle-v9.9.9")
    assert out == wb.bin_dir()
    assert (out / "wezterm").is_file()
    assert (out / "wezterm-gui").is_file()
    if platform.system() != "Windows":
        # chmod +x applied to wezterm* files.
        mode = (out / "wezterm").stat().st_mode
        assert mode & stat.S_IXUSR
    assert wb.version_marker_path().read_text().strip() == "wezterm-bundle-v9.9.9"
    # Locked config also written.
    assert wb.wezterm_config_path().is_file()


def test_install_from_tarball_rejects_path_traversal(fake_root, tmp_path):
    import io
    import tarfile

    tar = tmp_path / "bad.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo(name="../etc/passwd")
        ti.size = 0
        tf.addfile(ti, io.BytesIO(b""))
    tar.write_bytes(buf.getvalue())

    with pytest.raises(ValueError, match="unsafe tarball member"):
        wb.install_from_tarball(tar)


def test_install_from_tarball_rejects_absolute_paths(fake_root, tmp_path):
    import io
    import tarfile

    tar = tmp_path / "bad.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo(name="/etc/passwd")
        ti.size = 0
        tf.addfile(ti, io.BytesIO(b""))
    tar.write_bytes(buf.getvalue())

    with pytest.raises(ValueError, match="unsafe tarball member"):
        wb.install_from_tarball(tar)


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


def test_status_summary_when_uninstalled(fake_root):
    s = wb.status_summary()
    assert s["is_installed"] is False
    assert s["installed_version"] is None
    assert s["config_present"] is False
    # Paths surface the resolved root.
    assert str(fake_root) in s["wezterm_bin"]
    assert str(fake_root) in s["wezterm_config"]


def test_status_summary_when_installed(fake_root, tmp_path):
    tar = _make_fake_bundle_tarball(tmp_path / "bundle.tar.gz")
    wb.install_from_tarball(tar, version="wezterm-bundle-v1.0")
    s = wb.status_summary()
    assert s["is_installed"] is True
    assert s["installed_version"] == "wezterm-bundle-v1.0"
    assert s["config_present"] is True


def test_remove_bundle_clears_bin_and_etc(fake_root, tmp_path):
    tar = _make_fake_bundle_tarball(tmp_path / "bundle.tar.gz")
    wb.install_from_tarball(tar, version="wezterm-bundle-v1.0")
    assert wb.is_installed()
    removed = wb.remove_bundle()
    assert wb.bin_dir() in removed
    assert wb.etc_dir() in removed
    assert wb.is_installed() is False


# ---------------------------------------------------------------------------
# Auto-relaunch decision logic
# ---------------------------------------------------------------------------


class _FakeStdout:
    """Stand-in for sys.stdout in TTY-detection tests."""
    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _install_fake_bundle(fake_root: Path, tmp_path: Path) -> None:
    tar = _make_fake_bundle_tarball(tmp_path / "bundle.tar.gz")
    wb.install_from_tarball(tar, version="wezterm-bundle-vTEST")


def test_should_relaunch_skips_when_already_inside(fake_root, tmp_path,
                                                   monkeypatch):
    _install_fake_bundle(fake_root, tmp_path)
    monkeypatch.setenv(wb.INSIDE_TERMINAL_ENV, "1")
    monkeypatch.setattr("sys.stdout", _FakeStdout(True))
    monkeypatch.setenv("DISPLAY", ":0")  # Linux guard
    ok, reason = wb.should_relaunch_in_bundled_terminal()
    assert ok is False
    assert reason is None  # silent — already inside is not a hint-worthy case


def test_should_relaunch_skips_when_not_a_tty(fake_root, tmp_path,
                                              monkeypatch):
    _install_fake_bundle(fake_root, tmp_path)
    monkeypatch.delenv(wb.INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setattr("sys.stdout", _FakeStdout(False))
    monkeypatch.setenv("DISPLAY", ":0")
    ok, reason = wb.should_relaunch_in_bundled_terminal()
    assert ok is False
    assert reason is None  # silent — piped output is not a hint-worthy case


def test_should_relaunch_returns_hint_when_not_installed(fake_root,
                                                        monkeypatch):
    # No install fixture — bundle absent.
    monkeypatch.delenv(wb.INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setattr("sys.stdout", _FakeStdout(True))
    monkeypatch.setenv("DISPLAY", ":0")
    ok, reason = wb.should_relaunch_in_bundled_terminal()
    assert ok is False
    assert reason is not None and "daimon install" in reason


@pytest.mark.skipif(platform.system() != "Linux",
                    reason="DISPLAY guard is Linux-only")
def test_should_relaunch_skips_when_no_display_on_linux(fake_root, tmp_path,
                                                       monkeypatch):
    _install_fake_bundle(fake_root, tmp_path)
    monkeypatch.delenv(wb.INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setattr("sys.stdout", _FakeStdout(True))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    ok, reason = wb.should_relaunch_in_bundled_terminal()
    assert ok is False
    assert reason is not None and "graphical" in reason.lower()


@pytest.mark.skipif(platform.system() != "Linux",
                    reason="DISPLAY guard is Linux-only")
def test_should_relaunch_returns_true_when_all_conditions_met(fake_root,
                                                              tmp_path,
                                                              monkeypatch):
    _install_fake_bundle(fake_root, tmp_path)
    monkeypatch.delenv(wb.INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setattr("sys.stdout", _FakeStdout(True))
    monkeypatch.setenv("DISPLAY", ":0")
    ok, reason = wb.should_relaunch_in_bundled_terminal()
    assert ok is True
    assert reason is None


def test_should_relaunch_require_tty_false_skips_tty_check(fake_root, tmp_path,
                                                          monkeypatch):
    """Callers that don't care about TTY (e.g. tests) can disable the guard."""
    _install_fake_bundle(fake_root, tmp_path)
    monkeypatch.delenv(wb.INSIDE_TERMINAL_ENV, raising=False)
    monkeypatch.setattr("sys.stdout", _FakeStdout(False))  # not a TTY
    monkeypatch.setenv("DISPLAY", ":0")  # Linux guard satisfied
    ok, reason = wb.should_relaunch_in_bundled_terminal(require_tty=False)
    if platform.system() == "Linux":
        assert ok is True
        assert reason is None


def test_relaunch_in_bundled_terminal_raises_when_not_installed(fake_root):
    with pytest.raises(wb.WezTermNotInstalledError):
        wb.relaunch_in_bundled_terminal(["daimon", "shop"])


def test_relaunch_in_bundled_terminal_calls_execvpe_with_inside_env(
        fake_root, tmp_path, monkeypatch):
    """Verify the env passed to execvpe sets DAIMON_INSIDE_TERMINAL=1."""
    _install_fake_bundle(fake_root, tmp_path)
    captured: dict = {}

    def _fake_execvpe(file, args, env):
        captured["file"] = file
        captured["args"] = args
        captured["env"] = env
        # Must raise — execvpe normally never returns; raising surfaces any
        # tests that forget to short-circuit the caller's flow.
        raise SystemExit(0)

    monkeypatch.setattr("os.execvpe", _fake_execvpe)
    with pytest.raises(SystemExit):
        wb.relaunch_in_bundled_terminal(["daimon", "shop"])

    assert captured["file"] == str(wb.wezterm_bin())
    assert captured["env"][wb.INSIDE_TERMINAL_ENV] == "1"
    # The inner command appears after `--`.
    assert "--" in captured["args"]
    sep = captured["args"].index("--")
    assert captured["args"][sep + 1:] == ["daimon", "shop"]
    # Locked config was rewritten.
    assert wb.wezterm_config_path().is_file()
