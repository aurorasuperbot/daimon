"""Gacha pulls — spend currency, roll a card, mint a serial.

Pull pipeline:

  1. Resolve identity (raise if not initialized)
  2. Verify the ledger is intact (refuse to spend on a corrupt log)
  3. Generate a 32-byte seed (from arg or os.urandom)
  4. Roll the catalog with that seed → (card_id, rarity, payload)
  5. Append a `pull` entry to the ledger (deducts PULL_COST)
     - Raises InsufficientBalanceError if balance < cost
  6. Mint a UUID serial linked to the ledger entry hash
  7. Append the serial to the collection
  8. Return PullReceipt

The ledger entry is written *before* the collection entry. If the collection
write fails, the currency is already gone — but the next `daimon mine status`
will still show the pull, and the collection can be reconciled from the
ledger via `reconcile_collection_from_ledger()`. (V1.5.)

Determinism: pass a fixed seed → identical card_id outcome. The serial UUID
is always fresh, since it's an instance ID, not a roll outcome.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.catalog import (
    DEFAULT_CATALOG_ID,
    Catalog,
    load_catalog,
    roll_pull,
)
from daimon import collection as _collection_mod
from daimon.collection import (
    Serial,
    append_serial,
    new_serial,
)
from daimon.identity import Identity, load_identity
from daimon.mining import ledger as _ledger_mod
from daimon.mining.formula import PULL_COST
from daimon.mining.ledger import (
    InsufficientBalanceError,
    append_pull_entry,
    entry_hash,
    get_balance,
    verify_ledger,
)
from daimon.pity import adjusted_rarity_weights, get_pity_state


@dataclass(frozen=True)
class PullReceipt:
    serial: Serial
    card_id: str
    rarity: str
    pack: str
    cost: int
    balance_after: int
    seed_hex: str
    ledger_entry_hash: str
    payload: Dict[str, Any]      # full card JSON for the agent / render layer

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial.serial,
            "card_id": self.card_id,
            "rarity": self.rarity,
            "pack": self.pack,
            "cost": self.cost,
            "balance_after": self.balance_after,
            "seed_hex": self.seed_hex,
            "ledger_entry_hash": self.ledger_entry_hash,
            "payload": self.payload,
            "edition": self.serial.edition,
        }


def perform_pull(
    *,
    catalog_name: str = DEFAULT_CATALOG_ID,
    seed: Optional[bytes] = None,
    identity: Optional[Identity] = None,
    cost: int = PULL_COST,
    ledger_path: Optional[Path] = None,
    collection_path: Optional[Path] = None,
    catalog: Optional[Catalog] = None,
    use_pity: bool = True,
    _skip_verify: bool = False,
) -> PullReceipt:
    """Execute one pull. Raises:
      - FileNotFoundError: no identity (`daimon init` not run)
      - InsufficientBalanceError: balance < cost
      - RuntimeError: ledger corruption (refuse to spend)
    """
    identity = identity or load_identity()
    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH
    if collection_path is None:
        collection_path = _collection_mod.COLLECTION_PATH

    if not _skip_verify:
        verification = verify_ledger(ledger_path,
                                     expected_pubkey_hex=identity.pubkey_hex)
        if not verification.get("ok"):
            raise RuntimeError(
                f"ledger verification failed: {verification.get('errors')}"
            )

    if catalog is None:
        catalog = load_catalog(catalog_name)
    seed_bytes = seed if seed is not None else os.urandom(32)
    if len(seed_bytes) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(seed_bytes)}")

    override_weights = None
    if use_pity:
        pity = get_pity_state(ledger_path)
        override_weights = adjusted_rarity_weights(
            catalog.rarity_weights,
            pity["pulls_since_rare_plus"],
        )

    pull = roll_pull(catalog, seed_bytes, override_weights=override_weights)

    entry = append_pull_entry(
        cost=cost,
        serial="pending",
        card_id=pull.card.card_id,
        pack=pull.card.pack,
        rarity=pull.rarity,
        identity=identity,
        path=ledger_path,
    )
    eh = entry_hash(entry)

    edition = "1st" if pull.card.pack == "v1_alpha" else None
    serial = new_serial(
        card_id=pull.card.card_id,
        pack=pull.card.pack,
        rarity=pull.rarity,
        minted_via="pull",
        ledger_entry_hash=eh,
        edition=edition,
        original_owner_pubkey=identity.pubkey_hex,
    )
    append_serial(serial, pubkey_hex=identity.pubkey_hex, path=collection_path)

    balance_after = get_balance(ledger_path)

    return PullReceipt(
        serial=serial,
        card_id=pull.card.card_id,
        rarity=pull.rarity,
        pack=pull.card.pack,
        cost=cost,
        balance_after=balance_after,
        seed_hex=pull.seed_hex,
        ledger_entry_hash=eh,
        payload=pull.card.payload,
    )


def perform_multi_pull(
    count: int = 10,
    *,
    catalog_name: str = DEFAULT_CATALOG_ID,
    cost: int = PULL_COST,
    ledger_path: Optional[Path] = None,
    collection_path: Optional[Path] = None,
) -> List[PullReceipt]:
    """Execute up to *count* pulls. Stops early on insufficient balance."""
    identity = load_identity()
    catalog = load_catalog(catalog_name)

    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH
    if collection_path is None:
        collection_path = _collection_mod.COLLECTION_PATH

    verification = verify_ledger(ledger_path,
                                 expected_pubkey_hex=identity.pubkey_hex)
    if not verification.get("ok"):
        raise RuntimeError(
            f"ledger verification failed: {verification.get('errors')}"
        )

    receipts: List[PullReceipt] = []
    for _ in range(count):
        try:
            receipt = perform_pull(
                identity=identity,
                catalog=catalog,
                cost=cost,
                ledger_path=ledger_path,
                collection_path=collection_path,
                _skip_verify=True,
            )
            receipts.append(receipt)
        except InsufficientBalanceError:
            break
    return receipts


def can_pull(cost: int = PULL_COST,
             ledger_path: Optional[Path] = None) -> Dict[str, Any]:
    """Cheap check before showing pull UI."""
    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH
    bal = get_balance(ledger_path)
    return {"can_pull": bal >= cost, "balance": bal, "cost": cost,
            "needed": max(0, cost - bal)}
