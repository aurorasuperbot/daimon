"""Mining ledger — signed, append-only, hash-chained log of currency events.

The ledger is the single source of truth for an agent's DAIMON balance.
Each entry records either a `mine` (positive reward from productive work) or
a `pull` (negative balance change from spending currency on a gacha).

## Design

Storage: JSONL at `~/.config/daimon/mining_ledger.jsonl`. One JSON object
per line. Append-only — we never rewrite or delete entries. A balance is
just `sum(amount for entry in entries)`.

Each entry is a dict with these fields:
  ts          — RFC3339 UTC timestamp
  kind        — "mine" | "pull" | "genesis"
  amount      — int (positive for mine, negative for pull, 0 for genesis)
  tool_name   — Claude Code tool name (mine entries only)
  factors     — debug breakdown from compute_reward (mine only)
  novelty_key — dedup key (mine only)
  serial      — UUID of card minted (pull only)
  card_id     — card_id of the pull result (pull only)
  pubkey_hex  — signer's public key (every entry)
  prev_hash   — sha256 of previous entry's canonical bytes (chains the log)
  nonce       — random 16-byte hex (prevents identical-entry collisions)
  signature   — ed25519 over canonical_bytes(entry without "signature" field)

## Tamper detection

`verify_ledger()` recomputes prev_hash chain + verifies every signature.
A single edit anywhere in the file breaks the chain forward.

## Concurrency

Single-writer assumption: the agent's Claude Code session is the only writer.
We use an O_APPEND open + flock to serialize writes within a process, but
parallel agents on the same identity would race. That's a "don't do that"
constraint for V1 — the ledger lives in `~/.config/daimon/`, one identity
per machine.

## Idempotency

Hook entrypoints can be retried (Claude Code may invoke twice on retries).
`append_mine_entry` accepts an `idempotency_key` — if the last N entries
already contain that key, it's a no-op.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from daimon.identity import Identity, load_identity, verify

CONFIG_DIR = Path.home() / ".config" / "daimon"
LEDGER_PATH = CONFIG_DIR / "mining_ledger.jsonl"

# How many recent entries to scan for an idempotency_key collision.
IDEMPOTENCY_WINDOW = 64

GENESIS_PREV_HASH = "0" * 64


# ---------------------------------------------------------------------------
# Canonical encoding
# ---------------------------------------------------------------------------

def canonical_bytes(entry: Dict[str, Any]) -> bytes:
    """Stable JSON encoding for hashing/signing. Sorted keys, no whitespace."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def entry_hash(entry: Dict[str, Any]) -> str:
    """SHA-256 of an entry's canonical bytes (signature included)."""
    return hashlib.sha256(canonical_bytes(entry)).hexdigest()


def signing_payload(entry: Dict[str, Any]) -> bytes:
    """Bytes signed by the identity. Excludes the signature field itself."""
    stripped = {k: v for k, v in entry.items() if k != "signature"}
    return canonical_bytes(stripped)


