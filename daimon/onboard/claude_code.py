"""Claude Code integration writer — MCP entry + PostToolUse hook in one transaction.

``daimon mine install-hook`` (in :mod:`daimon.mining.installer`) handles the
hook half of Claude Code wiring. Onboarding additionally registers a
``mcpServers.daimon`` entry pointing at the ``dmn-mcp`` stdio server so
agents inside Claude Code can call the 32 ``dm_*`` tools without
shelling out.

This module owns the *combined* write: a single read of the settings
file, in-memory merge of both pieces, single backup, single write. Two
sequential calls to install_hook + a separate MCP-entry function would
back the file up twice and leave a window where the hook is installed
but the MCP entry is not (or vice versa).

Idempotent. ``mcpServers.daimon`` is overwritten if it already points
at a different command (so a daimon-engine upgrade can refresh the
absolute path), but the rest of the file — including any other MCP
servers the user has — is untouched.

Detection of "is Claude Code installed?" is conservative: we look for
the user-level settings file at ``~/.claude/settings.json``. If it
doesn't exist we still proceed (creating it), since Claude Code reads
its config lazily and a user who's about to install the CLI for the
first time still wants ``daimon onboard`` to set things up so a fresh
``claude code`` launch picks up the integration immediately.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from daimon.mining.installer import (
    DEFAULT_HOOK_COMMAND,
    DEFAULT_MATCHER,
    DEFAULT_SETTINGS_PATH,
    HOOK_OWNER,
    _has_daimon_hook,
    _new_hook_entry,
)


DAIMON_MCP_SERVER_NAME = "daimon"
DAIMON_MCP_BINARY_NAME = "dmn-mcp"


# ---------------------------------------------------------------------------
# MCP command resolver
# ---------------------------------------------------------------------------

def resolve_mcp_command() -> str:
    """Pick the absolute path to ``dmn-mcp`` we'll write into settings.json.

    Resolution order:

      1. **Frozen binary** (Nuitka / PyInstaller). The ``dmn-mcp[.exe]``
         binary ships alongside ``daimon[.exe]`` and we resolve it via
         ``sys.executable``. This is the package-manager case (winget /
         Scoop / Brew / AppImage) — no PATH lookup needed because the
         path is stable.
      2. **Source install via ``shutil.which``**. For ``pip install -e .``
         in a venv, ``dmn-mcp`` lives in ``<venv>/Scripts/`` (Windows) or
         ``<venv>/bin/`` (POSIX) and is on PATH while the venv is
         activated. Test fixtures and CI use this path.
      3. **Bare command name**. Last resort — Claude Code does its own
         PATH lookup at launch. Works only if ``dmn-mcp`` is on PATH.

    Always returns an absolute path when possible, since Claude Code
    runs from its own working directory and a bare command would fail
    on a system without ``dmn-mcp`` on the PATH that Claude Code sees.
    """
    suffix = ".exe" if platform.system() == "Windows" else ""
    bin_name = f"{DAIMON_MCP_BINARY_NAME}{suffix}"

    # 1) Frozen binary distribution.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        cand = exe_dir / bin_name
        if cand.is_file():
            return str(cand)

    # 2) Source install — use the same Python prefix that's running us.
    # This is more reliable than shutil.which() because it respects an
    # active venv even if the user's PATH is misconfigured.
    py_prefix = Path(sys.executable).resolve().parent
    for cand in (py_prefix / bin_name, py_prefix.parent / "bin" / bin_name):
        if cand.is_file():
            return str(cand)

    # 3) Last resort — let Claude Code resolve via PATH.
    found = shutil.which(DAIMON_MCP_BINARY_NAME)
    if found:
        return found
    return DAIMON_MCP_BINARY_NAME


# ---------------------------------------------------------------------------
# JSON settings IO — duplicates the small primitives from mining/installer.py
# rather than importing private helpers; keeps both modules independently
# verifiable.
# ---------------------------------------------------------------------------

def _now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}") from e
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} top-level is not an object")
    return data


def _backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + f".bak.{_now_stamp()}")
    shutil.copy2(path, bak)
    return bak


def _write_settings_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Tempfile + rename so a crash mid-write never leaves a half-baked file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


# ---------------------------------------------------------------------------
# MCP entry merge logic
# ---------------------------------------------------------------------------

def _build_mcp_entry(command: str) -> Dict[str, Any]:
    return {"command": command}


def _merge_mcp_entry(
    data: Dict[str, Any],
    *,
    server_name: str,
    command: str,
) -> tuple[bool, str]:
    """Add or refresh ``data["mcpServers"][server_name]``.

    Returns ``(changed, action)`` where action ∈
    ``{"installed", "refreshed", "already_present"}``. ``installed`` is
    a fresh entry; ``refreshed`` overwrote a stale command path (e.g.
    after a daimon-engine upgrade moved the binary); ``already_present``
    means the same command was already there.
    """
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("settings.mcpServers is not an object")

    existing = servers.get(server_name)
    desired = _build_mcp_entry(command)

    if existing == desired:
        return False, "already_present"
    if isinstance(existing, dict) and existing.get("command") == command:
        return False, "already_present"

    servers[server_name] = desired
    if existing is None:
        return True, "installed"
    return True, "refreshed"


def is_daimon_mcp_present(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    server_name: str = DAIMON_MCP_SERVER_NAME,
) -> bool:
    """True iff settings.json has a ``mcpServers[server_name]`` entry."""
    if not settings_path.exists():
        return False
    try:
        data = _read_settings(settings_path)
    except RuntimeError:
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    return server_name in servers


# ---------------------------------------------------------------------------
# Combined installer — single transaction
# ---------------------------------------------------------------------------

def install_claude_code_integration(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    mcp_command: Optional[str] = None,
    hook_command: str = DEFAULT_HOOK_COMMAND,
    matcher: str = DEFAULT_MATCHER,
    server_name: str = DAIMON_MCP_SERVER_NAME,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Install both the PostToolUse hook AND the daimon MCP server entry.

    Single read, single backup, single write. Idempotent — re-running
    refreshes the MCP command path if it has drifted (e.g. across a
    daimon-engine upgrade) but leaves an already-installed hook alone.

    Args:
        settings_path: target settings file. Defaults to
            ``~/.claude/settings.json``. Honored as a path override for
            tests + scripted installs.
        mcp_command: absolute path to ``dmn-mcp``. If ``None``, resolved
            via :func:`resolve_mcp_command` at call time.
        hook_command: command to register in the PostToolUse hook.
            Defaults to ``"daimon mine receipt"`` (the standard mining
            hook).
        matcher: regex matcher for the hook entry. Defaults to ``".*"``.
        server_name: key under ``mcpServers`` to write. Defaults to
            ``"daimon"``.
        dry_run: when True, return the action plan without writing.

    Returns:
        ``{
            "settings_path": str,
            "backup_path": str | None,
            "hook_action": "installed" | "already_present" | "would_install",
            "mcp_action": "installed" | "refreshed" | "already_present" | "would_install",
            "mcp_command": str,    # the command we wrote (or would write)
        }``
    """
    if mcp_command is None:
        mcp_command = resolve_mcp_command()

    data = _read_settings(settings_path)

    # Hook half. Mirrors the merge from mining.installer.install_hook
    # but folded into the same data dict so we write once.
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError("settings.hooks is not an object — refusing to clobber")
    post_tool_use = hooks.setdefault("PostToolUse", [])
    if not isinstance(post_tool_use, list):
        raise RuntimeError("settings.hooks.PostToolUse is not an array")

    if _has_daimon_hook(post_tool_use):
        hook_action = "already_present"
        hook_changed = False
    elif dry_run:
        hook_action = "would_install"
        hook_changed = False
    else:
        post_tool_use.append(_new_hook_entry(hook_command, matcher))
        hook_action = "installed"
        hook_changed = True

    # MCP-entry half.
    if dry_run:
        # Compute the action without mutating.
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and servers.get(server_name) == _build_mcp_entry(mcp_command):
            mcp_action = "already_present"
        else:
            mcp_action = "would_install"
        mcp_changed = False
    else:
        mcp_changed, mcp_action = _merge_mcp_entry(
            data, server_name=server_name, command=mcp_command
        )

    backup_path: Optional[Path] = None
    if not dry_run and (hook_changed or mcp_changed):
        backup_path = _backup(settings_path)
        _write_settings_atomic(settings_path, data)

    return {
        "settings_path": str(settings_path),
        "backup_path": str(backup_path) if backup_path else None,
        "hook_action": hook_action,
        "mcp_action": mcp_action,
        "mcp_command": mcp_command,
    }


