"""Tests for :mod:`daimon.onboard`.

Covers:

  * ``resolve_mcp_command`` — frozen, venv, PATH, fallback ordering.
  * ``install_claude_code_integration`` — fresh install, idempotent
    re-run, refresh-on-path-drift, dry-run, preservation of unrelated
    user state, single-backup invariant.
  * ``uninstall_claude_code_integration`` — with and without
    ``keep_hook``, no-op when nothing is present.
  * ``write_recovery_file`` — writes mode 0600 on POSIX, idempotent.
  * ``run_onboard`` — full happy path, mnemonic-confirmation abort,
    ``wire_claude_code=False``, manifest fetch failure.
"""

from __future__ import annotations

import json
import os
import platform
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from daimon.mining.installer import HOOK_OWNER
from daimon.onboard.claude_code import (
    DAIMON_MCP_BINARY_NAME,
    DAIMON_MCP_SERVER_NAME,
    install_claude_code_integration,
    is_daimon_mcp_present,
    resolve_mcp_command,
    uninstall_claude_code_integration,
)
from daimon.onboard.orchestrator import (
    OnboardResult,
    run_onboard,
    write_recovery_file,
)


IS_WINDOWS = platform.system() == "Windows"
EXE = ".exe" if IS_WINDOWS else ""


# ---------------------------------------------------------------------------
# resolve_mcp_command
# ---------------------------------------------------------------------------

class TestResolveMcpCommand:
    def test_frozen_binary_alongside_executable(self, tmp_path, monkeypatch):
        dist = tmp_path / "dist"
        dist.mkdir()
        fake_exe = dist / f"daimon{EXE}"
        fake_exe.write_bytes(b"")
        target = dist / f"{DAIMON_MCP_BINARY_NAME}{EXE}"
        target.write_bytes(b"")

        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(fake_exe), raising=False)
        # Make sure the venv path doesn't accidentally win.
        monkeypatch.setattr("shutil.which", lambda _name: None)

        assert resolve_mcp_command() == str(target)

    def test_falls_back_to_venv_scripts(self, tmp_path, monkeypatch):
        # Pretend sys.executable is at <venv>/Scripts/python[.exe] (Windows
        # layout) — dmn-mcp[.exe] should be discovered next to it.
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        py = scripts / f"python{EXE}"
        py.write_bytes(b"")
        target = scripts / f"{DAIMON_MCP_BINARY_NAME}{EXE}"
        target.write_bytes(b"")

        monkeypatch.delattr("sys.frozen", raising=False)
        monkeypatch.setattr("sys.executable", str(py), raising=False)
        monkeypatch.setattr("shutil.which", lambda _name: None)

        assert resolve_mcp_command() == str(target)

    def test_falls_back_to_path_lookup(self, tmp_path, monkeypatch):
        # Neither frozen nor venv-adjacent — last resort: shutil.which.
        py = tmp_path / f"python{EXE}"
        py.write_bytes(b"")
        on_path = tmp_path / f"{DAIMON_MCP_BINARY_NAME}-on-path{EXE}"
        on_path.write_bytes(b"")

        monkeypatch.delattr("sys.frozen", raising=False)
        monkeypatch.setattr("sys.executable", str(py), raising=False)
        monkeypatch.setattr("shutil.which", lambda name: str(on_path))

        assert resolve_mcp_command() == str(on_path)

    def test_returns_bare_name_when_nothing_resolves(self, tmp_path, monkeypatch):
        py = tmp_path / f"python{EXE}"
        py.write_bytes(b"")

        monkeypatch.delattr("sys.frozen", raising=False)
        monkeypatch.setattr("sys.executable", str(py), raising=False)
        monkeypatch.setattr("shutil.which", lambda _name: None)

        assert resolve_mcp_command() == DAIMON_MCP_BINARY_NAME


# ---------------------------------------------------------------------------
# install_claude_code_integration
# ---------------------------------------------------------------------------