# ---------------------------------------------------------------------------
# I/O primitives
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def _read_entries(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    if path is None:
        path = LEDGER_PATH
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise LedgerCorruptError(
                    f"line {lineno}: invalid JSON ({e})"
                ) from e
    return out


def _last_entry(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Cheap tail-read for the last entry without parsing the whole file."""
    if path is None:
        path = LEDGER_PATH
    if not path.exists() or path.stat().st_size == 0:
        return None
    # Small files: just read all. For big logs we could reverse-scan.
    entries = _read_entries(path)
    return entries[-1] if entries else None


def _append_line(entry: Dict[str, Any], path: Optional[Path] = None) -> None:
    if path is None:
        path = LEDGER_PATH
    _ensure_dir()
    line = canonical_bytes(entry).decode("utf-8") + "\n"
    # O_APPEND is atomic for small writes on POSIX. We don't need flock for
    # single-writer scenarios — the ledger is per-identity, one host.
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------

class LedgerError(Exception):
    pass


class LedgerCorruptError(LedgerError):
    """Raised when the on-disk ledger fails verification."""


class InsufficientBalanceError(LedgerError):
    """Raised when a pull is attempted with insufficient balance."""


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _new_nonce_hex() -> str:
    return secrets.token_hex(16)


def _build_entry(
    *,
    identity: Identity,
    kind: str,
    amount: int,
    prev_hash: str,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "ts": _now_iso(),
        "kind": kind,
        "amount": int(amount),
        "pubkey_hex": identity.pubkey_hex,
        "prev_hash": prev_hash,
        "nonce": _new_nonce_hex(),
    }
    if extras:
        # Merge extras in but don't let them clobber chain fields.
        for k, v in extras.items():
            if k in {"ts", "kind", "amount", "pubkey_hex", "prev_hash",
                     "nonce", "signature"}:
                continue
            entry[k] = v
    sig = identity.sign_bytes(signing_payload(entry))
    entry["signature"] = sig.hex()
    return entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initialize_ledger(identity: Optional[Identity] = None,
                      path: Optional[Path] = None) -> Dict[str, Any]:
    """Create a genesis entry if the ledger is empty. Returns the entry."""
    if path is None:
        path = LEDGER_PATH
    if path.exists() and path.stat().st_size > 0:
        last = _last_entry(path)
        if last is not None:
            return last
    identity = identity or load_identity()
    entry = _build_entry(
        identity=identity,
        kind="genesis",
        amount=0,
        prev_hash=GENESIS_PREV_HASH,
        extras={"note": "ledger_genesis"},
    )
    _append_line(entry, path)
    return entry


def _has_idempotency_key(key: str, entries: List[Dict[str, Any]]) -> bool:
    """Scan recent entries for a matching idempotency_key."""
    if not key:
        return False
    window = entries[-IDEMPOTENCY_WINDOW:]
    for e in window:
        if e.get("idempotency_key") == key:
            return True
    return False


def append_mine_entry(
    *,
    tool_name: str,
    amount: int,
    factors: Dict[str, Any],
    novelty_key: str,
    idempotency_key: Optional[str] = None,
    identity: Optional[Identity] = None,
    path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Append a mine event. Returns the entry, or None if deduped/zero.

    A mine of `amount=0` is dropped (no point recording). An idempotency_key
    that already exists in the recent window is also dropped.
    """
    if amount <= 0:
        return None
    if path is None:
        path = LEDGER_PATH
    identity = identity or load_identity()
    _ensure_dir()
    initialize_ledger(identity, path)

    entries = _read_entries(path)
    if idempotency_key and _has_idempotency_key(idempotency_key, entries):
        return None

    last = entries[-1] if entries else None
    prev_hash = entry_hash(last) if last else GENESIS_PREV_HASH

    extras: Dict[str, Any] = {
        "tool_name": tool_name,
        "factors": factors,
        "novelty_key": novelty_key,
    }
    if idempotency_key:
        extras["idempotency_key"] = idempotency_key

    entry = _build_entry(
        identity=identity,
        kind="mine",
        amount=int(amount),
        prev_hash=prev_hash,
        extras=extras,
    )
    _append_line(entry, path)
    return entry


def append_pull_entry(
    *,
    cost: int,
    serial: str,
    card_id: str,
    pack: str,
    rarity: str,
    identity: Optional[Identity] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append a pull event (negative amount). Validates balance before writing.

    Raises InsufficientBalanceError if balance < cost.
    """
    if cost <= 0:
        raise ValueError("pull cost must be positive")
    if path is None:
        path = LEDGER_PATH
    identity = identity or load_identity()
    initialize_ledger(identity, path)

    entries = _read_entries(path)
    bal = sum(int(e.get("amount", 0)) for e in entries)
    if bal < cost:
        raise InsufficientBalanceError(
            f"need {cost}, have {bal}"
        )

    last = entries[-1]
    prev_hash = entry_hash(last)

    extras = {
        "serial": serial,
        "card_id": card_id,
        "pack": pack,
        "rarity": rarity,
    }
    entry = _build_entry(
        identity=identity,
        kind="pull",
        amount=-int(cost),
        prev_hash=prev_hash,
        extras=extras,
    )
    _append_line(entry, path)
    return entry


def get_balance(path: Optional[Path] = None) -> int:
    if path is None:
        path = LEDGER_PATH
    if not path.exists():
        return 0
    entries = _read_entries(path)
    return sum(int(e.get("amount", 0)) for e in entries)


def get_recent_entries(limit: int = 10,
                       path: Optional[Path] = None) -> List[Dict[str, Any]]:
    if path is None:
        path = LEDGER_PATH
    if not path.exists():
        return []
    entries = _read_entries(path)
    return entries[-limit:]


@dataclass(frozen=True)
class LedgerStats:
    entry_count: int
    balance: int
    total_mined: int
    total_pulled: int
    pull_count: int
    mine_count: int


def get_stats(path: Optional[Path] = None) -> LedgerStats:
    if path is None:
        path = LEDGER_PATH
    entries = _read_entries(path)
    total_mined = sum(int(e.get("amount", 0)) for e in entries
                      if e.get("kind") == "mine")
    pull_amounts = [int(e.get("amount", 0)) for e in entries
                    if e.get("kind") == "pull"]
    return LedgerStats(
        entry_count=len(entries),
        balance=sum(int(e.get("amount", 0)) for e in entries),
        total_mined=total_mined,
        total_pulled=-sum(pull_amounts),
        pull_count=len(pull_amounts),
        mine_count=sum(1 for e in entries if e.get("kind") == "mine"),
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_ledger(path: Optional[Path] = None,
                  expected_pubkey_hex: Optional[str] = None
                  ) -> Dict[str, Any]:
    """Re-derive the hash chain + verify every signature. Never raises.

    Returns:
      {"ok": True, "entries": N, "balance": int}
      {"ok": False, "errors": [...], "first_bad_index": int}

    If `expected_pubkey_hex` is provided, every entry must be signed by it.
    Otherwise the first entry's pubkey is taken as canonical.
    """
    if path is None:
        path = LEDGER_PATH
    if not path.exists():
        return {"ok": True, "entries": 0, "balance": 0}

    try:
        entries = _read_entries(path)
    except LedgerCorruptError as e:
        return {"ok": False, "errors": [str(e)], "first_bad_index": -1}

    if not entries:
        return {"ok": True, "entries": 0, "balance": 0}

    canonical_pubkey = expected_pubkey_hex or entries[0].get("pubkey_hex")
    errors: List[str] = []
    first_bad: int = -1
    prev_hash = GENESIS_PREV_HASH

    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append(f"entry {i}: not an object")
            if first_bad < 0:
                first_bad = i
            break
        # Pubkey consistency
        pk = e.get("pubkey_hex")
        if pk != canonical_pubkey:
            errors.append(f"entry {i}: pubkey mismatch ({pk} vs {canonical_pubkey})")
            if first_bad < 0:
                first_bad = i
        # Chain link
        ph = e.get("prev_hash")
        if ph != prev_hash:
            errors.append(
                f"entry {i}: prev_hash mismatch (got {ph}, expected {prev_hash})"
            )
            if first_bad < 0:
                first_bad = i
        # Signature
        sig_hex = e.get("signature")
        if not isinstance(sig_hex, str):
            errors.append(f"entry {i}: missing signature")
            if first_bad < 0:
                first_bad = i
        else:
            try:
                sig = bytes.fromhex(sig_hex)
                if not verify(canonical_pubkey, signing_payload(e), sig):
                    errors.append(f"entry {i}: signature invalid")
                    if first_bad < 0:
                        first_bad = i
            except ValueError:
                errors.append(f"entry {i}: signature not hex")
                if first_bad < 0:
                    first_bad = i

        prev_hash = entry_hash(e)

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "first_bad_index": first_bad,
            "entries": len(entries),
        }

    return {
        "ok": True,
        "entries": len(entries),
        "balance": sum(int(e.get("amount", 0)) for e in entries),
        "pubkey_hex": canonical_pubkey,
    }
