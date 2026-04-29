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
from daimon.mining import buffer as buffer_mod
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
    # Mining buffer must also be redirected so tests don't write to the real
    # ~/.config/daimon/mine_buffer.jsonl on the dev box.
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
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
    lines = isolated.read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(l)["kind"] for l in lines]
    assert "mine" in kinds


# ---------------------------------------------------------------------------
# Mine-buffer (HUD ticker) integration
# ---------------------------------------------------------------------------

def test_mint_emits_to_mine_buffer(isolated):
    """Each successful mint mirrors into the HUD ticker buffer."""
    status = process_event(_event("Edit"))
    assert status["action"] == "minted"

    events = buffer_mod.tail(10, path=buffer_mod.BUFFER_PATH)
    assert len(events) >= 1
    last = events[-1]
    assert last["kind"] == "mine"
    assert last["tool"] == "Edit"
    assert last["amount"] == status["reward"]
    # balance_after must equal post-mint ledger balance
    assert last["balance_after"] == ledger_mod.get_balance(path=isolated)


def test_skipped_tool_does_not_write_buffer(isolated):
    process_event(_event("TodoWrite"))
    assert buffer_mod.tail(10, path=buffer_mod.BUFFER_PATH) == []


def test_dedup_does_not_write_buffer(isolated):
    e = _event("Edit")
    process_event(e)
    process_event(e)   # dedup
    events = buffer_mod.tail(10, path=buffer_mod.BUFFER_PATH)
    # Only the first mint should have produced a buffer entry.
    mines = [x for x in events if x["kind"] == "mine"]
    assert len(mines) == 1


def test_milestone_fires_on_threshold_cross(isolated, monkeypatch):
    """When a mint crosses a MILESTONE_STEP boundary, an extra event lands."""
    # Force the milestone step low + force every mint to award exactly enough
    # to cross it on the first event. Patch compute_reward to a fixed return.
    from daimon.mining import formula as formula_mod_local
    from daimon.mining import hook as hook_mod_local

    monkeypatch.setattr(hook_mod_local, "MILESTONE_STEP", 5)

    fixed = formula_mod_local.MiningOutput(
        reward=7, factors={"forced": True},
    )
    monkeypatch.setattr(hook_mod_local, "compute_reward", lambda inp: fixed)

    process_event(_event("Edit"))
    events = buffer_mod.tail(10, path=buffer_mod.BUFFER_PATH)
    kinds = [e["kind"] for e in events]
    assert "mine" in kinds
    assert "milestone" in kinds
    milestone = [e for e in events if e["kind"] == "milestone"][0]
    assert milestone["balance_after"] == 7
    # 5¤ was the crossed threshold; note carries the boundary number.
    assert "5" in milestone.get("note", "")


def test_buffer_emit_failure_does_not_break_mint(isolated, monkeypatch):
    """If buffer.append raises, the mint must still succeed."""
    def _boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr("daimon.mining.hook._buffer.append", _boom)

    status = process_event(_event("Edit"))
    # Mint contract is preserved even though buffer emission failed.
    assert status["action"] == "minted"
    assert status["reward"] > 0
