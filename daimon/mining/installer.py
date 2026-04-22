"""Claude Code hook installer.

Patches `~/.claude/settings.json` (or any custom settings file) to register a
`PostToolUse` hook that pipes the event JSON into `daimon mine receipt`.

Idempotent: rerunning never duplicates the hook. Backs up the settings file
to `<settings>.bak.<timestamp>` before each write.

Hook entry shape (Claude Code accepts):

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": ".*",
            "hooks": [
              {"type": "command", "command": "daimon mine receipt"}
            ]
          }
        ]
      }
    }

We mark our entry with `_owner: "daimon"` so we can find/remove it later
without disturbing user-added hooks.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_OWNER = "daimon"
DEFAULT_HOOK_COMMAND = "daimon mine receipt"
DEFAULT_MATCHER = ".*"


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


def _has_daimon_hook(post_tool_use: list) -> bool:
    for entry in post_tool_use:
        if not isinstance(entry, dict):
            continue
        if entry.get("_owner") == HOOK_OWNER:
            return True
        # Also detect non-tagged duplicates by command match (legacy installs)
        for h in entry.get("hooks", []) or []:
            if (isinstance(h, dict) and h.get("type") == "command"
                    and DEFAULT_HOOK_COMMAND in str(h.get("command", ""))):
                return True
    return False


def _new_hook_entry(command: str, matcher: str) -> Dict[str, Any]:
    return {
        "_owner": HOOK_OWNER,
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command}],
    }


def install_hook(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    command: str = DEFAULT_HOOK_COMMAND,
    matcher: str = DEFAULT_MATCHER,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Install the DAIMON hook into the given settings file.

    Returns:
      {"action": "installed" | "already_present" | "would_install",
       "settings_path": str, "backup_path": str | None}
    """
    data = _read_settings(settings_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError("settings.hooks is not an object — refusing to clobber")
    post_tool_use = hooks.setdefault("PostToolUse", [])
    if not isinstance(post_tool_use, list):
        raise RuntimeError("settings.hooks.PostToolUse is not an array")

    if _has_daimon_hook(post_tool_use):
        return {
            "action": "already_present",
            "settings_path": str(settings_path),
            "backup_path": None,
        }

    if dry_run:
        return {
            "action": "would_install",
            "settings_path": str(settings_path),
            "backup_path": None,
        }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(settings_path)

    post_tool_use.append(_new_hook_entry(command, matcher))
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "action": "installed",
        "settings_path": str(settings_path),
        "backup_path": str(backup) if backup else None,
    }


def uninstall_hook(
    *,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Remove the DAIMON-owned hook from the settings file.

    Returns {"action": "uninstalled" | "not_present" | "would_uninstall", ...}
    """
    if not settings_path.exists():
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None}
    data = _read_settings(settings_path)
    hooks = data.get("hooks") or {}
    post_tool_use = hooks.get("PostToolUse") if isinstance(hooks, dict) else None
    if not isinstance(post_tool_use, list):
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None}

    keep = []
    removed = 0
    for entry in post_tool_use:
        if (isinstance(entry, dict)
                and entry.get("_owner") == HOOK_OWNER):
            removed += 1
            continue
        keep.append(entry)

    if removed == 0:
        return {"action": "not_present", "settings_path": str(settings_path),
                "backup_path": None}

    if dry_run:
        return {"action": "would_uninstall", "settings_path": str(settings_path),
                "backup_path": None, "removed": removed}

    backup = _backup(settings_path)
    hooks["PostToolUse"] = keep
    data["hooks"] = hooks
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "action": "uninstalled",
        "settings_path": str(settings_path),
        "backup_path": str(backup) if backup else None,
        "removed": removed,
    }


def hook_status(settings_path: Path = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    """Inspect whether the hook is installed."""
    if not settings_path.exists():
        return {"installed": False, "settings_path": str(settings_path),
                "reason": "settings file does not exist"}
    try:
        data = _read_settings(settings_path)
    except RuntimeError as e:
        return {"installed": False, "settings_path": str(settings_path),
                "reason": str(e)}
    hooks = data.get("hooks") or {}
    post = hooks.get("PostToolUse") if isinstance(hooks, dict) else None
    if not isinstance(post, list):
        return {"installed": False, "settings_path": str(settings_path),
                "reason": "no PostToolUse hooks"}
    return {
        "installed": _has_daimon_hook(post),
        "settings_path": str(settings_path),
    }
