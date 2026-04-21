"""Hook installer tests.

Covers:
  - install creates the file + adds the entry
  - install is idempotent (rerun → "already_present", no duplicates)
  - install preserves user-added hooks
  - uninstall removes the nullpoint-owned entry only
  - dry_run never writes
  - hook_status reports correctly
"""

from __future__ import annotations

import json

import pytest

from nullpoint.mining.installer import (
    HOOK_OWNER,
    hook_status,
    install_hook,
    uninstall_hook,
)


def test_install_into_empty(tmp_path):
    settings = tmp_path / "settings.json"
    result = install_hook(settings_path=settings)
    assert result["action"] == "installed"
    assert settings.exists()
    data = json.loads(settings.read_text())
    post = data["hooks"]["PostToolUse"]
    assert len(post) == 1
    assert post[0]["_owner"] == HOOK_OWNER


def test_install_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    install_hook(settings_path=settings)
    second = install_hook(settings_path=settings)
    assert second["action"] == "already_present"
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PostToolUse"]) == 1


def test_install_preserves_user_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PostToolUse": [
            {"matcher": "user", "hooks": [
                {"type": "command", "command": "user_thing"}
            ]}
        ]}
    }))
    install_hook(settings_path=settings)
    data = json.loads(settings.read_text())
    post = data["hooks"]["PostToolUse"]
    assert len(post) == 2
    # User entry survived
    assert any(p.get("matcher") == "user" for p in post)


def test_install_dry_run(tmp_path):
    settings = tmp_path / "settings.json"
    result = install_hook(settings_path=settings, dry_run=True)
    assert result["action"] == "would_install"
    assert not settings.exists()


def test_uninstall_removes_only_nullpoint_entry(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PostToolUse": [
            {"matcher": "user", "hooks": [
                {"type": "command", "command": "user_thing"}
            ]}
        ]}
    }))
    install_hook(settings_path=settings)
    result = uninstall_hook(settings_path=settings)
    assert result["action"] == "uninstalled"
    assert result["removed"] == 1
    data = json.loads(settings.read_text())
    post = data["hooks"]["PostToolUse"]
    assert len(post) == 1
    assert post[0]["matcher"] == "user"


def test_uninstall_when_not_present(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    result = uninstall_hook(settings_path=settings)
    assert result["action"] == "not_present"


def test_hook_status_not_installed(tmp_path):
    settings = tmp_path / "settings.json"
    s = hook_status(settings)
    assert s["installed"] is False


def test_hook_status_installed(tmp_path):
    settings = tmp_path / "settings.json"
    install_hook(settings_path=settings)
    s = hook_status(settings)
    assert s["installed"] is True


def test_install_creates_backup(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    result = install_hook(settings_path=settings)
    assert result["backup_path"]
    backup_path = result["backup_path"]
    # Backup file exists and is a previous version (just "{}")
    from pathlib import Path
    assert Path(backup_path).exists()
    assert Path(backup_path).read_text() == "{}"
