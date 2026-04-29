"""Silent first-run bootstrap — invoked at the top of every CLI command.

Per refactor.md §6: idempotent, silent on success, single error message on
failure. Subroutines own their own idempotency check; we deliberately do
NOT use a version-marker fast-path because the marker would live at
``DAIMON_HOME`` while the identity lives at ``CONFIG_DIR`` (a separately
resolvable env), so a marker can be stale relative to the underlying
state in ways that produce real bugs (XDG_CONFIG_HOME-only test
harnesses observed this directly). The subroutine checks are cheap (one
stat per call); the saved disk write isn't worth the desync hazard.

Bootstrap is **never fatal** for headless ops — every subroutine swallows
its own failures so ``daimon pull --json`` keeps working even if the
MCP wiring or art-pack fetch couldn't complete.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path


logger = logging.getLogger(__name__)


def daimon_home() -> Path:
    """Resolve the canonical ``~/.daimon`` (override via ``DAIMON_HOME``)."""
    override = os.environ.get("DAIMON_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".daimon"


SUBDIRS = ("run", "cache", "art", "loadouts", "log")


def _ensure_directories() -> None:
    """Create the ~/.daimon tree if missing. Idempotent."""
    home = daimon_home()
    home.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (home / sub).mkdir(exist_ok=True)


def _ensure_identity() -> None:
    """Create an identity + recovery file if neither exists yet.

    The mnemonic is normally returned exactly once and never persisted —
    that's the whole point of a recovery phrase. The bootstrap path
    *does* persist it (mode 0600) at ``CONFIG_DIR/recovery.txt`` because
    a silent first-run gives the user no chance to write it down. They
    can ``rm`` the recovery file once they've copied the words elsewhere.
    """
    from daimon.identity import generate_identity
    from daimon.identity.keys import CONFIG_DIR, PRIVATE_KEY_PATH

    if PRIVATE_KEY_PATH.is_file():
        return

    try:
        identity = generate_identity()
    except Exception:  # noqa: BLE001 — never fatal at bootstrap layer
        logger.exception("identity bootstrap failed (non-fatal)")
        return

    if identity.mnemonic:
        recovery = CONFIG_DIR / "recovery.txt"
        try:
            recovery.write_text(
                identity.mnemonic + "\n",
                encoding="utf-8",
            )
            os.chmod(recovery, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            logger.exception("recovery file write failed (non-fatal)")


def _ensure_mining_hook() -> None:
    """Wire the Claude Code PostToolUse mining hook. Non-fatal on failure."""
    try:
        from daimon.mining import installer as hook_installer
        # Look the path up off the module so monkeypatched test paths
        # take effect — the function default-arg captures at definition
        # time, which is too early for tests.
        hook_installer.install_hook(
            settings_path=hook_installer.DEFAULT_SETTINGS_PATH,
        )
    except Exception:  # noqa: BLE001 — never fatal at bootstrap layer
        logger.exception("mining hook install failed (non-fatal)")


def _ensure_mcp_server() -> None:
    """Register the daimon MCP server in Claude Code settings. Non-fatal.

    Existing user-customised entries (``_owner != "daimon"``) are left alone.
    """
    try:
        from daimon.mcp import installer as mcp_installer
        mcp_installer.install_mcp_server(
            settings_path=mcp_installer.DEFAULT_SETTINGS_PATH,
        )
    except Exception:  # noqa: BLE001
        logger.exception("MCP server registration failed (non-fatal)")


def ensure_bootstrapped() -> None:
    """Idempotent setup. Silent on success.

    Note: card-art-pack fetching is **not** done here — that's the CLI
    group callback's job (in ``daimon/cli.py``), which gates it on
    ``ART_PURE_COMMANDS`` so pure data commands (``daimon npcs``,
    ``daimon collection list``, etc.) don't pay the network cost. The
    daemon entry point (``daimon/daemon/entry.py::run``) calls
    ``ensure_art_available`` directly because both ``menu`` and
    ``_daemon_internal`` live in ART_PURE_COMMANDS.
    """
    _ensure_directories()
    _ensure_identity()
    _ensure_mining_hook()
    _ensure_mcp_server()