class TestInstallIntegration:
    def test_fresh_install_writes_both_halves(self, tmp_path):
        settings = tmp_path / "settings.json"
        result = install_claude_code_integration(
            settings_path=settings,
            mcp_command="/abs/path/to/dmn-mcp",
        )

        assert result["hook_action"] == "installed"
        assert result["mcp_action"] == "installed"
        assert result["mcp_command"] == "/abs/path/to/dmn-mcp"
        # No prior file → no backup.
        assert result["backup_path"] is None

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["mcpServers"][DAIMON_MCP_SERVER_NAME] == {
            "command": "/abs/path/to/dmn-mcp"
        }
        post = data["hooks"]["PostToolUse"]
        assert len(post) == 1
        assert post[0]["_owner"] == HOOK_OWNER

    def test_idempotent_when_already_wired(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        second = install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        assert second["hook_action"] == "already_present"
        assert second["mcp_action"] == "already_present"
        # No backup taken because nothing changed.
        assert second["backup_path"] is None

    def test_refreshes_when_command_drifts(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/old/dmn-mcp"
        )
        result = install_claude_code_integration(
            settings_path=settings, mcp_command="/new/dmn-mcp"
        )
        assert result["mcp_action"] == "refreshed"
        assert result["hook_action"] == "already_present"
        assert result["backup_path"] is not None

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert (
            data["mcpServers"][DAIMON_MCP_SERVER_NAME]["command"]
            == "/new/dmn-mcp"
        )
        # Hook untouched, single entry.
        assert len(data["hooks"]["PostToolUse"]) == 1

    def test_preserves_other_mcp_servers_and_hooks(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "mcpServers": {"otherTool": {"command": "/usr/bin/other"}},
            "hooks": {"PostToolUse": [
                {"matcher": "user-rule",
                 "hooks": [{"type": "command", "command": "user_thing"}]}
            ]},
            "unrelated_top_level_key": True,
        }))

        result = install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        assert result["mcp_action"] == "installed"
        assert result["hook_action"] == "installed"

        data = json.loads(settings.read_text(encoding="utf-8"))
        # User's MCP server preserved.
        assert data["mcpServers"]["otherTool"] == {"command": "/usr/bin/other"}
        # Daimon's MCP server added.
        assert data["mcpServers"][DAIMON_MCP_SERVER_NAME] == {
            "command": "/abs/dmn-mcp"
        }
        # User's hook entry preserved alongside ours.
        post = data["hooks"]["PostToolUse"]
        assert len(post) == 2
        assert any(p.get("matcher") == "user-rule" for p in post)
        assert any(p.get("_owner") == HOOK_OWNER for p in post)
        # Unknown top-level keys preserved (no clobber).
        assert data["unrelated_top_level_key"] is True

    def test_dry_run_does_not_write(self, tmp_path):
        settings = tmp_path / "settings.json"
        result = install_claude_code_integration(
            settings_path=settings,
            mcp_command="/abs/dmn-mcp",
            dry_run=True,
        )
        assert result["hook_action"] == "would_install"
        assert result["mcp_action"] == "would_install"
        assert result["backup_path"] is None
        assert not settings.exists()

    def test_dry_run_reports_already_present(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        # Re-running dry should report no-ops.
        result = install_claude_code_integration(
            settings_path=settings,
            mcp_command="/abs/dmn-mcp",
            dry_run=True,
        )
        assert result["hook_action"] == "already_present"
        assert result["mcp_action"] == "already_present"

    def test_single_backup_for_combined_write(self, tmp_path):
        settings = tmp_path / "settings.json"
        # Pre-existing content forces a backup on the next mutating write.
        settings.write_text(json.dumps({"unrelated": 1}))
        result = install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        assert result["backup_path"] is not None
        # Exactly one .bak.* file should have been written.
        backups = list(tmp_path.glob("settings.json.bak.*"))
        assert len(backups) == 1

    def test_rejects_nonobject_top_level(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps([1, 2, 3]))  # array, not object
        with pytest.raises(RuntimeError, match="not an object"):
            install_claude_code_integration(
                settings_path=settings, mcp_command="/abs/dmn-mcp"
            )

    def test_rejects_invalid_mcpservers_shape(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"mcpServers": ["malformed"]}))
        with pytest.raises(RuntimeError, match="mcpServers is not an object"):
            install_claude_code_integration(
                settings_path=settings, mcp_command="/abs/dmn-mcp"
            )

    def test_resolves_command_when_none_passed(self, tmp_path, monkeypatch):
        settings = tmp_path / "settings.json"
        monkeypatch.setattr(
            "daimon.onboard.claude_code.resolve_mcp_command",
            lambda: "/resolved/dmn-mcp",
        )
        result = install_claude_code_integration(settings_path=settings)
        assert result["mcp_command"] == "/resolved/dmn-mcp"


# ---------------------------------------------------------------------------
# is_daimon_mcp_present
# ---------------------------------------------------------------------------

