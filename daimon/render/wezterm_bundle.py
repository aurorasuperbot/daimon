"""Bundled-WezTerm path layout, status checks, and launcher.

DAIMON ships its own WezTerm binary so card art renders pixel-perfect at
known DPI / cell size / colour space. The render-surface decision was
locked 2026-04-21 (see ``docs/canon_audit.md`` § "RENDER SURFACE — BUNDLED
WEZTERM"). This module owns:

  * Path resolution for the bundled binary + locked Lua config.
  * Status checks (is it installed, what version is it).
  * The ``write_locked_config`` writer that copies our packaged
    ``wezterm.lua`` into ``~/.daimon/etc/wezterm.lua`` on every install /
    launch (so a daimon-engine upgrade can refresh the config in lockstep
    with the engine's expectations).
  * The ``launch`` launcher that spawns wezterm with the locked config,
    using three guarantees to ensure it's OUR terminal every time:

      1. **Absolute path** — never PATH lookup, so a user-installed
         wezterm doesn't get picked up.
      2. ``--config-file`` — overrides any user-side ``~/.wezterm.lua``.
      3. ``--always-new-process`` — fresh process, doesn't attach to a
         stale wezterm-mux instance.

The actual binary tarball is downloaded by ``daimon/install/installer.py``
(the ``daimon install`` CLI command). This module is path/launch-only and
makes no network calls.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import List, Optional

from daimon.update.paths import art_root


# ---------------------------------------------------------------------------
# Path layout
#
# We share the runtime root (``~/.daimon``) with the art-pack — same root,
# different subdirs:
#
#     ~/.daimon/
#       art/v1_alpha/...        # NovelAI card PNGs (managed by daimon update)
#       bin/wezterm{,.exe}      # bundled WezTerm binary (this module)
#       etc/wezterm.lua         # locked Lua config (this module)
#       cache/...               # download scratch (shared with art pack)
#       inbox/...               # match/event inbox (managed by daimon play)
#       pvp_state/...           # PvP records (managed by daimon arena)
#
# Path precedence is identical to ``daimon.update.paths.art_root()``:
# DAIMON_ART_DIR > XDG_DATA_HOME/daimon > ~/.daimon. We piggyback on the
# same resolver so test fixtures + sandboxes can override one root and
# get a coherent tree.
# ---------------------------------------------------------------------------


def runtime_root() -> Path:
    """Returns ``~/.daimon`` (or the override-resolved equivalent).

    Identical resolution to ``daimon.update.paths.art_root`` so any
    DAIMON_ART_DIR / XDG_DATA_HOME override moves both art + binaries
    together.
    """
    return art_root()


def bin_dir() -> Path:
    """``~/.daimon/bin`` — where the bundled WezTerm binary lives."""
    return runtime_root() / "bin"


def etc_dir() -> Path:
    """``~/.daimon/etc`` — where the locked Lua config lives."""
    return runtime_root() / "etc"


def wezterm_bin() -> Path:
    """Absolute path to the bundled WezTerm binary.

    Adds ``.exe`` on Windows. The file may not exist yet — call
    :func:`is_installed` to check.
    """
    suffix = ".exe" if platform.system() == "Windows" else ""
    return bin_dir() / f"wezterm{suffix}"


def wezterm_gui_bin() -> Path:
    """Absolute path to ``wezterm-gui`` (the GUI launcher binary).

    Some WezTerm distributions split the CLI (``wezterm``) and GUI process
    (``wezterm-gui``) into two binaries. Both ship in the bundle. We launch
    via ``wezterm start --`` which dispatches to the GUI binary; this
    helper is exposed mostly for diagnostics.
    """
    suffix = ".exe" if platform.system() == "Windows" else ""
    return bin_dir() / f"wezterm-gui{suffix}"


def wezterm_config_path() -> Path:
    """``~/.daimon/etc/wezterm.lua`` — the locked Lua config path."""
    return etc_dir() / "wezterm.lua"


def version_marker_path() -> Path:
    """``~/.daimon/bin/.wezterm-version`` — installed bundle version marker.

    Written by the installer with the bundle version (e.g. ``"20240203-110809"``)
    so we can compare against latest without execing the binary.
    """
    return bin_dir() / ".wezterm-version"


# ---------------------------------------------------------------------------
# Status checks
# ---------------------------------------------------------------------------


def is_installed() -> bool:
    """``True`` iff the bundled WezTerm binary exists and is executable."""
    p = wezterm_bin()
    if not p.is_file():
        return False
    if platform.system() != "Windows" and not os.access(p, os.X_OK):
        return False
    return True


def installed_version() -> Optional[str]:
    """Read the bundle version marker, falling back to ``wezterm --version``.

    Returns the bundle version string (e.g. ``"20240203-110809"``) or
    ``None`` if no bundle is installed.
    """
    marker = version_marker_path()
    if marker.is_file():
        try:
            return marker.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
    if not is_installed():
        return None
    try:
        out = subprocess.run(
            [str(wezterm_bin()), "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            # WezTerm prints "wezterm 20240203-110809-..." → take the version token.
            parts = out.stdout.strip().split()
            if len(parts) >= 2:
                return parts[1]
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Locked config writer
# ---------------------------------------------------------------------------


_LOCKED_LUA_RESOURCE = ("daimon.render", "wezterm.lua")


def locked_config_text() -> str:
    """Returns the Lua config that ships in the wheel.

    Loaded via importlib.resources so the test suite + the installer both
    see the same canonical bytes.
    """
    pkg, name = _LOCKED_LUA_RESOURCE
    return resources.files(pkg).joinpath(name).read_text(encoding="utf-8")


def write_locked_config(*, dest: Optional[Path] = None) -> Path:
    """Write the locked ``wezterm.lua`` to disk; returns the path written.

    Creates ``~/.daimon/etc/`` if needed. Idempotent — safe to call on
    every launch so the on-disk config tracks daimon-engine upgrades.
    Always overwrites; user edits to ``~/.daimon/etc/wezterm.lua`` will
    be lost (that's the point — every player's render surface is identical).
    """
    target = dest or wezterm_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(locked_config_text(), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class WezTermNotInstalledError(RuntimeError):
    """Raised when ``launch()`` is called without a bundled WezTerm."""


def build_launch_argv(command: List[str], *,
                      cwd: Optional[Path] = None) -> List[str]:
    """Return the argv list that ``launch()`` would exec — without spawning.

    Useful for tests, dry-runs, or callers that want to manage the
    subprocess themselves. Does NOT verify the binary exists; that's
    :func:`launch`'s job.
    """
    args: List[str] = [
        str(wezterm_bin()),
        "--config-file", str(wezterm_config_path()),
        # Don't merge into the user's existing wezterm-mux session.
        "start",
        "--always-new-process",
    ]
    if cwd is not None:
        args.extend(["--cwd", str(cwd)])
    args.append("--")
    args.extend(command)
    return args


def launch(command: List[str], *,
           cwd: Optional[Path] = None,
           env: Optional[dict] = None) -> subprocess.Popen:
    """Spawn the bundled WezTerm running ``command`` and return the Popen.

    ``command`` is the argv list executed inside the new WezTerm window
    (e.g. ``["daimon", "shop"]``). ``cwd`` and ``env`` are forwarded to
    Popen. The locked config is rewritten on every launch so on-disk state
    stays in sync with the installed daimon-engine.

    Raises :class:`WezTermNotInstalledError` if the bundle isn't installed
    yet — caller should run ``daimon install`` first.
    """
    if not is_installed():
        raise WezTermNotInstalledError(
            f"daimon's bundled WezTerm not installed at {wezterm_bin()}; "
            "run `daimon install` to bootstrap.")
    write_locked_config()
    argv = build_launch_argv(command, cwd=cwd)
    return subprocess.Popen(argv, env=env)


# ---------------------------------------------------------------------------
# Auto-relaunch — interactive TUI commands re-exec into our bundled WezTerm
# so users always get the locked render surface, regardless of which
# terminal they typed `daimon shop` from.
# ---------------------------------------------------------------------------

#: Env-var sentinel set when we relaunch into our terminal. Prevents an
#: infinite re-exec loop and lets nested commands skip the relaunch.
INSIDE_TERMINAL_ENV = "DAIMON_INSIDE_TERMINAL"


def should_relaunch_in_bundled_terminal(*,
                                        require_tty: bool = True
                                        ) -> tuple[bool, Optional[str]]:
    """Decide whether to re-exec the current process into our bundled WezTerm.

    Returns ``(True, None)`` when the relaunch should happen.
    Returns ``(False, reason)`` otherwise — ``reason`` is a short
    human-readable string the caller can echo as a hint, or ``None`` when
    the situation isn't worth surfacing (already inside, piped output).

    Conditions checked, in order:
      1. ``DAIMON_INSIDE_TERMINAL=1`` already set → no relaunch (silent).
      2. ``require_tty`` AND stdout is not a TTY → no relaunch (silent).
         This is the agent / pipe path: caller wants text out, not a window.
      3. Bundle not installed → no relaunch, returns a hint reason.
      4. Linux without ``$DISPLAY`` and ``$WAYLAND_DISPLAY`` → no relaunch,
         no graphical session to spawn into.
      5. Otherwise → relaunch.

    macOS and Windows have an implicit display, so guard #4 is Linux-only.
    """
    import sys

    if os.environ.get(INSIDE_TERMINAL_ENV) == "1":
        return False, None
    if require_tty and not sys.stdout.isatty():
        return False, None
    if not is_installed():
        return False, "DAIMON terminal not installed (run `daimon install`)"
    if platform.system() == "Linux":
        if not (os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY")):
            return False, "no graphical display detected"
    return True, None


def relaunch_in_bundled_terminal(command: List[str], *,
                                 cwd: Optional[Path] = None) -> None:
    """Replace the current process with our WezTerm running ``command``.

    Uses ``os.execvpe`` so the parent shell's exit code reflects the WezTerm
    window's exit. Sets ``DAIMON_INSIDE_TERMINAL=1`` in the child env so the
    re-launched ``daimon`` invocation skips this relaunch and proceeds to
    the actual TUI.

    Always rewrites the locked config first (so daimon-engine upgrades
    refresh the on-disk config the new window will load).

    Raises :class:`WezTermNotInstalledError` if the bundle isn't present —
    callers should gate this with :func:`should_relaunch_in_bundled_terminal`
    rather than relying on the exception path for control flow.

    NOTE: this function never returns under normal conditions — execvpe
    replaces the process. It only returns control if execvpe itself fails.
    """
    if not is_installed():
        raise WezTermNotInstalledError(
            f"daimon's bundled WezTerm not installed at {wezterm_bin()}; "
            "run `daimon install` to bootstrap.")
    write_locked_config()
    argv = build_launch_argv(command, cwd=cwd)
    env = dict(os.environ)
    env[INSIDE_TERMINAL_ENV] = "1"
    os.execvpe(argv[0], argv, env)


# ---------------------------------------------------------------------------
# Tarball extraction (called by the installer)
# ---------------------------------------------------------------------------


def install_from_tarball(tarball: Path, *,
                         version: Optional[str] = None) -> Path:
    """Extract a bundle tarball into ``bin_dir()``; returns the bin dir.

    The tarball layout is:
        wezterm
        wezterm-gui
        (optional) wezterm-mux-server, strip-ansi-escapes, etc.

    All members are extracted directly into ``~/.daimon/bin/`` (flat, no
    nested ``wezterm-x.y.z/`` dir). The installer handles SHA verification
    + atomic swap; this helper is the unpack stage only.

    Refuses tarballs containing absolute paths or ``..`` traversal segments.
    """
    import tarfile

    bin_dir().mkdir(parents=True, exist_ok=True)

    with tarfile.open(tarball, "r:*") as tf:
        # Validate every member's path.
        for m in tf.getmembers():
            name = m.name
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"unsafe tarball member: {name!r}")
            if not (m.isfile() or m.isdir() or m.issym()):
                # Refuse devices, FIFOs, hardlinks — bundle should be flat files only.
                raise ValueError(f"unsupported tarball member type: {name!r}")
        # filter="data" rejects unsafe paths/permissions; we already validated
        # paths above but pass it explicitly to silence Python 3.14's default
        # deprecation warning and lock in the safer semantics.
        tf.extractall(path=bin_dir(), filter="data")

    # Mark binaries executable on POSIX.
    if platform.system() != "Windows":
        for p in bin_dir().iterdir():
            if p.name.startswith("wezterm"):
                p.chmod(p.stat().st_mode | 0o111)

    if version is not None:
        version_marker_path().write_text(version + "\n", encoding="utf-8")

    write_locked_config()
    return bin_dir()


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


def status_summary() -> dict:
    """Returns a dict summarising the bundle install state for `daimon doctor`."""
    return {
        "runtime_root": str(runtime_root()),
        "bin_dir": str(bin_dir()),
        "etc_dir": str(etc_dir()),
        "wezterm_bin": str(wezterm_bin()),
        "wezterm_config": str(wezterm_config_path()),
        "is_installed": is_installed(),
        "installed_version": installed_version(),
        "config_present": wezterm_config_path().is_file(),
    }


def remove_bundle() -> List[Path]:
    """Delete the bundled binaries + locked config; return list of paths removed.

    Used by ``daimon install --reinstall`` and tests. Does NOT touch the
    art pack, identity, or inbox.
    """
    removed: List[Path] = []
    for p in (bin_dir(), etc_dir()):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
    return removed
