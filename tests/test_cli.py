"""CLI surface tests — locked-in invariants for ``daimon`` Click commands.

We deliberately don't try to mock Click's own internals. Instead we use
``click.testing.CliRunner`` (the official testing harness) and exercise each
command end-to-end with isolated paths.

Coverage:
  * ``DAIMON_HOME`` / ``XDG_CONFIG_HOME`` env override — verified via subprocess
    (real env-var honored end-to-end without polluting in-process module state).
  * CLI ↔ MCP parity for state.json side-effects: ``daimon match``,
    ``daimon match-npc``, ``daimon pull`` must publish a ``state.json``
    payload so the spectator HUD picks it up. Before 2026-04-22 only the MCP
    surface did this — humans driving the CLI were invisible to ``daimon
    play``.
  * Subcommand registration: every advertised command responds to ``--help``.

Why test the CLI through CliRunner instead of just exercising the helpers?
The whole point of the regression is the WIRING. A unit test on
``publish_match_state`` proves the helper works; only an end-to-end CLI test
proves the helper is actually CALLED from each command.

## Why monkeypatch instead of importlib.reload?

Earlier drafts of this file used ``importlib.reload`` to force module-level
``CONFIG_DIR`` constants to re-read the env var per test. That's catastrophic
for test isolation: reload creates *new* class objects, so an
``InsufficientBalanceError`` raised after reload is a different class than the
``InsufficientBalanceError`` other test modules imported at collection time —
breaking ``pytest.raises`` matches in unrelated tests.

Pattern used here (matches ``tests/test_ledger.py``): monkeypatch the
constants directly on each module that holds them. monkeypatch is symmetric:
it auto-undoes at test teardown, no reload needed. The DAIMON_HOME env var
contract itself is verified in a subprocess (full process isolation).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Point all CONFIG_DIR-derived paths at a tmp dir via monkeypatch.

    Patches the source-of-truth (`identity.keys.CONFIG_DIR`) AND every module
    that imported a derived path at module-load time. monkeypatch auto-reverses
    on test exit, so no cross-test pollution.
    """
    home = tmp_path / "daimon_home"
    home.mkdir()

    from daimon.identity import keys as identity_keys
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", home)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", home / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", home / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", home / "identity.json")

    from daimon import collection as collection_mod
    monkeypatch.setattr(collection_mod, "CONFIG_DIR", home)
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", home / "collection.json")

    from daimon.mining import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "CONFIG_DIR", home)
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", home / "ledger.jsonl")

    from daimon.play import state as state_mod
    monkeypatch.setattr(state_mod, "DEFAULT_STATE_PATH", home / "state.json")
    monkeypatch.setattr(state_mod, "_CONFIG_DIR", home)

    # Mine buffer (HUD ticker stream) — without redirection, tests that fire
    # CLI commands which write to mine_buffer.jsonl (match, pull) leak rows
    # into the user's real ~/.config/daimon/mine_buffer.jsonl. Same isolation
    # hole that bit test_mcp.py before 2026-04-26.
    from daimon.mining import buffer as buffer_mod
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH",
                        home / "mine_buffer.jsonl")

    # Loadouts dir — `daimon loadout-save` and dm_home's loadout-summary
    # walk this directory. Redirected for parity with the MCP isolation.
    from daimon.mcp import server as mcp_server
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", home / "loadouts")

    # Make sure DAIMON_STATE doesn't override our patched default.
    monkeypatch.delenv("DAIMON_STATE", raising=False)

    yield home


def _full_loadout_json() -> dict:
    """Minimal valid 6-card loadout — distinct species so engine accepts it."""
    elements = ["FIRE", "WATER", "NATURE", "VOLT", "VOID", "FIRE"]
    return {
        "cards": [
            {
                "card_id": f"cli_test_{i}",
                "species": f"cli_species_{i}",
                "element": elements[i],
                "atk": 5, "def": 5, "hp": 18, "spd": 5,
                "triggers": [],
            }
            for i in range(6)
        ],
    }