class TestIsDaimonMcpPresent:
    def test_false_when_file_missing(self, tmp_path):
        assert not is_daimon_mcp_present(
            settings_path=tmp_path / "missing.json"
        )

    def test_false_when_no_mcp_servers(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))
        assert not is_daimon_mcp_present(settings_path=settings)

    def test_true_after_install(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        assert is_daimon_mcp_present(settings_path=settings)


# ---------------------------------------------------------------------------
# uninstall_claude_code_integration
# ---------------------------------------------------------------------------

class TestUninstallIntegration:
    def test_removes_both_halves(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        result = uninstall_claude_code_integration(settings_path=settings)
        assert result["hook_action"] == "uninstalled"
        assert result["mcp_action"] == "uninstalled"
        assert result["backup_path"] is not None

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert DAIMON_MCP_SERVER_NAME not in data.get("mcpServers", {})
        assert data["hooks"]["PostToolUse"] == []

    def test_keep_hook(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        result = uninstall_claude_code_integration(
            settings_path=settings, keep_hook=True
        )
        assert result["mcp_action"] == "uninstalled"
        assert result["hook_action"] == "kept"

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert DAIMON_MCP_SERVER_NAME not in data.get("mcpServers", {})
        # Hook preserved.
        assert any(
            p.get("_owner") == HOOK_OWNER
            for p in data["hooks"]["PostToolUse"]
        )

    def test_noop_when_nothing_present(self, tmp_path):
        settings = tmp_path / "missing.json"
        result = uninstall_claude_code_integration(settings_path=settings)
        assert result["hook_action"] == "not_present"
        assert result["mcp_action"] == "not_present"
        assert result["backup_path"] is None

    def test_dry_run_does_not_write(self, tmp_path):
        settings = tmp_path / "settings.json"
        install_claude_code_integration(
            settings_path=settings, mcp_command="/abs/dmn-mcp"
        )
        before = settings.read_text(encoding="utf-8")
        result = uninstall_claude_code_integration(
            settings_path=settings, dry_run=True
        )
        assert result["hook_action"] == "would_uninstall"
        assert result["mcp_action"] == "would_uninstall"
        assert settings.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# write_recovery_file
# ---------------------------------------------------------------------------

class TestWriteRecoveryFile:
    def test_writes_to_dest(self, tmp_path):
        dest = tmp_path / "recovery.txt"
        path = write_recovery_file("word " * 24, dest=dest)
        assert path == dest
        assert dest.read_text(encoding="utf-8").strip().split() == ["word"] * 24

    def test_creates_parent_dir(self, tmp_path):
        dest = tmp_path / "nested" / "missing" / "recovery.txt"
        write_recovery_file("a b c d", dest=dest)
        assert dest.is_file()

    @pytest.mark.skipif(IS_WINDOWS, reason="POSIX permission semantics")
    def test_mode_is_0600_on_posix(self, tmp_path):
        dest = tmp_path / "recovery.txt"
        write_recovery_file("a b c", dest=dest)
        mode = dest.stat().st_mode & 0o777
        assert mode == 0o600

    def test_idempotent_overwrite(self, tmp_path):
        dest = tmp_path / "recovery.txt"
        write_recovery_file("first", dest=dest)
        write_recovery_file("second", dest=dest)
        assert dest.read_text(encoding="utf-8").strip() == "second"

    def test_rejects_empty_mnemonic(self, tmp_path):
        with pytest.raises(ValueError):
            write_recovery_file("", dest=tmp_path / "x.txt")


# ---------------------------------------------------------------------------
# run_onboard — full flow with stubbed dependencies
# ---------------------------------------------------------------------------

@dataclass
class _FakeIdentity:
    pubkey_hex: str
    mnemonic: str = ""


@dataclass
class _FakeManifest:
    pack_version: str
    starter_card_ids: List[str]
    card_count: int = 12


@pytest.fixture
def onboard_sandbox(tmp_path, monkeypatch):
    """Sandbox config dirs + stub heavy IO so run_onboard is hermetic."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    private_key = config_dir / "identity.key"

    monkeypatch.setattr(
        "daimon.identity.keys.CONFIG_DIR", config_dir, raising=False
    )
    monkeypatch.setattr(
        "daimon.identity.keys.PRIVATE_KEY_PATH", private_key, raising=False
    )

    yield {
        "config_dir": config_dir,
        "private_key": private_key,
        "tmp_path": tmp_path,
    }


def _stub_run_onboard_deps(
    monkeypatch,
    *,
    identity: _FakeIdentity,
    manifest: Optional[_FakeManifest],
    fetched: Optional[List[str]] = None,
    failed: Optional[Dict[str, str]] = None,
    spawn_pid: Optional[int] = 4242,
) -> Dict[str, List[Any]]:
    """Patch ``run_onboard``'s lazy imports. Returns a call-tracking dict."""
    calls: Dict[str, List[Any]] = {
        "generate": [],
        "load": [],
        "fetch_card": [],
        "fetch_manifest": [],
        "spawn": [],
        "install_cc": [],
        "resolve": [],
    }

    def _gen(force=False):
        calls["generate"].append({"force": force})
        return identity

    def _load():
        calls["load"].append(True)
        return identity

    def _fetch_manifest(show_progress=False):
        calls["fetch_manifest"].append({"show_progress": show_progress})
        if manifest is None:
            from daimon.update.fetcher import ArtUpdateError
            raise ArtUpdateError("fake manifest fetch failure")
        return manifest

    def _fetch_card(card_id, manifest, show_progress=False):
        calls["fetch_card"].append(card_id)
        if failed and card_id in failed:
            from daimon.update.fetcher import ArtUpdateError
            raise ArtUpdateError(failed[card_id])
        if fetched is not None and card_id not in fetched:
            from daimon.update.fetcher import ArtUpdateError
            raise ArtUpdateError("not in fetched list")
        return tmp_artpath(card_id)

    def tmp_artpath(card_id):
        return Path("/tmp") / f"{card_id}.png"

    def _spawn():
        calls["spawn"].append(True)
        return spawn_pid

    def _install_cc(**kwargs):
        calls["install_cc"].append(kwargs)
        return {
            "hook_action": "installed",
            "mcp_action": "installed",
            "settings_path": str(kwargs.get("settings_path")),
            "backup_path": None,
            "mcp_command": kwargs.get("mcp_command"),
        }

    def _resolve():
        calls["resolve"].append(True)
        return "/abs/dmn-mcp"

    monkeypatch.setattr("daimon.identity.generate_identity", _gen)
    monkeypatch.setattr("daimon.identity.load_identity", _load)
    monkeypatch.setattr(
        "daimon.update.manifest.fetch_manifest", _fetch_manifest
    )
    monkeypatch.setattr("daimon.update.lazy.fetch_card", _fetch_card)
    monkeypatch.setattr(
        "daimon.update.prefetch.spawn_prefetch_subprocess", _spawn
    )
    monkeypatch.setattr(
        "daimon.onboard.claude_code.install_claude_code_integration",
        _install_cc,
    )
    monkeypatch.setattr(
        "daimon.onboard.claude_code.resolve_mcp_command", _resolve
    )
    return calls


class TestRunOnboardHappyPath:
    def test_fresh_identity_full_flow(self, onboard_sandbox, monkeypatch):
        identity = _FakeIdentity(
            pubkey_hex="ab" * 32,
            mnemonic=" ".join(["word"] * 24),
        )
        manifest = _FakeManifest(
            pack_version="v1_alpha",
            starter_card_ids=["c1", "c2", "c3"],
            card_count=12,
        )
        calls = _stub_run_onboard_deps(
            monkeypatch,
            identity=identity,
            manifest=manifest,
            fetched=["c1", "c2", "c3"],
        )
        # Force "fresh identity" branch — PRIVATE_KEY_PATH does NOT exist yet.
        log_lines: List[str] = []
        result = run_onboard(
            confirm_mnemonic=lambda _m: True,
            wire_claude_code=True,
            spawn_prefetch=True,
            log=log_lines.append,
        )

        assert result.identity_action == "generated"
        assert result.pubkey_hex == identity.pubkey_hex
        assert result.mnemonic == identity.mnemonic
        assert result.recovery_path is not None
        assert Path(result.recovery_path).read_text(encoding="utf-8")\
            .strip().split() == ["word"] * 24

        assert result.manifest_action == "installed"
        assert result.manifest_version == "v1_alpha"
        assert result.starter_fetched == ["c1", "c2", "c3"]
        assert result.starter_failed == []

        assert result.prefetch_pid == 4242

        assert result.claude_code_action == "wired"
        assert result.claude_code_mcp_command == "/abs/dmn-mcp"

        # Ensure the lazy paths fired in the expected order.
        assert calls["generate"] and not calls["load"]
        assert calls["fetch_manifest"]
        assert calls["fetch_card"] == ["c1", "c2", "c3"]
        assert calls["spawn"]
        assert calls["install_cc"]

    def test_existing_identity_skips_recovery(self, onboard_sandbox, monkeypatch):
        # Pre-create the identity file so the orchestrator takes the
        # "already present" branch.
        onboard_sandbox["private_key"].write_bytes(b"fake-pem")
        identity = _FakeIdentity(pubkey_hex="cd" * 32, mnemonic="")
        manifest = _FakeManifest(
            pack_version="v1_alpha", starter_card_ids=[], card_count=4
        )
        _stub_run_onboard_deps(
            monkeypatch, identity=identity, manifest=manifest,
            fetched=[], spawn_pid=None,
        )

        result = run_onboard(
            confirm_mnemonic=lambda _m: True,
            wire_claude_code=True,
            spawn_prefetch=False,
        )
        assert result.identity_action == "already_present"
        assert result.mnemonic == ""
        # No mnemonic → no recovery file.
        assert result.recovery_path is None


class TestRunOnboardConfirmGate:
    def test_aborts_when_confirmation_returns_false(
        self, onboard_sandbox, monkeypatch
    ):
        identity = _FakeIdentity(
            pubkey_hex="ef" * 32, mnemonic=" ".join(["zz"] * 24)
        )
        manifest = _FakeManifest(
            pack_version="v1_alpha", starter_card_ids=["c1"], card_count=1
        )
        calls = _stub_run_onboard_deps(
            monkeypatch, identity=identity, manifest=manifest, fetched=["c1"]
        )

        result = run_onboard(confirm_mnemonic=lambda _m: False)

        # We aborted *after* recovery file but *before* manifest fetch.
        assert result.identity_action == "generated"
        assert result.recovery_path is not None
        assert result.manifest_action == "skipped"
        assert result.claude_code_action == "skipped"
        assert calls["fetch_manifest"] == []
        assert calls["install_cc"] == []


class TestRunOnboardWireOff:
    def test_skips_claude_code_when_disabled(self, onboard_sandbox, monkeypatch):
        identity = _FakeIdentity(
            pubkey_hex="aa" * 32, mnemonic=" ".join(["x"] * 24)
        )
        manifest = _FakeManifest(
            pack_version="v1_alpha", starter_card_ids=[], card_count=0
        )
        calls = _stub_run_onboard_deps(
            monkeypatch, identity=identity, manifest=manifest,
            fetched=[], spawn_pid=None,
        )
        result = run_onboard(
            confirm_mnemonic=None,
            wire_claude_code=False,
            spawn_prefetch=False,
        )
        assert result.claude_code_action == "skipped"
        assert calls["install_cc"] == []


class TestRunOnboardManifestFailure:
    def test_manifest_failure_is_reported_not_raised(
        self, onboard_sandbox, monkeypatch
    ):
        identity = _FakeIdentity(
            pubkey_hex="aa" * 32, mnemonic=" ".join(["x"] * 24)
        )
        _stub_run_onboard_deps(
            monkeypatch, identity=identity, manifest=None,
            spawn_pid=None,
        )
        result = run_onboard(
            confirm_mnemonic=None,
            wire_claude_code=False,
            spawn_prefetch=False,
        )
        assert result.manifest_action == "failed"
        assert "fake manifest fetch failure" in (result.manifest_error or "")
        # No starter prefetch attempted when manifest failed.
        assert result.starter_fetched == []
        assert result.starter_failed == []

    def test_partial_starter_failure_recorded(self, onboard_sandbox, monkeypatch):
        identity = _FakeIdentity(
            pubkey_hex="aa" * 32, mnemonic=" ".join(["x"] * 24)
        )
        manifest = _FakeManifest(
            pack_version="v1",
            starter_card_ids=["good", "bad"],
            card_count=2,
        )
        _stub_run_onboard_deps(
            monkeypatch,
            identity=identity, manifest=manifest,
            fetched=["good"], failed={"bad": "boom"},
            spawn_pid=None,
        )
        result = run_onboard(
            confirm_mnemonic=None,
            wire_claude_code=False,
            spawn_prefetch=False,
        )
        assert result.starter_fetched == ["good"]
        assert result.starter_failed == [["bad", "boom"]]
        assert result.manifest_action == "installed"


# ---------------------------------------------------------------------------
# OnboardResult round-trip
# ---------------------------------------------------------------------------

class TestOnboardResultDict:
    def test_to_dict_round_trip(self):
        r = OnboardResult(
            identity_action="generated",
            pubkey_hex="00" * 32,
            mnemonic="a b c",
            recovery_path="/tmp/recovery.txt",
            manifest_action="installed",
            manifest_version="v1",
            starter_fetched=["c1"],
            starter_failed=[["c2", "boom"]],
            prefetch_pid=12,
            claude_code_action="wired",
            claude_code_settings="/x",
            claude_code_backup="/x.bak",
            claude_code_mcp_command="/abs/dmn-mcp",
        )
        d = r.to_dict()
        # JSON-serialisable.
        assert json.dumps(d)
        assert d["starter_failed"] == [["c2", "boom"]]
        assert d["claude_code_mcp_command"] == "/abs/dmn-mcp"
