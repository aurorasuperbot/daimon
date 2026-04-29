"""End-to-end pull tests.

Covers:
  - perform_pull happy path: deducts cost, mints serial, appends collection
  - same seed → same card_id, fresh serials each time
  - perform_pull raises InsufficientBalanceError when broke
  - perform_pull raises RuntimeError on corrupt ledger (refuse to spend)
  - can_pull returns balance/cost/needed correctly
"""

from __future__ import annotations

import json

import pytest

from daimon import collection as collection_mod
from daimon.identity import generate_identity
from daimon.identity import keys as identity_keys
from daimon.mining import formula as formula_mod
from daimon.mining import ledger as ledger_mod
from daimon.mining.ledger import (
    InsufficientBalanceError,
    append_mine_entry,
)
from daimon.pulls import can_pull, perform_pull


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
    # Multiple small mines to skirt the per-call cap (100).
    while amount > 0:
        chunk = min(amount, 50)
        append_mine_entry(tool_name="Edit", amount=chunk,
                          factors={}, novelty_key=f"k{amount}")
        amount -= chunk


def test_pull_deducts_cost(isolated):
    _fund(150)
    receipt = perform_pull(seed=b"\xab" * 32)
    assert receipt.balance_after == 50
    assert receipt.cost == 100


def test_pull_mints_serial_and_collection(isolated):
    _fund(100)
    receipt = perform_pull(seed=b"\xab" * 32)
    coll = json.loads(isolated["collection"].read_text(encoding="utf-8"))
    assert len(coll["serials"]) == 1
    assert coll["serials"][0]["serial"] == receipt.serial.serial
    assert coll["serials"][0]["card_id"] == receipt.card_id
    assert coll["pubkey_hex"]


def test_pull_same_seed_same_card_different_serial(isolated):
    _fund(300)
    seed = b"\xcd" * 32
    r1 = perform_pull(seed=seed)
    r2 = perform_pull(seed=seed)
    assert r1.card_id == r2.card_id
    assert r1.serial.serial != r2.serial.serial


def test_pull_insufficient_raises(isolated):
    with pytest.raises(InsufficientBalanceError):
        perform_pull(seed=b"\x00" * 32)


def test_pull_links_serial_to_ledger_hash(isolated):
    _fund(100)
    receipt = perform_pull(seed=b"\xef" * 32)
    coll = json.loads(isolated["collection"].read_text(encoding="utf-8"))
    assert coll["serials"][0]["ledger_entry_hash"] == receipt.ledger_entry_hash


def test_pull_refuses_corrupt_ledger(isolated):
    _fund(100)
    # Corrupt the ledger by editing an amount.
    p = isolated["ledger"]
    lines = p.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[1])
    e["amount"] = 9999
    lines[1] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")

    with pytest.raises(RuntimeError, match="ledger verification failed"):
        perform_pull(seed=b"\xab" * 32)


def test_can_pull_reports_state(isolated):
    info = can_pull()
    assert info["can_pull"] is False
    assert info["balance"] == 0
    assert info["needed"] == 100
    _fund(150)
    info = can_pull()
    assert info["can_pull"] is True
    assert info["balance"] == 150
    assert info["needed"] == 0
