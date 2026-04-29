"""Claude Code MCP server registration.

Patches ``~/.claude/settings.json`` to add a ``mcpServers.daimon`` entry
pointing at the bundled stdio MCP entry point (``dmn-mcp``, declared
in ``pyproject.toml`` ``[project.scripts]``).

Mirrors ``daimon.mining.installer`` — same idempotency contract, same
backup-then-write pattern, never clobbers user-added MCP servers.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SERVER_NAME = "daimon"
DEFAULT_COMMAND = "dmn-mcp"


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


def install_mcp_server(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    command: str = DEFAULT_COMMAND,
    server_name: str = SERVER_NAME,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add a ``mcpServers.<server_name>`` entry to the settings file.

    Idempotent: if an entry with the same command is already present we
    return ``already_present``. If a different command is registered we
    leave it alone and return ``conflict`` — never clobber a user override.
    """
    data = _read_settings(settings_path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("settings.mcpServers is not an object — refusing to clobber")

    existing = servers.get(server_name)
    if isinstance(existing, dict) and existing.get("command") == command:
        return {
            "action": "already_present",
            "settings_path": str(settings_path),
            "backup_path": None,
        }
    if existing is not None and not (isinstance(existing, dict)
                                     and existing.get("_owner") == server_name):
        return {
            "action": "conflict",
            "settings_path": str(settings_path),
            "backup_path": None,
            "existing": existing,
        }

    if dry_run:
        return {
            "action": "would_install",
            "settings_path": str(settings_path),
            "backup_path": None,
        }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(settings_path)

    servers[server_name] = {
        "_owner": server_name,
        "command": command,
        "args": [],
    }
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "action": "installed",
        "settings_path": str(settings_path),
        "backup_path": str(backup) if backup else None,
    }


def uninstall_mcp_server(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    server_name: str = SERVER_NAME,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove the daimon-owned MCP server entry from the settings file.

    Only removes entries we own (``_owner == server_name``) so a
    hand-customized entry is preserved.
    """
    if not settings_path.exists():
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None}
    data = _read_settings(settings_path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None}

    existing = servers[server_name]
    if not (isinstance(existing, dict) and existing.get("_owner") == server_name):
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None,
                "reason": "entry not owned by daimon — left alone"}

    if dry_run:
        return {"action": "would_uninstall", "settings_path": str(settings_path),
                "backup_path": None}

    backup = _backup(settings_path)
    del servers[server_name]
    if not servers:
        # Don't leave an empty mcpServers stub behind.
        del data["mcpServers"]

    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "action": "uninstalled",
        "settings_path": str(settings_path),
        "backup_path": str(backup) if backup else None,
    }


def mcp_status(settings_path: Path = DEFAULT_SETTINGS_PATH,
               server_name: str = SERVER_NAME) -> Dict[str, Any]:
    """Inspect whether the MCP server is registered."""
    if not settings_path.exists():
        return {"installed": False, "settings_path": str(settings_path),
                "reason": "settings file does not exist"}
    try:
        data = _read_settings(settings_path)
    except RuntimeError as e:
        return {"installed": False, "settings_path": str(settings_path),
                "reason": str(e)}
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict) or server_name not in servers:
        return {"installed": False, "settings_path": str(settings_path)}
    entry = servers[server_name]
    return {
        "installed": True,
        "settings_path": str(settings_path),
        "command": entry.get("command") if isinstance(entry, dict) else None,
        "owned": (isinstance(entry, dict) and entry.get("_owner") == server_name),
    }
