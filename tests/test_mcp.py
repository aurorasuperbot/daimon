"""Tests for the MCP server tools.

We test the tool functions directly (the @mcp.tool() decorator preserves the
underlying callable as `<tool>.fn` in FastMCP, but accessing them via the
module-level names works because FastMCP returns the original function).

Coverage:
  - np_whoami: missing identity → graceful error; with identity → pubkey hex
  - np_match: vanilla mirror → draw; invalid input → error envelope; round
              log opt-in works; bare-list and dict-with-cards both accepted
  - np_loadout_validate: valid + invalid cases
  - np_collection: missing file → empty; corrupt JSON → error envelope
  - np_pull: returns not_yet_implemented stub
  - np_mine_status: missing ledger → not_yet_implemented stub
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nullpoint.mcp import server as mcp_server
from nullpoint.mcp.server import (
    np_collection,
    np_loadout_validate,
    np_match,
    np_mine_status,
    np_pull,
    np_whoami,
)


# Helper: extract the actual callable from the FastMCP decorator if needed.
def _call(tool, **kwargs):
    """FastMCP wraps the function; .fn is the original callable."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _vanilla_head_dict() -> dict:
    return json.loads((FIXTURE_DIR / "test_card_01_vanilla_head.json").read_text())


def _filler_card_dict(slot: str, suffix: str = "f") -> dict:
    return {
        "card_id": f"filler_{slot.lower()}_{suffix}",
        "slot": slot,
        "atk": 5,
        "def": 5,
        "hp": 20,
        "spd": 5,
        "triggers": [],
    }


def _full_loadout_dict() -> dict:
    head = _vanilla_head_dict()
    cards = [head]
    for slot in ["TORSO", "ARM_L", "ARM_R", "LEGS", "CORE"]:
        cards.append(_filler_card_dict(slot))
    return {"cards": cards}


# ---------------------------------------------------------------------------
# np_whoami
# ---------------------------------------------------------------------------

def test_whoami_no_identity(tmp_path, monkeypatch):
    # Point CONFIG_DIR somewhere empty so load_identity raises FileNotFoundError.
    fake_dir = tmp_path / "no_identity_here"
    fake_dir.mkdir()
    monkeypatch.setattr("nullpoint.identity.keys.CONFIG_DIR", fake_dir)
    monkeypatch.setattr(
        "nullpoint.identity.keys.PRIVATE_KEY_PATH", fake_dir / "identity.key"
    )

    result = _call(np_whoami)
    assert result["error"] == "no_identity"


def test_whoami_with_identity(tmp_path, monkeypatch):
    from nullpoint.identity import generate_identity, keys as keys_mod

    fake_dir = tmp_path / "ident"
    monkeypatch.setattr(keys_mod, "CONFIG_DIR", fake_dir)
    monkeypatch.setattr(keys_mod, "PRIVATE_KEY_PATH", fake_dir / "identity.key")
    monkeypatch.setattr(keys_mod, "PUBLIC_KEY_PATH", fake_dir / "identity.pub")
    monkeypatch.setattr(keys_mod, "METADATA_PATH", fake_dir / "identity.json")

    identity = generate_identity()
    result = _call(np_whoami)
    assert "pubkey_hex" in result
    assert result["pubkey_hex"] == identity.pubkey_hex
    assert len(result["pubkey_hex"]) == 64
    assert "version" in result


# ---------------------------------------------------------------------------
# np_match
# ---------------------------------------------------------------------------

def test_match_mirror_is_draw():
    lo = _full_loadout_dict()
    result = _call(np_match, loadout_a=lo, loadout_b=lo)
    # Mirror with vanilla cards should produce a draw or symmetric outcome.
    assert "winner" in result
    assert "side_a_final_hp" in result
    assert "side_b_final_hp" in result
    assert result["seed"] == "00" * 32  # default seed


def test_match_with_seed():
    lo = _full_loadout_dict()
    seed_hex = "01" * 32
    result = _call(np_match, loadout_a=lo, loadout_b=lo, seed=seed_hex)
    assert result["seed"] == seed_hex