def _run_daimon_subprocess(args: list[str], env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Run `daimon <args>` in a fresh subprocess with env overrides.

    Used for env-var tests so the test process's module state isn't polluted by
    the env var taking effect at import time.
    """
    env = os.environ.copy()
    # Strip any inherited DAIMON_* env vars so the test starts clean.
    for k in list(env.keys()):
        if k.startswith("DAIMON_") or k == "XDG_CONFIG_HOME":
            del env[k]
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "daimon.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# DAIMON_HOME / XDG_CONFIG_HOME env overrides — subprocess (full isolation)
# ---------------------------------------------------------------------------


def test_daimon_home_isolates_identity(tmp_path):
    """Setting DAIMON_HOME=<tmp> must route identity files to <tmp>/.

    Post-bootstrap migration: bootstrap silently mints an identity on the
    first CLI invocation, so we observe it via ``daimon whoami`` instead
    of a now-redundant explicit ``daimon init``.
    """
    home = tmp_path / "daimon_home"
    home.mkdir()
    result = _run_daimon_subprocess(["whoami"], {"DAIMON_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    # Bootstrap writes the identity files into DAIMON_HOME (CONFIG_DIR).
    assert (home / "identity.key").exists()
    assert (home / "identity.pub").exists()
    assert (home / "identity.json").exists()


def test_daimon_home_whoami_is_stable_across_invocations(tmp_path):
    """Two whoami calls in the same DAIMON_HOME must return the same pubkey
    — bootstrap's silent identity generation is one-shot, not re-rolled."""
    home = tmp_path / "daimon_home"
    home.mkdir()
    first = _run_daimon_subprocess(["whoami"], {"DAIMON_HOME": str(home)})
    assert first.returncode == 0
    second = _run_daimon_subprocess(["whoami"], {"DAIMON_HOME": str(home)})
    assert second.returncode == 0
    assert first.stdout.strip() == second.stdout.strip()


def test_xdg_config_home_fallback_when_daimon_home_absent(tmp_path):
    """Without DAIMON_HOME, XDG_CONFIG_HOME/daimon must be the config dir."""
    xdg_root = tmp_path / "xdg"
    xdg_root.mkdir()
    # No DAIMON_HOME — XDG_CONFIG_HOME should win.
    result = _run_daimon_subprocess(
        ["whoami"], {"XDG_CONFIG_HOME": str(xdg_root)},
    )
    assert result.returncode == 0, result.stderr
    expected_dir = xdg_root / "daimon"
    assert (expected_dir / "identity.key").exists(), \
        f"identity.key not at expected XDG path: {expected_dir}"


# ---------------------------------------------------------------------------
# CLI ↔ MCP parity: state.json side-effects (in-process, monkeypatched)
# ---------------------------------------------------------------------------


def test_match_writes_state_json(isolated_home, tmp_path):
    """`daimon match A B` must publish a 'match' view to state.json so the
    HUD picks it up — same side-effect as dm_match."""
    from daimon.cli import main
    runner = CliRunner()

    # Need an identity to share infra paths consistently — though match
    # itself doesn't require it, the publish helper writes alongside.
    runner.invoke(main, ["init"])

    lo_path = tmp_path / "loadout.json"
    lo_path.write_text(json.dumps(_full_loadout_json()))

    seed = "00" * 32
    result = runner.invoke(main, ["match", str(lo_path), str(lo_path), "--seed", seed])
    assert result.exit_code == 0, result.output
    assert "state_id:" in result.output

    state_path = isolated_home / "state.json"
    assert state_path.exists(), "match should write state.json"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["view"] == "match"
    assert state["id"].startswith("match_")
    # The state payload is a V2 Match — has both participants populated.
    assert set(state["data"]["participants"].keys()) == {"player", "opponent"}
    assert len(state["data"]["participants"]["player"]["loadout"]) == 6


def test_match_npc_writes_state_json_with_npc_name(isolated_home, tmp_path):
    """`daimon match-npc loadout sparring_sam` must write state.json and
    the opponent.name field must carry the NPC's real name (not 'opponent')."""
    from daimon.cli import main
    runner = CliRunner()

    runner.invoke(main, ["init"])
    lo_path = tmp_path / "loadout.json"
    lo_path.write_text(json.dumps(_full_loadout_json()))

    seed = "00" * 32
    result = runner.invoke(main, [
        "match-npc", str(lo_path), "sparring_sam", "--seed", seed,
    ])
    assert result.exit_code == 0, result.output
    assert "state_id:" in result.output

    state_path = isolated_home / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["view"] == "match"
    # CRITICAL: opponent name = NPC name, not the literal "opponent"
    assert state["data"]["participants"]["opponent"]["name"] == "Sparring Sam"


def test_pull_writes_state_json(isolated_home):
    """`daimon pull` must publish a 'pull' view so the HUD can play the
    gacha reveal — mirrors dm_pull's MCP side-effect."""
    from daimon.cli import main
    from daimon.mining import append_mine_entry
    runner = CliRunner()

    # Need identity + funded ledger to actually pull.
    runner.invoke(main, ["init"])
    append_mine_entry(
        tool_name="Edit", amount=200,
        factors={"base": 4}, novelty_key="cli_pull_test",
    )

    seed = "ab" * 32
    result = runner.invoke(main, ["pull", "--seed", seed])
    assert result.exit_code == 0, result.output
    assert "state_id:" in result.output

    state_path = isolated_home / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["view"] == "pull"
    assert state["id"].startswith("pull_")
    assert "card_id" in state["data"]
    assert "rarity" in state["data"]


def test_pull_json_output_includes_state_id(isolated_home):
    """`daimon pull --json` should include the state_id in the JSON envelope
    so downstream tooling can correlate the pull with the rendered animation."""
    from daimon.cli import main
    from daimon.mining import append_mine_entry
    runner = CliRunner()

    runner.invoke(main, ["init"])
    append_mine_entry(
        tool_name="Edit", amount=200,
        factors={"base": 4}, novelty_key="cli_pull_json_test",
    )

    result = runner.invoke(main, ["pull", "--seed", "cd" * 32, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "state_id" in payload
    assert payload["state_id"].startswith("pull_")


# ---------------------------------------------------------------------------
# CLI surface registration — regression that no command silently disappeared
# ---------------------------------------------------------------------------


def test_all_subcommands_responsd_to_help():
    """Every advertised subcommand must produce --help output without crashing.

    Catches the case where a refactor accidentally drops a @main.command()
    registration or breaks an import that the command depends on.
    """
    from daimon.cli import main
    runner = CliRunner()

    # Locked surface — bump this list explicitly when adding commands.
    expected = [
        "init", "whoami", "home", "match", "match-npc", "mine",
        "npcs", "pull",
    ]
    for cmd in expected:
        result = runner.invoke(main, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed:\n{result.output}"
        assert "Usage:" in result.output


def test_mine_subcommands_respond_to_help():
    from daimon.cli import main
    runner = CliRunner()
    for sub in ("status", "install-hook", "uninstall-hook", "receipt"):
        result = runner.invoke(main, ["mine", sub, "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output


# ---------------------------------------------------------------------------
# `daimon home` — chat home card renderer
# ---------------------------------------------------------------------------

def test_home_default_prints_summary(isolated_home):
    """Default output: human-readable summary. Bootstrap auto-mints the
    identity on first invocation, so no explicit init is needed."""
    from daimon.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["home"])
    assert result.exit_code == 0, result.output
    assert "DAIMON home:" in result.output
    assert "tier:" in result.output
    assert "balance:" in result.output


def test_home_json_flag_prints_payload(isolated_home):
    from daimon.cli import main
    runner = CliRunner()
    runner.invoke(main, ["init"])
    result = runner.invoke(main, ["home", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert "identity" in payload
    assert "rank" in payload
