"""Canonical encodings for the engine ↔ arbiter contract.

Every byte that crosses the engine/arbiter boundary is produced or consumed
here. The arbiter (`daimon-arena/scripts/arbitrate.py`) reproduces the same
hashing, signing, and parsing — these two modules MUST stay in sync byte-for-
byte. Tests in `tests/arena/test_encoding.py` cross-verify against the
arbiter's expected output to catch drift.

Six protocol surfaces:
  - PvP commit-reveal     (PROTOCOL_VERSION_PVP)
  - Identity register     (PROTOCOL_VERSION_REGISTER)
  - Match dispute         (PROTOCOL_VERSION_DISPUTE)
  - Card proposal         (PROTOCOL_VERSION_CARD_PROPOSE)
  - Pull ticket claim     (PROTOCOL_VERSION_PULL_CLAIM)
  - Joint match seed      (SEED_LABEL — derived, not signed)

Each signing payload is **prefixed with its protocol version** so a
signature on one cannot be replayed against another (domain separation).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Protocol version labels — each one is a domain-separation prefix.
# Bumping any of these is a hard-break: arbiter, engine, and any cached
# leaderboards must all roll forward together.
# ---------------------------------------------------------------------------

PROTOCOL_VERSION_PVP = "daimon-pvp-v1"
PROTOCOL_VERSION_REGISTER = "daimon-register-v1"
PROTOCOL_VERSION_DISPUTE = "daimon-dispute-v1"
PROTOCOL_VERSION_CARD_PROPOSE = "daimon-card-propose-v1"
PROTOCOL_VERSION_PULL_CLAIM = "daimon-pull-claim-v1"
PROTOCOL_VERSION_TICKET = "daimon-ticket-v1"
PROTOCOL_VERSION_QUEST_CLAIM = "daimon-quest-claim-v1"
PROTOCOL_VERSION_TIER_CLAIM = "daimon-tier-claim-v1"
SEED_LABEL = "daimon-pvp-seed-v1"


# ---------------------------------------------------------------------------
# Canonical JSON — sorted keys, no whitespace, UTF-8.
# This is the form fed into hashes and signatures so two implementations
# always agree on the bytes for a given object.
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    """Stable JSON encoding: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# PvP commit-reveal hashing + signing
# ---------------------------------------------------------------------------

def loadout_commit_hash(loadout_obj: Any, nonce_hex: str) -> str:
    """SHA-256 of canonical(loadout) || nonce_bytes, hex-encoded.

    The challenger publishes this in the Issue body BEFORE knowing the
    opponent's loadout — that's the "commit" half of commit-reveal.
    """
    nonce = bytes.fromhex(nonce_hex)
    payload = canonical_json(loadout_obj) + nonce
    return hashlib.sha256(payload).hexdigest()


def pvp_signing_payload(issue_number: int,
                        loadout_obj: Any,
                        nonce_hex: str) -> bytes:
    """Bytes a player signs at reveal time.

    The signature proves the revealing pubkey actually owns the loadout
    being revealed — i.e. it's not a man-in-the-middle replacing the JSON
    with something the player never agreed to.

    Layout (must match arbitrate.py::signing_payload exactly):

        b"daimon-pvp-v1\\n"
        + str(issue_number).encode()
        + b"\\n"
        + canonical_json(loadout_obj)
        + b"\\n"
        + bytes.fromhex(nonce_hex)
    """
    return (
        PROTOCOL_VERSION_PVP.encode() + b"\n"
        + str(issue_number).encode() + b"\n"
        + canonical_json(loadout_obj) + b"\n"
        + bytes.fromhex(nonce_hex)
    )


def derive_joint_seed(issue_number: int,
                      commit_a: str,
                      commit_b: str,
                      nonce_a: str,
                      nonce_b: str) -> bytes:
    """The match RNG seed, derived from BOTH players' commits + nonces.

    Neither side can grind a favorable seed because their commit is locked
    before they see the other's nonce, and the seed mixes both. Mirror of
    arbitrate.py::derive_joint_seed.
    """
    payload = (
        SEED_LABEL.encode() + b"\n"
        + str(issue_number).encode() + b"\n"
        + commit_a.encode() + b"\n"
        + commit_b.encode() + b"\n"
        + nonce_a.encode() + b"\n"
        + nonce_b.encode()
    )
    return hashlib.sha256(payload).digest()


# ---------------------------------------------------------------------------
# Non-PvP signing payloads (register, dispute, card_propose)
#
# Each prepends its own protocol-version label so signatures from one
# context cannot be replayed in another.
# ---------------------------------------------------------------------------

def register_signing_payload(pubkey_hex: str,
                             handle: str,
                             ts_iso: str) -> bytes:
    """Bytes signed when registering a pubkey↔handle binding with the arena.

    Layout:
        b"daimon-register-v1\\n"
        + pubkey_hex.encode()
        + b"\\n"
        + handle.encode()
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_REGISTER.encode() + b"\n"
        + pubkey_hex.encode() + b"\n"
        + handle.encode() + b"\n"
        + ts_iso.encode()
    )


def dispute_signing_payload(match_id: str,
                            reason: str,
                            ts_iso: str) -> bytes:
    """Bytes signed when opening a dispute.

    Layout:
        b"daimon-dispute-v1\\n"
        + match_id.encode()
        + b"\\n"
        + reason.encode()
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_DISPUTE.encode() + b"\n"
        + match_id.encode() + b"\n"
        + reason.encode() + b"\n"
        + ts_iso.encode()
    )


