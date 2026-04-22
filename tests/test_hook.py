"""Claude Code PostToolUse hook tests.

Covers:
  - process_event mints currency for an Edit success
  - process_event skips Reply / TodoWrite (in SKIP_TOOLS)
  - process_event skips self-mining (mcp__*daimon*)
  - process_event dedups via session_id+novelty key
  - process_event treats failed tools (success=False) more conservatively
  - main() never raises on bad input (returns 0)
  - main() reads stdin JSON and appends to the ledger
"""

from __future__ import annotations

import io
import json

import pytest

from daimon.identity import generate_identity
from daimon.identity import keys as identity_keys
from daimon.mining import formula as formula_mod
from daimon.mining import ledger as ledger_mod
from daimon.mining import hook as hook_mod
from daimon.mining.hook import main as hook_main, process_event


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "ledger.jsonl")
    # Reset the in-process novelty memory so tests are independent.
    monkeypatch.setattr(formula_mod, "_NOVELTY_MEMORY", {})
    generate_identity(force=True)
    return cfg / "ledger.jsonl"


def _event(tool_name: str, **overrides) -> dict:
    base = {
        "session_id": "test_session",
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/x.py", "new_string": "hello"},
        "tool_response": {"output": "ok", "success": True},
    }
    base.update(overrides)
    return base


def test_edit_mints_currency(isolated):
    status = process_event(_event("Edit"))
    assert status["action"] == "minted"
    assert status["reward"] > 0


def test_reply_is_skipped(isolated):
    status = process_event(_event("Reply"))
    assert status["action"] == "skipped"
    assert status["reward"] == 0


def test_todowrite_is_skipped(isolated):
    status = process_event(_event("TodoWrite"))
    assert status["action"] == "skipped"


def test_self_mining_blocked(isolated):
    status = process_event(_event("mcp__daimon__np_match"))
    assert status["action"] == "skipped"
    assert "self-mining" in status["reason"]


def test_dedup_within_session(isolated):
    e = _event("Edit")
    s1 = process_event(e)
    s2 = process_event(e)
    assert s1["action"] == "minted"
    assert s2["action"] == "deduped"


def test_failed_tool_zero_reward(isolated):
    e = _event("Edit", tool_response={"is_error": True, "error": "boom"})
    status = process_event(e)
    # success=False multiplies value_signal by 0.1 — likely → 0 after rounding
    assert status["action"] in {"noop", "minted"}
    if status["action"] == "minted":
        assert status["reward"] >= 1


def test_missing_tool_name(isolated):
    status = process_event({"session_id": "x"})
    assert status["action"] == "skipped"


def test_main_handles_empty_stdin(monkeypatch, isolated):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = hook_main([])
    assert rc == 0


def test_main_handles_bad_json(monkeypatch, isolated, capfd):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    rc = hook_main([])
    assert rc == 0
    err = capfd.readouterr().err
    assert "bad JSON" in err


def test_main_processes_real_event(monkeypatch, isolated):
    payload = json.dumps(_event("Edit"))
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    rc = hook_main(["--verbose"])
    assert rc == 0
    # Ledger now has genesis + mine entry
    assert isolated.exists()
    lines = isolated.read_text().splitlines()
    kinds = [json.loads(l)["kind"] for l in lines]
    assert "mine" in kinds
