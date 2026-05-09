"""Pity system tests.

Covers:
  - get_pity_state returns correct counter from ledger
  - adjusted_rarity_weights: no change below soft pity
  - adjusted_rarity_weights: boosts rare+ during soft pity
  - adjusted_rarity_weights: removes common/uncommon at hard pity
  - perform_multi_pull returns up to N receipts, stops on insufficient balance
"""

from __future__ import annotations

import json

import pytest

from daimon import collection as collection_mod
from daimon.identity import generate_identity
from daimon.identity import keys as identity_keys
from daimon.mining import formula as formula_mod
from daimon.mining import ledger as ledger_mod
from daimon.mining.ledger import append_mine_entry
from daimon.pity import (
    HARD_PITY_AT,
    SOFT_PITY_START,
    adjusted_rarity_weights,
    get_pity_state,
)
from daimon.pulls import perform_multi_pull, perform_pull


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "ledger.jsonl")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", cfg / "coll.json")
    monkeypatch.setattr(formula_mod, "_NOVELTY_MEMORY", {})
    generate_identity(force=True)
    return {"ledger": cfg / "ledger.jsonl", "collection": cfg / "coll.json"}


def _fund(amount: int) -> None:
    while amount > 0:
        chunk = min(amount, 50)
        append_mine_entry(tool_name="Edit", amount=chunk,
                          factors={}, novelty_key=f"k{amount}")
        amount -= chunk


def test_pity_state_empty_ledger(isolated):
    state = get_pity_state()
    assert state["pulls_since_rare_plus"] == 0
    assert state["total_pulls"] == 0
    assert state["soft_pity_active"] is False
    assert state["next_is_guaranteed"] is False


def test_pity_state_counts_common_pulls(isolated):
    _fund(500)
    for i in range(5):
        perform_pull(seed=bytes([i]) * 32, use_pity=False)

    state = get_pity_state()
    assert state["total_pulls"] == 5
    # Might be less than 5 if any pull landed rare+ by chance
    assert state["pulls_since_rare_plus"] <= 5


def test_pity_state_resets_on_rare_plus(isolated):
    """Manually inject pull entries to simulate a rare+ reset."""
    ledger = isolated["ledger"]

    entries = []
    for i in range(3):
        entries.append(json.dumps({
            "kind": "pull", "rarity": "common", "amount": -100,
            "card_id": f"c{i}", "serial": f"s{i}",
        }))
    entries.append(json.dumps({
        "kind": "pull", "rarity": "rare", "amount": -100,
        "card_id": "r0", "serial": "sr0",
    }))
    entries.append(json.dumps({
        "kind": "pull", "rarity": "common", "amount": -100,
        "card_id": "c3", "serial": "s3",
    }))
    ledger.write_text("\n".join(entries) + "\n")

    state = get_pity_state(ledger)
    assert state["pulls_since_rare_plus"] == 1
    assert state["total_pulls"] == 5


def test_adjusted_weights_no_change_below_soft(isolated):
    base = {"common": 500, "uncommon": 300, "rare": 100, "epic": 15, "legendary": 5}
    result = adjusted_rarity_weights(base, 10)
    assert result == base


def test_adjusted_weights_boosts_during_soft_pity(isolated):
    base = {"common": 500, "uncommon": 300, "rare": 100, "epic": 15, "legendary": 5}
    result = adjusted_rarity_weights(base, 35)
    assert result["rare"] > base["rare"]
    assert result["epic"] > base["epic"]
    assert result["legendary"] > base["legendary"]
    assert result["common"] < base["common"]


def test_adjusted_weights_hard_pity_removes_common(isolated):
    base = {"common": 500, "uncommon": 300, "rare": 100, "epic": 15, "legendary": 5}
    result = adjusted_rarity_weights(base, HARD_PITY_AT)
    assert "common" not in result
    assert "uncommon" not in result
    assert "rare" in result
    assert "epic" in result
    assert "legendary" in result


def test_multi_pull_returns_receipts(isolated):
    _fund(350)
    receipts = perform_multi_pull(count=3)
    assert len(receipts) == 3
    assert all(r.card_id for r in receipts)
    assert receipts[-1].balance_after == 50


def test_multi_pull_stops_on_insufficient(isolated):
    _fund(250)
    receipts = perform_multi_pull(count=5)
    assert len(receipts) == 2
    assert receipts[-1].balance_after == 50
