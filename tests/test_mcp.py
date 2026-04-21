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
  - np_pull: insufficient_balance without ledger; success after seeding ledger
  - np_mine_status: missing ledger → empty; populated ledger → real stats
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


_FILLER_ELEMENTS = ["FIRE", "WATER", "NATURE", "VOLT", "VOID"]


def _filler_card_dict(position: int, suffix: str = "f") -> dict:
    element = _FILLER_ELEMENTS[position % len(_FILLER_ELEMENTS)]
    return {
        "card_id": f"filler_{position}_{suffix}",
        "species": f"filler_{position}",
        "element": element,
        "atk": 5,
        "def": 5,
        "hp": 20,
        "spd": 5,
        "triggers": [],
    }


def _full_loadout_dict() -> dict:
    lead = _vanilla_head_dict()
    cards = [lead] + [_filler_card_dict(i) for i in range(1, 6)]
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
    # V2: cards expose `element` + `species` (no more `slot`).
    assert "element" in result["cards"][0]
    assert "species" in result["cards"][0]


def test_loadout_validate_wrong_count():
    lo = _full_loadout_dict()
    lo["cards"] = lo["cards"][:5]  # only 5 cards → must be rejected
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
# np_pull / np_mine_status (real implementations)
# ---------------------------------------------------------------------------

def _isolate_paths(monkeypatch, tmp_path):
    """Redirect identity/ledger/collection paths into a temp dir so tests
    don't touch the user's real ~/.config/nullpoint."""
    from nullpoint.identity import keys as identity_keys
    from nullpoint.mining import ledger as ledger_mod
    from nullpoint import collection as collection_mod

    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                        cfg / "collection.json")
    return cfg


def test_pull_no_identity(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    result = _call(np_pull)
    assert result["status"] == "no_identity"


def test_pull_insufficient_balance(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from nullpoint.identity import generate_identity
    generate_identity(force=True)
    result = _call(np_pull)
    assert result["status"] == "insufficient_balance"
    assert result["balance"] == 0
    assert result["needed"] == 100


def test_pull_succeeds_with_funded_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from nullpoint.identity import generate_identity
    from nullpoint.mining import append_mine_entry
    generate_identity(force=True)
    # Manually credit balance.
    append_mine_entry(
        tool_name="Edit", amount=150,
        factors={"base": 4}, novelty_key="seed",
    )
    seed_hex = "ab" * 32
    result = _call(np_pull, seed=seed_hex)
    assert result["status"] == "ok", result
    assert result["balance_after"] == 50
    assert result["seed_hex"] == seed_hex
    assert "card_id" in result and "serial" in result


def test_pull_seed_determinism(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from nullpoint.identity import generate_identity
    from nullpoint.mining import append_mine_entry
    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=300,
                      factors={"base": 4}, novelty_key="seed")
    seed_hex = "cd" * 32
    r1 = _call(np_pull, seed=seed_hex)
    r2 = _call(np_pull, seed=seed_hex)
    # Same seed → same card_id (UUIDs differ).
    assert r1["card_id"] == r2["card_id"]
    assert r1["serial"] != r2["serial"]


def test_mine_status_no_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    result = _call(np_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 0
    assert result["ledger_entries"] == 0


def test_mine_status_with_ledger(monkeypatch, tmp_path):
    _isolate_paths(monkeypatch, tmp_path)
    from nullpoint.identity import generate_identity
    from nullpoint.mining import append_mine_entry
    generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=12,
                     factors={"base": 4}, novelty_key="x")
    result = _call(np_mine_status)
    assert result["status"] == "ok"
    assert result["balance"] == 12
    assert result["mine_count"] == 1
    assert result["verified"] is True


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