def pull_claim_signing_payload(github_username: str,
                              ticket_index: int,
                              ts_iso: str) -> bytes:
    """Bytes signed when claiming a pre-minted pull ticket.

    Layout:
        b"daimon-pull-claim-v1\\n"
        + github_username.encode()
        + b"\\n"
        + str(ticket_index).encode()
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_PULL_CLAIM.encode() + b"\n"
        + github_username.encode() + b"\n"
        + str(ticket_index).encode() + b"\n"
        + ts_iso.encode()
    )


def ticket_signing_payload(ticket_data: Dict[str, Any]) -> bytes:
    """Bytes the arbiter signs when minting a pull ticket.

    The ticket_data dict is canonicalized so field ordering doesn't
    invalidate the signature. Domain-separated with PROTOCOL_VERSION_TICKET.

    Layout:
        b"daimon-ticket-v1\\n"
        + canonical_json(ticket_data)
    """
    return (
        PROTOCOL_VERSION_TICKET.encode() + b"\n"
        + canonical_json(ticket_data)
    )


def verify_ticket_signature(ticket_data: Dict[str, Any],
                            sig_hex: str,
                            arbiter_pubkey_hex: str) -> bool:
    """Verify an arbiter's ed25519 signature on a ticket.

    Returns True if valid, False on any verification failure.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pubkey_bytes = bytes.fromhex(arbiter_pubkey_hex)
        sig_bytes = bytes.fromhex(sig_hex)
        pubkey = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        payload = ticket_signing_payload(ticket_data)
        pubkey.verify(sig_bytes, payload)
        return True
    except Exception:
        return False


def card_propose_signing_payload(card_def: Dict[str, Any],
                                 ts_iso: str) -> bytes:
    """Bytes signed when proposing a new card definition.

    The card_def itself is canonicalized so any whitespace / key reordering
    in transit doesn't invalidate the signature.

    Layout:
        b"daimon-card-propose-v1\\n"
        + canonical_json(card_def)
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_CARD_PROPOSE.encode() + b"\n"
        + canonical_json(card_def) + b"\n"
        + ts_iso.encode()
    )


def quest_claim_signing_payload(github_username: str,
                                quest_id: str,
                                date_str: str,
                                ts_iso: str) -> bytes:
    """Bytes signed when claiming a quest reward.

    Layout:
        b"daimon-quest-claim-v1\\n"
        + github_username.encode()
        + b"\\n"
        + quest_id.encode()
        + b"\\n"
        + date_str.encode()
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_QUEST_CLAIM.encode() + b"\n"
        + github_username.encode() + b"\n"
        + quest_id.encode() + b"\n"
        + date_str.encode() + b"\n"
        + ts_iso.encode()
    )


def tier_claim_signing_payload(github_username: str,
                               tier: str,
                               ts_iso: str) -> bytes:
    """Bytes signed when claiming a tier-up reward.

    Layout:
        b"daimon-tier-claim-v1\\n"
        + github_username.encode()
        + b"\\n"
        + tier.encode()
        + b"\\n"
        + ts_iso.encode()
    """
    return (
        PROTOCOL_VERSION_TIER_CLAIM.encode() + b"\n"
        + github_username.encode() + b"\n"
        + tier.encode() + b"\n"
        + ts_iso.encode()
    )


# ---------------------------------------------------------------------------
# Issue body format / parse
#
# The arbiter parses bodies as `key: value` lines plus optional fenced
# JSON blocks. We mirror its regexes here so a body we generate is
# guaranteed to round-trip cleanly.
# ---------------------------------------------------------------------------

# Match `key: value` lines (case-insensitive keys).
# Same regex as arbitrate.py::_KV — DO NOT diverge.
_KV = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+?)\s*$", re.MULTILINE)

# Match a fenced JSON code block. Same as arbitrate.py::_JSON_BLOCK.
_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def format_kv_body(kv_pairs: List[tuple],
                   json_block: Optional[Any] = None) -> str:
    """Format a body for an Issue or comment.

    Args:
      kv_pairs: ordered list of ``(key, value)`` tuples. Values are
                stringified; the order is preserved in the output (matters
                for human-readable diffs but NOT for the parser, which
                builds a dict).
      json_block: optional object to embed as a fenced ```json block. Goes
                  AFTER all kv pairs.

    Output is markdown-safe text the arbiter can parse end-to-end.
    """
    lines = [f"{k}: {v}" for k, v in kv_pairs]
    out = "\n".join(lines)
    if json_block is not None:
        # Use indent=2 for human readability; the arbiter doesn't care
        # about whitespace inside the fence (json.loads is whitespace-
        # tolerant), but signatures use `canonical_json`, NOT this rendered
        # form — so this is purely a presentation choice.
        block = json.dumps(json_block, indent=2, sort_keys=True)
        out += "\n\n```json\n" + block + "\n```"
    return out


def parse_kv_body(text: str) -> Dict[str, str]:
    """Extract `key: value` pairs from a body. Last value wins on duplicate.

    Mirror of arbitrate.py::parse_kv. Used by ops.py to read back arbiter
    response comments.
    """
    out: Dict[str, str] = {}
    for m in _KV.finditer(text):
        out[m.group(1).lower()] = m.group(2)
    return out


def extract_json_block(text: str) -> Optional[Any]:
    """Find the first ```json ... ``` block and parse it.

    Mirror of arbitrate.py::extract_json_block.
    """
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
