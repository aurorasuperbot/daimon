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

Two distribution paths share this module:

  * **Source install** (``pip install daimon-engine``). The user runs
    ``daimon install`` post-install, which downloads a WezTerm tarball
    into ``~/.daimon/bin/`` (the legacy bundle location).

  * **Binary distribution** (Nuitka/PyInstaller, shipped via winget /
    Scoop / Brew / AppImage). WezTerm is packed into the binary at build
    time and lives alongside ``sys.executable`` (or under
    ``sys._MEIPASS`` for PyInstaller). No network call required;
    ``daimon install`` is a no-op.

:func:`wezterm_bin` is the layered resolver: embedded location first,
legacy ``~/.daimon/bin/`` second. Other resolvers (``wezterm_gui_bin``,
``installed_version``) follow the same precedence.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import List, Optional

from daimon.update.paths import art_root


# Name of the data directory that the binary build pipeline embeds
# alongside the daimon binary (or under sys._MEIPASS for PyInstaller).
# The build script (release-binaries.yml + scripts/build_nuitka.py)
# stages the WezTerm binaries here; this module resolves them.
_BUNDLED_WEZTERM_DIRNAME = "daimon-bundled-wezterm"


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


def _exe_suffix() -> str:
    return ".exe" if platform.system() == "Windows" else ""


def bundled_wezterm_dir() -> Optional[Path]:
    """Resolve the embedded WezTerm directory packed at build time.

    Returns the absolute path containing ``wezterm{,-gui,...}`` when
    running from a binary distribution (Nuitka standalone / onefile or
    PyInstaller), or ``None`` when running from a source install.

    Detection signals:
      * ``sys.frozen`` set by both Nuitka (``"nuitka"`` or ``True``) and
        PyInstaller (``True``).
      * ``sys._MEIPASS`` set only by PyInstaller — points at the temp
        directory where data files are extracted.
      * Otherwise the data dir lives next to ``sys.executable`` (Nuitka
        standalone), so we resolve relative to that.

    Returns ``None`` if the directory isn't present even in a frozen
    build — the runtime falls back to the legacy ``~/.daimon/bin/``
    path so a user can still bootstrap via ``daimon install``.
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / _BUNDLED_WEZTERM_DIRNAME
        if cand.is_dir():
            return cand
    exe = Path(sys.executable).resolve()
    cand = exe.parent / _BUNDLED_WEZTERM_DIRNAME
    if cand.is_dir():
        return cand
    return None


def _legacy_wezterm_bin() -> Path:
    """``~/.daimon/bin/wezterm`` — populated by ``daimon install`` for source installs."""
    return bin_dir() / f"wezterm{_exe_suffix()}"


def _legacy_wezterm_gui_bin() -> Path:
    return bin_dir() / f"wezterm-gui{_exe_suffix()}"


def wezterm_bin() -> Path:
    """Absolute path to the bundled WezTerm binary.

    Layered lookup:
      1. Embedded directory (binary distribution, packed at build time).
      2. ``~/.daimon/bin/wezterm`` — legacy source-install location
         populated by ``daimon install``.

    Adds ``.exe`` on Windows. The file may not exist at the legacy path
    yet — call :func:`is_installed` to check.
    """
    embedded = bundled_wezterm_dir()
    if embedded is not None:
        cand = embedded / f"wezterm{_exe_suffix()}"
        if cand.is_file():
            return cand
    return _legacy_wezterm_bin()


def wezterm_gui_bin() -> Path:
    """Absolute path to ``wezterm-gui`` (the GUI launcher binary).

    Some WezTerm distributions split the CLI (``wezterm``) and GUI process
    (``wezterm-gui``) into two binaries. Both ship in the bundle. We launch
    via ``wezterm start --`` which dispatches to the GUI binary; this
    helper is exposed mostly for diagnostics. Same layered resolution as
    :func:`wezterm_bin`.
    """
    embedded = bundled_wezterm_dir()
    if embedded is not None:
        cand = embedded / f"wezterm-gui{_exe_suffix()}"
        if cand.is_file():
            return cand
    return _legacy_wezterm_gui_bin()


def wezterm_config_path() -> Path:
    """``~/.daimon/etc/wezterm.lua`` — the locked Lua config path."""
    return etc_dir() / "wezterm.lua"


def version_marker_path() -> Path:
    """Path to the installed bundle's version marker, layered.

    Embedded distributions ship a ``.wezterm-version`` file next to the
    binaries inside the bundled data dir. Source installs land it in
    ``~/.daimon/bin/.wezterm-version`` via the installer.
    """
    embedded = bundled_wezterm_dir()
    if embedded is not None:
        cand = embedded / ".wezterm-version"
        if cand.is_file():
            return cand
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


def terminal_supports_kgp() -> bool:
    """Best-effort detector — are we running inside a KGP-capable terminal?

    The reliable signal is ``DAIMON_INSIDE_TERMINAL=1`` — set by
    :func:`relaunch_in_bundled_terminal` when we re-exec into our own
    WezTerm. That guarantees KGP works because we control the binary.

    Falls back to ``TERM_PROGRAM == 'WezTerm'`` so users who launch
    ``daimon shop --in-place`` from an existing WezTerm session still
    get pixel-perfect art (rather than degrading to half-block).

    Returns False everywhere else, so the half-block fallback wins when
    we're unsure — better to render something less crisp than to drown
    a non-KGP terminal in unrenderable APC escapes.
    """
    if os.environ.get(INSIDE_TERMINAL_ENV) == "1":
        return True
    if os.environ.get("TERM_PROGRAM") == "WezTerm":
        return True
    return False


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
    """Returns a dict summarising the bundle install state for `daimon doctor`.

    ``source`` is "embedded" when the binary distribution shipped a
    pre-packed WezTerm, "legacy" when ``daimon install`` populated
    ``~/.daimon/bin/``, and "missing" when neither is present.
    """
    embedded = bundled_wezterm_dir()
    if embedded is not None and (embedded / f"wezterm{_exe_suffix()}").is_file():
        source = "embedded"
    elif _legacy_wezterm_bin().is_file():
        source = "legacy"
    else:
        source = "missing"
    return {
        "runtime_root": str(runtime_root()),
        "bin_dir": str(bin_dir()),
        "etc_dir": str(etc_dir()),
        "wezterm_bin": str(wezterm_bin()),
        "wezterm_config": str(wezterm_config_path()),
        "embedded_dir": str(embedded) if embedded is not None else None,
        "source": source,
        "is_installed": is_installed(),
        "installed_version": installed_version(),
        "config_present": wezterm_config_path().is_file(),
    }


def remove_bundle() -> List[Path]:
    """Delete the legacy ``~/.daimon/bin/`` + ``etc/`` and return removed paths.

    Used by ``daimon install --reinstall`` and tests. Only ever touches
    the source-install legacy location (``bin_dir`` + ``etc_dir``); the
    embedded location packed into a binary distribution is read-only
    and managed by the package manager that installed the binary
    (winget / Scoop / Brew / AppImage / .deb / .rpm). Does NOT touch
    the art pack, identity, or inbox.
    """
    removed: List[Path] = []
    for p in (bin_dir(), etc_dir()):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
    return removed
