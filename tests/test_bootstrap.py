"""Tests for the silent bootstrap (daimon/bootstrap.py)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Point DAIMON_HOME at a tmp dir so bootstrap doesn't touch ~/.daimon.

    Also redirects both Claude Code installer paths into the same tmp dir
    so the bootstrap-wired MCP + mining-hook writes never reach the real
    ``~/.claude/settings.json``.
    """
    home = tmp_path / "daimon_home"
    monkeypatch.setenv("DAIMON_HOME", str(home))

    settings = tmp_path / "claude_settings.json"
    from daimon.mining import installer as hook_installer
    from daimon.mcp import installer as mcp_installer
    monkeypatch.setattr(hook_installer, "DEFAULT_SETTINGS_PATH", settings)
    monkeypatch.setattr(mcp_installer, "DEFAULT_SETTINGS_PATH", settings)

    return home


@pytest.fixture
def isolated_identity(monkeypatch, tmp_path):
    """Redirect identity paths into a tmp dir so silent identity-gen doesn't
    touch the real ~/.config/daimon."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    from daimon.identity import keys as identity_keys
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    return cfg


def test_bootstrap_creates_directory_tree(isolated_home: Path, isolated_identity):
    from daimon.bootstrap import SUBDIRS, ensure_bootstrapped
    ensure_bootstrapped()
    assert isolated_home.is_dir()
    for sub in SUBDIRS:
        assert (isolated_home / sub).is_dir(), f"missing subdir {sub}"


def test_bootstrap_is_idempotent(isolated_home: Path, isolated_identity: Path):
    """Re-running bootstrap when state is current must not change anything."""
    from daimon.bootstrap import ensure_bootstrapped
    from daimon.identity import load_identity
    ensure_bootstrapped()
    first_pubkey = load_identity().pubkey_hex
    first_mtime = (isolated_identity / "identity.key").stat().st_mtime_ns
    ensure_bootstrapped()
    second_pubkey = load_identity().pubkey_hex
    second_mtime = (isolated_identity / "identity.key").stat().st_mtime_ns
    assert first_pubkey == second_pubkey
    assert first_mtime == second_mtime, "identity.key was rewritten on idempotent re-run"


def test_bootstrap_creates_identity_silently_on_first_run(
    isolated_home: Path, isolated_identity: Path,
):
    """First-time bootstrap mints an identity + recovery file with no prompts."""
    from daimon.bootstrap import ensure_bootstrapped
    from daimon.identity import load_identity
    ensure_bootstrapped()
    identity = load_identity()
    assert len(identity.pubkey_hex) == 64
    recovery = isolated_identity / "recovery.txt"
    assert recovery.is_file()
    assert len(recovery.read_text(encoding="utf-8").split()) == 24


def test_bootstrap_does_not_overwrite_existing_identity(
    isolated_home: Path, isolated_identity: Path,
):
    """If an identity already exists, bootstrap leaves it alone."""
    from daimon.bootstrap import ensure_bootstrapped
    from daimon.identity import generate_identity, load_identity
    first = generate_identity()
    ensure_bootstrapped()
    second = load_identity()
    assert second.pubkey_hex == first.pubkey_hex


def test_bootstrap_recovers_when_daimon_home_and_config_dir_diverge(
    isolated_home: Path, isolated_identity: Path,
):
    """Regression: DAIMON_HOME and CONFIG_DIR can resolve independently
    (XDG_CONFIG_HOME-only environments hit this). Bootstrap must not
    skip identity creation just because the directory tree under
    DAIMON_HOME already exists.
    """
    from daimon.bootstrap import ensure_bootstrapped
    from daimon.identity import load_identity

    # First call: create directory tree at DAIMON_HOME (no identity yet).
    ensure_bootstrapped()
    # Wipe the identity from the diverged CONFIG_DIR so the two paths disagree.
    (isolated_identity / "identity.key").unlink()

    # Second call: must re-mint, not no-op based on a stale "already done" marker.
    ensure_bootstrapped()
    identity = load_identity()
    assert len(identity.pubkey_hex) == 64


# ---------------------------------------------------------------------------
# Claude Code wiring subroutines — invoked from ensure_bootstrapped()
# ---------------------------------------------------------------------------

def test_ensure_mining_hook_writes_settings(
    isolated_home: Path, isolated_identity: Path, tmp_path,
):
    """Bootstrap installs the mining hook into the redirected settings.json."""
    from daimon import bootstrap as bs

    bs._ensure_mining_hook()

    settings = tmp_path / "claude_settings.json"
    import json
    data = json.loads(settings.read_text(encoding="utf-8"))
    post = data["hooks"]["PostToolUse"]
    assert any(e.get("_owner") == "daimon" for e in post)


def test_ensure_mcp_server_writes_settings(
    isolated_home: Path, isolated_identity: Path, tmp_path,
):
    from daimon import bootstrap as bs

    bs._ensure_mcp_server()

    settings = tmp_path / "claude_settings.json"
    import json
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "daimon" in data["mcpServers"]
    assert data["mcpServers"]["daimon"]["command"] == "dmn-mcp"


def test_ensure_mcp_server_is_idempotent(
    isolated_home: Path, isolated_identity: Path, tmp_path,
):
    from daimon import bootstrap as bs

    bs._ensure_mcp_server()
    settings = tmp_path / "claude_settings.json"
    first_mtime = settings.stat().st_mtime_ns
    bs._ensure_mcp_server()
    second_mtime = settings.stat().st_mtime_ns
    assert first_mtime == second_mtime, "settings.json was rewritten on idempotent re-run"


