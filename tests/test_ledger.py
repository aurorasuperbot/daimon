"""Mining ledger tests.

Covers:
  - Genesis bootstrapping (idempotent, signs with identity)
  - Append mine entries: balance increments, hash chain links
  - Append pull entries: balance enforcement, raises on insufficient
  - Tamper detection: any byte change → verify_ledger fails at right index
  - Idempotency keys collapse duplicates
  - Zero-amount mines are dropped
  - get_stats produces consistent rollups
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.identity import generate_identity, load_identity
from daimon.identity import keys as identity_keys
from daimon.mining import ledger as ledger_mod
from daimon.mining.ledger import (
    GENESIS_PREV_HASH,
    InsufficientBalanceError,
    append_mine_entry,
    append_pull_entry,
    entry_hash,
    get_balance,
    get_stats,
    initialize_ledger,
    verify_ledger,
)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "ledger.jsonl")
    generate_identity(force=True)
    return cfg / "ledger.jsonl"


# ---------------------------------------------------------------------------
# Genesis
# ---------------------------------------------------------------------------

def test_genesis_creates_one_entry(isolated):
    initialize_ledger()
    assert isolated.exists()
    lines = isolated.read_text().splitlines()
    assert len(lines) == 1
    e = json.loads(lines[0])
    assert e["kind"] == "genesis"
    assert e["amount"] == 0
    assert e["prev_hash"] == GENESIS_PREV_HASH
    assert "signature" in e


def test_genesis_idempotent(isolated):
    initialize_ledger()
    initialize_ledger()
    initialize_ledger()
    lines = isolated.read_text().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Mine entries
# ---------------------------------------------------------------------------

def test_mine_entry_increments_balance(isolated):
    e = append_mine_entry(tool_name="Edit", amount=12,
                          factors={"base": 4}, novelty_key="x")
    assert e is not None
    assert e["amount"] == 12
    assert get_balance() == 12


def test_mine_entry_zero_dropped(isolated):
    e = append_mine_entry(tool_name="Edit", amount=0,
                          factors={}, novelty_key="x")
    assert e is None


def test_mine_entry_chains_prev_hash(isolated):
    initialize_ledger()
    a = append_mine_entry(tool_name="Edit", amount=5,
                          factors={}, novelty_key="a")
    b = append_mine_entry(tool_name="Edit", amount=5,
                          factors={}, novelty_key="b")
    assert b["prev_hash"] == entry_hash(a)


def test_idempotency_key_dedupes(isolated):
    a = append_mine_entry(tool_name="Edit", amount=5,
                          factors={}, novelty_key="x",
                          idempotency_key="dup1")
    b = append_mine_entry(tool_name="Edit", amount=5,
                          factors={}, novelty_key="x",
                          idempotency_key="dup1")
    assert a is not None
    assert b is None
    assert get_balance() == 5


# ---------------------------------------------------------------------------
# Pull entries
# ---------------------------------------------------------------------------

def test_pull_requires_balance(isolated):
    with pytest.raises(InsufficientBalanceError):
        append_pull_entry(cost=100, serial="s", card_id="x",
                          pack="v1_alpha", rarity="rare")


def test_pull_decrements_balance(isolated):
    append_mine_entry(tool_name="Edit", amount=200,
                      factors={}, novelty_key="x")
    e = append_pull_entry(cost=100, serial="s", card_id="x",
                          pack="v1_alpha", rarity="rare")
    assert e["amount"] == -100
    assert get_balance() == 100


# ---------------------------------------------------------------------------
# Verification + tamper detection
# ---------------------------------------------------------------------------

def test_verify_passes_on_clean_ledger(isolated):
    append_mine_entry(tool_name="Edit", amount=5,
                      factors={}, novelty_key="x")
    append_mine_entry(tool_name="Bash", amount=3,
                      factors={}, novelty_key="y")
    v = verify_ledger()
    assert v["ok"]
    assert v["balance"] == 8


def test_verify_detects_amount_tamper(isolated):
    append_mine_entry(tool_name="Edit", amount=5,
                      factors={}, novelty_key="x")
    append_mine_entry(tool_name="Edit", amount=5,
                      factors={}, novelty_key="y")
    # Tamper: rewrite the amount in entry 1 (the first mine, idx 1 after genesis)
    lines = isolated.read_text().splitlines()
    e = json.loads(lines[1])
    e["amount"] = 9999
    lines[1] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    isolated.write_text("\n".join(lines) + "\n")

    v = verify_ledger()
    assert not v["ok"]
    assert v["first_bad_index"] == 1


def test_verify_detects_pubkey_tamper(isolated):
    initialize_ledger()
    # Switch identity, append — now pubkey mismatch.
    identity_keys.PRIVATE_KEY_PATH.unlink()
    identity_keys.PUBLIC_KEY_PATH.unlink()
    identity_keys.METADATA_PATH.unlink()
    new_identity = generate_identity(force=True)
    append_mine_entry(tool_name="Edit", amount=5,
                      factors={}, novelty_key="x",
                      identity=new_identity)
    v = verify_ledger(expected_pubkey_hex=new_identity.pubkey_hex)
    # Genesis was signed by old identity → mismatch on entry 0.
    assert not v["ok"]
    assert v["first_bad_index"] == 0


def test_verify_passes_on_empty_ledger(isolated):
    # No initialize_ledger() call — file doesn't exist.
    v = verify_ledger()
    assert v["ok"]
    assert v["entries"] == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_get_stats_rollup(isolated):
    append_mine_entry(tool_name="Edit", amount=10,
                      factors={}, novelty_key="a")
    append_mine_entry(tool_name="Bash", amount=20,
                      factors={}, novelty_key="b")
    append_pull_entry(cost=15, serial="s", card_id="c",
                      pack="v1_alpha", rarity="common")
    s = get_stats()
    assert s.balance == 15
    assert s.total_mined == 30
    assert s.total_pulled == 15
    assert s.mine_count == 2
    assert s.pull_count == 1
    # Genesis + 2 mines + 1 pull = 4
    assert s.entry_count == 4