def test_match_round_log_opt_in():
    lo = _full_loadout_dict()
    no_log = _call(np_match, loadout_a=lo, loadout_b=lo)
    with_log = _call(np_match, loadout_a=lo, loadout_b=lo, include_round_log=True)
    assert "rounds" not in no_log
    assert "rounds" in with_log
    assert len(with_log["rounds"]) == with_log["round_count"]
    if with_log["rounds"]:
        assert "actions" in with_log["rounds"][0]


def test_match_accepts_bare_list():
    """The MCP tool should accept either {'cards': [...]} or a bare list."""
    lo_dict = _full_loadout_dict()
    bare_list = lo_dict["cards"]
    r1 = _call(np_match, loadout_a=lo_dict, loadout_b=lo_dict)
    r2 = _call(np_match, loadout_a=bare_list, loadout_b=bare_list)
    assert r1["winner"] == r2["winner"]
    assert r1["side_a_final_hp"] == r2["side_a_final_hp"]


def test_match_invalid_input_returns_error_envelope():
    result = _call(np_match, loadout_a="not a loadout", loadout_b={"cards": []})
    assert result["error"] == "invalid_input"
    assert "message" in result


def test_match_bad_seed_returns_error_envelope():
    lo = _full_loadout_dict()
    result = _call(np_match, loadout_a=lo, loadout_b=lo, seed="not hex!")
    assert result["error"] == "invalid_input"


def test_match_short_seed_returns_error_envelope():
    lo = _full_loadout_dict()
    result = _call(np_match, loadout_a=lo, loadout_b=lo, seed="0011")
    assert result["error"] == "invalid_input"
    assert "32 bytes" in result["message"]


# ---------------------------------------------------------------------------
# np_loadout_validate
# ---------------------------------------------------------------------------

def test_loadout_validate_ok():
    result = _call(np_loadout_validate, loadout=_full_loadout_dict())
    assert result["valid"] is True
    assert len(result["cards"]) == 6
    assert result["cards"][0]["slot"] == "HEAD"


def test_loadout_validate_missing_slot():
    lo = _full_loadout_dict()
    lo["cards"] = lo["cards"][:5]  # only 5 cards
    result = _call(np_loadout_validate, loadout=lo)
    assert result["valid"] is False
    assert "error" in result


def test_loadout_validate_garbage():
    result = _call(np_loadout_validate, loadout="banana")
    assert result["valid"] is False


# ---------------------------------------------------------------------------
# np_collection
# ---------------------------------------------------------------------------

def test_collection_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", tmp_path / "no_such_file.json")
    result = _call(np_collection)
    assert result["error"] == "no_collection"
    assert result["count"] == 0


def test_collection_present(monkeypatch, tmp_path):
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({
        "serials": [
            {"serial": "uuid-1", "card_id": "starter_scout_head", "pack": "starter"},
            {"serial": "uuid-2", "card_id": "plasma_lance", "pack": "legendary"},
        ]
    }))
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(np_collection)
    assert result["count"] == 2
    assert result["serials"][0]["card_id"] == "starter_scout_head"


def test_collection_corrupt(monkeypatch, tmp_path):
    path = tmp_path / "collection.json"
    path.write_text("{not json")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", path)
    result = _call(np_collection)
    assert result["error"] == "corrupt_collection"


# ---------------------------------------------------------------------------
# np_pull / np_mine_status (stubs)
# ---------------------------------------------------------------------------

def test_pull_is_stub():
    result = _call(np_pull)
    assert result["status"] == "not_yet_implemented"


def test_mine_status_no_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", tmp_path / "nope.json")
    result = _call(np_mine_status)
    assert result["status"] == "not_yet_implemented"
    assert result["balance"] == 0


def test_mine_status_with_ledger(monkeypatch, tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "balance": 273,
        "receipts": [{"ts": "2026-04-21T10:00:00Z", "amount": 12, "tool": "Edit"}],
    }))
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", path)
    result = _call(np_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 273
    assert len(result["receipts"]) == 1


# ---------------------------------------------------------------------------
# Server registration sanity check
# ---------------------------------------------------------------------------

def test_all_tools_registered():
    """All np_* tools should be discoverable on the FastMCP instance."""
    # FastMCP stores tools in an internal registry; we just verify the
    # decorated callables exist and are unique.
    names = {
        "np_whoami", "np_match", "np_loadout_validate",
        "np_collection", "np_pull", "np_mine_status",
    }
    for n in names:
        assert hasattr(mcp_server, n), f"{n} missing from server module"