def uninstall_claude_code_integration(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    server_name: str = DAIMON_MCP_SERVER_NAME,
    keep_hook: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove the daimon MCP entry + optionally the PostToolUse hook.

    Args:
        keep_hook: when True, only the MCP entry is removed (hook stays
            mining currency). Useful for a "stop using daimon as an agent
            tool but keep earning ¤" downgrade.

    Returns:
        ``{
            "settings_path": str,
            "backup_path": str | None,
            "hook_action": "uninstalled" | "kept" | "not_present" | "would_uninstall",
            "mcp_action": "uninstalled" | "not_present" | "would_uninstall",
        }``
    """
    if not settings_path.exists():
        return {
            "settings_path": str(settings_path),
            "backup_path": None,
            "hook_action": "not_present",
            "mcp_action": "not_present",
        }

    data = _read_settings(settings_path)
    changed = False

    # MCP entry.
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and server_name in servers:
        if dry_run:
            mcp_action = "would_uninstall"
        else:
            del servers[server_name]
            mcp_action = "uninstalled"
            changed = True
    else:
        mcp_action = "not_present"

    # Hook.
    if keep_hook:
        hook_action = "kept"
    else:
        hooks = data.get("hooks")
        post_tool_use = (
            hooks.get("PostToolUse")
            if isinstance(hooks, dict) else None
        )
        if isinstance(post_tool_use, list):
            kept = [
                e for e in post_tool_use
                if not (isinstance(e, dict) and e.get("_owner") == HOOK_OWNER)
            ]
            removed = len(post_tool_use) - len(kept)
            if removed == 0:
                hook_action = "not_present"
            elif dry_run:
                hook_action = "would_uninstall"
            else:
                hooks["PostToolUse"] = kept
                hook_action = "uninstalled"
                changed = True
        else:
            hook_action = "not_present"

    backup_path: Optional[Path] = None
    if changed and not dry_run:
        backup_path = _backup(settings_path)
        _write_settings_atomic(settings_path, data)

    return {
        "settings_path": str(settings_path),
        "backup_path": str(backup_path) if backup_path else None,
        "hook_action": hook_action,
        "mcp_action": mcp_action,
    }
