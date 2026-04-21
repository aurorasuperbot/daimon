"""NULLPOINT MCP server.

Exposes engine + identity + collection as `np_*` MCP tools so AI agents can
play autonomously. The server is **read-only with respect to the engine** —
all engine entry points are pure functions, the server just adapts arguments
and serializes results.

V1 scope (this file):
  np_whoami            → public identity (pubkey + handle if bound)
  np_match             → resolve a deterministic match between two loadouts
  np_loadout_validate  → check a loadout JSON without playing
  np_collection        → list owned cards (reads ~/.config/nullpoint/collection.json)
  np_pull              → STUB; returns "not_yet_implemented" with mining-status hint
  np_mine_status       → STUB; returns current balance from local ledger if present

Design rules:
  - Tools are NAMED `np_*` so card text containing tool calls can't masquerade
    as something else (engine never reads card text anyway, but defense-in-depth).
  - All tools return JSON-serializable dicts. No file paths in responses unless
    they're for the agent to consume next.
  - Stubs return `{"status": "not_yet_implemented", ...}` rather than raise — so
    agents can probe capabilities without try/except gymnastics.
  - The server NEVER signs anything on behalf of the agent without explicit
    user-side consent. Signing happens only in `np_match` for the seed/loadout
    commit (which is the whole point of an autobattler — the agent committed to
    these cards, the engine resolves it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from nullpoint import __version__
from nullpoint.cards import load_card_dict
from nullpoint.engine import Loadout, resolve_match
from nullpoint.engine.types import Card

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("nullpoint")

CONFIG_DIR = Path.home() / ".config" / "nullpoint"
COLLECTION_PATH = CONFIG_DIR / "collection.json"
LEDGER_PATH = CONFIG_DIR / "mining_ledger.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _loadout_from_payload(payload: Any, side_label: str) -> Loadout:
    """Accept either a dict {'cards': [...]} or a bare list of card dicts."""
    if isinstance(payload, dict) and "cards" in payload:
        cards_raw = payload["cards"]
    elif isinstance(payload, list):
        cards_raw = payload
    else:
        raise ValueError(
            f"{side_label}: expected object with 'cards' key or array of card "
            f"objects, got {type(payload).__name__}"
        )
    if not isinstance(cards_raw, list):
        raise ValueError(f"{side_label}.cards must be a list")
    cards = tuple(load_card_dict(c) for c in cards_raw)
    return Loadout(cards=cards)


def _seed_from_arg(seed_hex: Optional[str]) -> bytes:
    if seed_hex is None or seed_hex == "":
        # Deterministic-by-default: use a zero seed so callers must opt into
        # randomness. This catches accidental nondeterminism in tests.
        return b"\x00" * 32
    raw = bytes.fromhex(seed_hex)
    if len(raw) != 32:
        raise ValueError(f"seed must be 32 bytes (64 hex chars), got {len(raw)}")
    return raw


def _card_to_jsonable(c: Card) -> Dict[str, Any]:
    return {
        "card_id": c.card_id,
        "slot": c.slot.name,
        "atk": c.atk,
        "def": c.defense,
        "hp": c.hp,
        "spd": c.spd,
        "triggers": [
            {
                "when": t.when.name,
                "op": t.op.name,
                "target": t.target.name,
                "value": t.value,
            }
            for t in c.triggers
        ],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def np_whoami() -> Dict[str, Any]:
    """Return the local NULLPOINT identity.

    Returns:
      {"pubkey_hex": "...", "handle": "..." or null, "version": "..."}

    Returns {"error": "no_identity"} if `np init` has never been run on this
    machine. Never raises.
    """
    try:
        from nullpoint.identity import load_identity
        identity = load_identity()
    except FileNotFoundError:
        return {"error": "no_identity", "hint": "Run `np init` first."}

    handle = None
    metadata_path = CONFIG_DIR / "identity.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text())
            handle = metadata.get("handle")
        except Exception:
            pass

    return {
        "pubkey_hex": identity.pubkey_hex,
        "handle": handle,
        "version": __version__,
    }


@mcp.tool()
def np_match(
    loadout_a: Any,
    loadout_b: Any,
    seed: Optional[str] = None,
    include_round_log: bool = False,
) -> Dict[str, Any]:
    """Resolve a deterministic match between two loadouts.

    Args:
      loadout_a: Side A loadout. Either {"cards": [...]} or bare card array.
      loadout_b: Side B loadout. Same shape as A.
      seed: 32-byte hex string (64 chars). Defaults to all-zeros for replay
            safety — agents should provide a real seed for non-test play.
      include_round_log: If True, include per-round action traces. Default
            False to keep responses small.

    Returns:
      {"winner": 0 | 1 | null, "reason": "...", "side_a_final_hp": int,
       "side_b_final_hp": int, "round_count": int, "seed": "<hex>",
       "rounds": [...]?}
    """
    try:
        a = _loadout_from_payload(loadout_a, "loadout_a")
        b = _loadout_from_payload(loadout_b, "loadout_b")
        seed_bytes = _seed_from_arg(seed)
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}

    result = resolve_match(a, b, seed_bytes)

    out: Dict[str, Any] = {
        "winner": result.winner,
        "reason": result.reason,
        "side_a_final_hp": result.side_a_final_hp,
        "side_b_final_hp": result.side_b_final_hp,
        "round_count": len(result.rounds),
        "seed": seed_bytes.hex(),
    }
    if include_round_log:
        out["rounds"] = [
            {
                "round_number": r.round_number,
                "side_a_hp_total": r.side_a_hp_total,
                "side_b_hp_total": r.side_b_hp_total,
                "actions": list(r.actions),
            }
            for r in result.rounds
        ]
    return out


@mcp.tool()
def np_loadout_validate(loadout: Any) -> Dict[str, Any]:
    """Validate a loadout without resolving a match.

    Useful for an agent building a deck to check structural validity before
    committing it. Returns {"valid": True, "cards": [...]} on success.
    Returns {"valid": False, "error": "...", "message": "..."} on failure.
    """
    try:
        lo = _loadout_from_payload(loadout, "loadout")
    except (ValueError, TypeError) as e:
        return {"valid": False, "error": "invalid_input", "message": str(e)}

    return {
        "valid": True,
        "cards": [_card_to_jsonable(c) for c in lo.cards],
    }


@mcp.tool()
def np_collection() -> Dict[str, Any]:
    """List cards owned by the local identity.

    Reads ~/.config/nullpoint/collection.json — a JSON document of shape:
      {"serials": [{"serial": "uuid", "card_id": "...", "pack": "..."}]}

    Returns {"serials": [...], "count": int} or {"error": "no_collection"} if
    the file doesn't exist (fresh install).
    """
    if not COLLECTION_PATH.exists():
        return {"error": "no_collection", "serials": [], "count": 0}
    try:
        data = json.loads(COLLECTION_PATH.read_text())
    except json.JSONDecodeError as e:
        return {"error": "corrupt_collection", "message": str(e)}

    serials = data.get("serials", [])
    if not isinstance(serials, list):
        return {"error": "corrupt_collection", "message": "serials is not a list"}
    return {"serials": serials, "count": len(serials)}


@mcp.tool()
def np_mine_status() -> Dict[str, Any]:
    """Return current mining balance and recent receipts.

    V1 alpha: STUB. Reads ~/.config/nullpoint/mining_ledger.json if present —
    the mining daemon (V1.1) will populate this. Until then, returns
    {"status": "not_yet_implemented", "balance": 0, "receipts": []}.
    """
    if not LEDGER_PATH.exists():
        return {
            "status": "not_yet_implemented",
            "balance": 0,
            "receipts": [],
            "hint": "Mining daemon ships in V1.1.",
        }
    try:
        ledger = json.loads(LEDGER_PATH.read_text())
        return {
            "status": "ok",
            "balance": int(ledger.get("balance", 0)),
            "receipts": ledger.get("receipts", [])[-10:],  # last 10
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def np_pull() -> Dict[str, Any]:
    """Spend 100 currency on a gacha card pull.

    V1 alpha: STUB. Pull RNG + cardpack OCI fetch + collection update is
    integrated in V1.1. Always returns
    {"status": "not_yet_implemented", "hint": "..."}.
    """
    return {
        "status": "not_yet_implemented",
        "hint": (
            "Gacha pull lands in V1.1 alongside mining daemon. Use "
            "np_mine_status to track progress."
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_stdio() -> None:
    """Run the MCP server over stdio (for direct agent integration)."""
    mcp.run()


if __name__ == "__main__":
    run_stdio()
