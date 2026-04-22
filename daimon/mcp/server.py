"""DAIMON MCP server.

Exposes engine + identity + collection as `dm_*` MCP tools so AI agents can
play autonomously. The server is **read-only with respect to the engine** —
all engine entry points are pure functions, the server just adapts arguments
and serializes results.

V1 scope (this file) — 26 tools total (21 locked 2026-04-21 + dm_init
follow-up shipped same day + 3 NPC roster tools shipped 2026-04-22 +
dm_mine_status deprecated alias kept for back-compat):

Identity + currency:
  dm_init              → bootstrap identity + BIP39 mnemonic (one-time)
  dm_whoami            → pubkey + handle + balance + totals (absorbed mine_status)
  dm_register          → STUB: open identity registration Issue in arena
  dm_mine_status       → DEPRECATED alias for dm_whoami; kept for back-compat

Catalog (pure-local):
  dm_expansions        → list installed catalogs (manifest metadata)
  dm_catalog_list      → list cards in a catalog
  dm_catalog_card      → full card definition by id
  dm_card_compare      → side-by-side stat diff between two cards

Collection + pulls:
  dm_collection        → list owned serials
  dm_pull              → spend currency + mint new card

Loadouts (pure-local saved-deck CRUD):
  dm_loadout_validate  → structural validity check
  dm_loadout_save      → persist a named loadout to ~/.config/daimon/loadouts
  dm_loadout_list      → list saved loadout names
  dm_loadout_load      → fetch a saved loadout by name

Match / PvP:
  dm_match             → resolve two arbitrary loadouts (writes V2 Match to state file)
  dm_npcs              → list the NPC tier roster (Rookie → Champion)
  dm_npc               → full record for one NPC, with resolved card payloads
  dm_match_npc         → resolve player loadout vs a named NPC opponent
  dm_pvp_challenge     → STUB: open PvP challenge Issue in arena
  dm_pvp_accept        → STUB: accept + reveal against a pending challenge
  dm_pvp_status        → STUB: poll arbiter result for a challenge
  dm_pvp_my_matches    → STUB: list my open + recent PvP matches

Arena state:
  dm_leaderboard       → STUB: read leaderboard.json from arena repo
  dm_my_rank           → STUB: my standing + record

Disputes + contributions:
  dm_dispute_open      → STUB: appeal a resolved match (costs 50 currency)
  dm_card_propose      → STUB: propose a new card definition

Design rules:
  - Tools are NAMED `dm_*` so card text containing tool calls can't masquerade
    as something else (engine never reads card text anyway, but defense-in-depth).
  - All tools return JSON-serializable dicts. No file paths in responses unless
    they're for the agent to consume next.
  - Stubs return `{"status": "not_yet_implemented", ...}` rather than raise — so
    agents can probe capabilities without try/except gymnastics.
  - The server NEVER signs anything on behalf of the agent without explicit
    user-side consent. Signing happens only in `dm_match` for the seed/loadout
    commit (which is the whole point of an autobattler — the agent committed to
    these cards, the engine resolves it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from daimon import __version__
from daimon.cards import (
    CardDisplayFields,
    extract_display_fields,
    load_card_dict,
)
from daimon.engine import Loadout, resolve_match
from daimon.engine.types import Card
from daimon.play.adapter import (
    CardDisplay,
    ParticipantInfo,
    match_result_to_match,
)
from daimon.play.state import new_id, write_state

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("daimon")

CONFIG_DIR = Path.home() / ".config" / "daimon"
COLLECTION_PATH = CONFIG_DIR / "collection.json"
# Note: ledger lives at mining_ledger.jsonl (one entry per line). The legacy
# .json path is still recognized as a "no ledger" sentinel by older callers.
LEDGER_PATH = CONFIG_DIR / "mining_ledger.jsonl"
LOADOUTS_DIR = CONFIG_DIR / "loadouts"

# Arena repo URL — used by the PvP / dispute / card_propose stubs when they
# document the issue shape agents should post. Overrideable via env so forks
# and test arenas can point elsewhere.
DEFAULT_ARENA_REPO = "aurorasuperbot/daimon-arena"
ARENA_REPO = os.environ.get("DAIMON_ARENA_REPO", DEFAULT_ARENA_REPO)

# Max length for a saved loadout name. Names are used as filenames so we
# validate them strictly — no path traversal, no weird whitespace.
LOADOUT_NAME_MAX = 48
_LOADOUT_NAME_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_cards_from_payload(payload: Any, side_label: str) -> list[Any]:
    """Pull the raw card-dict list out of a loadout payload.

    Accepts either `{"cards": [...]}` or a bare list. The raw dicts still
    contain render-only fields (name, rarity, art) — those get stripped by
    `load_card_dict` but need to survive long enough for display extraction.
    """
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
    return cards_raw


def _loadout_from_payload(payload: Any, side_label: str) -> Loadout:
    """Accept either a dict {'cards': [...]} or a bare list of card dicts."""
    cards_raw = _raw_cards_from_payload(payload, side_label)
    cards = tuple(load_card_dict(c) for c in cards_raw)
    return Loadout(cards=cards)


def _display_override_from_fields(df: CardDisplayFields) -> Optional[CardDisplay]:
    """Translate a `CardDisplayFields` (from the card JSON) into a
    `CardDisplay` override for the adapter. Returns None when every field
    is empty — the adapter's synthesized defaults are fine then."""
    if not any((df.name, df.short_name, df.rarity, df.art_path)):
        return None
    return CardDisplay(
        name=df.name,
        short_name=df.short_name,
        rarity=df.rarity,
        art_path=df.art_path,
    )


def _card_displays_from_raw(cards_raw: list[Any]) -> tuple[Optional[CardDisplay], ...]:
    """Per-position display overrides pulled from the raw loadout payload."""
    out: list[Optional[CardDisplay]] = []
    for c in cards_raw:
        if isinstance(c, dict):
            out.append(_display_override_from_fields(extract_display_fields(c)))
        else:
            out.append(None)
    return tuple(out)


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
        "species": c.species,
        "element": c.element.name,
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


def _validate_loadout_name(name: Any) -> str:
    """Guard against path traversal in saved-loadout names.

    Returns the validated name or raises ValueError. Names must be 1–48 chars
    of `[A-Za-z0-9_-]` — no slashes, no dots, no whitespace, no unicode.
    """
    if not isinstance(name, str):
        raise ValueError(f"name must be string, got {type(name).__name__}")
    if not (1 <= len(name) <= LOADOUT_NAME_MAX):
        raise ValueError(
            f"name must be 1–{LOADOUT_NAME_MAX} chars, got {len(name)}"
        )
    bad = [c for c in name if c not in _LOADOUT_NAME_ALLOWED]
    if bad:
        raise ValueError(
            f"name contains disallowed chars {bad!r}; "
            "use only [A-Za-z0-9_-]"
        )
    return name


def _loadout_path(name: str) -> Path:
    return LOADOUTS_DIR / f"{name}.json"


def _mining_stats_or_empty() -> Dict[str, Any]:
    """Return balance + totals + recent receipts. Empty dict-shape if no
    ledger yet (fresh install). Shared by dm_whoami and dm_mine_status."""
    from daimon.mining.ledger import (
        LEDGER_PATH as _LP,
        get_recent_entries,
        get_stats,
        verify_ledger,
    )
    if not _LP.exists():
        return {
            "balance": 0,
            "total_mined": 0,
            "total_pulled": 0,
            "mine_count": 0,
            "pull_count": 0,
            "ledger_entries": 0,
            "verified": True,
            "recent": [],
        }
    stats = get_stats()
    verification = verify_ledger()
    recent = [
        {k: v for k, v in e.items()
         if k in ("ts", "kind", "amount", "tool_name", "card_id",
                  "rarity", "pack")}
        for e in get_recent_entries(limit=10)
    ]
    out: Dict[str, Any] = {
        "balance": stats.balance,
        "total_mined": stats.total_mined,
        "total_pulled": stats.total_pulled,
        "mine_count": stats.mine_count,
        "pull_count": stats.pull_count,
        "ledger_entries": stats.entry_count,
        "verified": bool(verification.get("ok")),
        "recent": recent,
    }
    if not verification.get("ok"):
        out["verification_errors"] = verification.get("errors", [])[:5]
    return out


def _catalog_summary(catalog_id: str) -> Dict[str, Any]:
    """Load a catalog and return manifest metadata (no card payloads)."""
    from daimon.catalog import load_catalog
    cat = load_catalog(catalog_id)
    rarity_counts: Dict[str, int] = {}
    for c in cat.cards:
        rarity_counts[c.rarity] = rarity_counts.get(c.rarity, 0) + 1
    return {
        "pack_id": cat.pack_id,
        "version": cat.version,
        "description": cat.description,
        "rarity_weights": dict(cat.rarity_weights),
        "card_count": len(cat.cards),
        "rarity_counts": rarity_counts,
    }


def _stub_arena_response(tool_name: str,
                         issue_shape: Dict[str, Any],
                         hint: str) -> Dict[str, Any]:
    """Uniform envelope for arena-bound tools that aren't wired up yet.

    Returns the shape agents will get in V1.x once wiring lands; for now the
    `status: not_yet_implemented` flag tells the agent to skip gracefully.
    The `issue_shape` field documents the exact JSON an arena GitHub Issue
    should carry, so skills-doc examples stay accurate.
    """
    return {
        "status": "not_yet_implemented",
        "tool": tool_name,
        "arena_repo": ARENA_REPO,
        "issue_shape": issue_shape,
        "hint": hint,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_init(force: bool = False) -> Dict[str, Any]:
    """Bootstrap a fresh ed25519 identity + BIP39 recovery mnemonic.

    This is the first thing an MCP-only agent runs on a new machine — without
    it, every other tool returns `{"error": "no_identity"}`. The CLI
    equivalent is `daimon init`.

    Args:
      force: If True, overwrite an existing identity. DESTRUCTIVE — the old
             private key + collection ledger position become unrecoverable
             unless the mnemonic was saved. Default False.

    Returns on success:
      {"status": "ok", "pubkey_hex": "...", "mnemonic": "word1 word2 ...",
       "created": true, "identity_path": "...", "warning": "..."}

    The `mnemonic` field is the 24-word BIP39 recovery phrase. It is
    returned EXACTLY ONCE — never persisted to disk, never visible via any
    other tool. The caller is responsible for surfacing it to the user in
    a way the user can save (print to terminal, prompt to copy, etc).

    Returns on failure:
      {"error": "identity_exists", "message": "...", "pubkey_hex": "...",
       "hint": "Pass force=true to overwrite (DESTRUCTIVE)."}
      {"error": "internal_error", "message": "..."}
    """
    from daimon.identity import generate_identity, load_identity
    from daimon.identity.keys import PRIVATE_KEY_PATH

    # Check existence BEFORE calling generate_identity so we can return a
    # structured error envelope instead of letting FileExistsError bubble.
    if PRIVATE_KEY_PATH.exists() and not force:
        try:
            existing = load_identity()
            existing_pub = existing.pubkey_hex
        except Exception:
            existing_pub = None
        return {
            "error": "identity_exists",
            "message": (
                f"identity already present at {PRIVATE_KEY_PATH}"
            ),
            "pubkey_hex": existing_pub,
            "hint": "Pass force=true to overwrite (DESTRUCTIVE — "
                    "old collection + ledger position will be lost "
                    "unless you have the mnemonic).",
        }

    try:
        identity = generate_identity(force=force)
    except Exception as e:  # noqa: BLE001 — structured-error contract
        return {
            "error": "internal_error",
            "message": f"{type(e).__name__}: {e}",
        }

    return {
        "status": "ok",
        "pubkey_hex": identity.pubkey_hex,
        "mnemonic": identity.mnemonic or "",
        "created": True,
        "identity_path": str(PRIVATE_KEY_PATH),
        "warning": (
            "Save the mnemonic NOW. It is shown once only — DAIMON "
            "never persists it. Loss of both mnemonic and identity.key "
            "means loss of your collection."
        ),
    }


@mcp.tool()
def dm_whoami() -> Dict[str, Any]:
    """Return the local DAIMON identity + mining snapshot.

    Per the locked 2026-04-21 design, `dm_whoami` absorbs what used to be
    `dm_mine_status` — balance, totals, and recent receipts live here now.
    The old tool is kept as a deprecated alias for back-compat.

    Returns:
      {"pubkey_hex": "...", "handle": "..." or null, "version": "...",
       "balance": int, "total_mined": int, "total_pulled": int,
       "mine_count": int, "pull_count": int, "ledger_entries": int,
       "verified": bool, "recent": [...], "registered": bool}

    Returns {"error": "no_identity"} if `daimon init` has never been run on this
    machine. Never raises.
    """
    try:
        from daimon.identity import load_identity
        identity = load_identity()
    except FileNotFoundError:
        return {"error": "no_identity",
                "hint": "Call `dm_init` (MCP) or run `daimon init` (CLI) "
                        "to bootstrap an identity."}

    handle = None
    registered = False
    metadata_path = CONFIG_DIR / "identity.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text())
            handle = metadata.get("handle")
            registered = bool(metadata.get("registered"))
        except Exception:
            pass

    out: Dict[str, Any] = {
        "pubkey_hex": identity.pubkey_hex,
        "handle": handle,
        "version": __version__,
        "registered": registered,
    }
    out.update(_mining_stats_or_empty())
    return out


@mcp.tool()
def dm_match(
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
        a_raw = _raw_cards_from_payload(loadout_a, "loadout_a")
        b_raw = _raw_cards_from_payload(loadout_b, "loadout_b")
        a = Loadout(cards=tuple(load_card_dict(c) for c in a_raw))
        b = Loadout(cards=tuple(load_card_dict(c) for c in b_raw))
        seed_bytes = _seed_from_arg(seed)
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}

    result = resolve_match(a, b, seed_bytes)

    state_id = new_id("match")

    # Build the V2 Match payload via the engine→schema adapter. We pull
    # real display metadata (name, rarity, short_name, art_path) from the
    # raw loadout payload — the engine drops those fields, but the renderer
    # needs them. When a card has no display fields (synthetic test
    # loadouts), the adapter synthesizes defaults from species.
    match_payload = match_result_to_match(
        result, a, b,
        match_id=state_id,
        player=ParticipantInfo(
            name="player", rank="",
            card_displays=_card_displays_from_raw(a_raw),
        ),
        opponent=ParticipantInfo(
            name="opponent", rank="",
            card_displays=_card_displays_from_raw(b_raw),
        ),
    )
    state_payload: Dict[str, Any] = json.loads(match_payload.model_dump_json())

    # Build the legacy round log for the agent-facing response (when
    # include_round_log=True). The state file no longer carries this — it
    # carries the V2 Match payload above — but agents that requested the
    # round-by-round trace expect the human-readable string log.
    full_rounds = [
        {
            "round_number": r.round_number,
            "side_a_hp_total": r.side_a_hp_total,
            "side_b_hp_total": r.side_b_hp_total,
            "actions": list(r.actions),
        }
        for r in result.rounds
    ]

    # Side effect: publish to the single state file so the game terminal
    # (if running) picks it up and animates. Never let a state-write failure
    # bubble up — the match result itself is what the agent needs.
    try:
        write_state("match", state_payload, id=state_id)
    except Exception:  # noqa: BLE001 — state-write is best-effort
        pass

    out: Dict[str, Any] = {
        "winner": result.winner,
        "reason": result.reason,
        "side_a_final_hp": result.side_a_final_hp,
        "side_b_final_hp": result.side_b_final_hp,
        "round_count": len(result.rounds),
        "seed": seed_bytes.hex(),
        "state_id": state_id,
    }
    if include_round_log:
        out["rounds"] = full_rounds
    return out


# ---------------------------------------------------------------------------
# NPC tier roster (V1 alpha)
# ---------------------------------------------------------------------------

def _npc_summary(npc, *, include_loadout_ids: bool = False) -> Dict[str, Any]:
    """Render an NPC row for the list endpoints (no card payloads)."""
    out: Dict[str, Any] = {
        "npc_id": npc.npc_id,
        "name": npc.name,
        "tier": npc.tier,
        "rank": npc.rank,
        "flavor": npc.flavor,
    }
    if include_loadout_ids:
        out["loadout"] = list(npc.loadout)
    return out


@mcp.tool()
def dm_npcs(tier: Optional[str] = None) -> Dict[str, Any]:
    """List the NPC tier roster.

    Args:
      tier: Optional tier filter. One of `rookie`, `novice`, `veteran`,
            `elite`, `champion`. Omit to list every NPC.

    Returns:
      {"tiers": [{"tier_id": "rookie", "rank": 1, "label": "Rookie",
                  "rule": "...", "npc_ids": [...]}, ...],
       "npcs": [{"npc_id": "...", "name": "...", "tier": "...",
                 "rank": int, "flavor": "..."}, ...],
       "count": int}

    Card payloads are NOT included -- call dm_npc(npc_id) for the full deck.

    Returns {"error": "unknown_tier", ...} if the tier filter doesn't match
    any tier in the roster.
    """
    from daimon.npcs import get_roster, list_npcs as _list_npcs

    roster = get_roster()
    tier_meta = [
        {
            "tier_id": t.tier_id,
            "rank": t.rank,
            "label": t.label,
            "rule": t.rule,
            "npc_ids": list(t.npc_ids),
        }
        for t in sorted(roster.tiers, key=lambda x: x.rank)
    ]

    try:
        npcs = _list_npcs(tier)
    except ValueError as e:
        return {
            "error": "unknown_tier",
            "message": str(e),
            "available_tiers": [t.tier_id for t in roster.tiers],
        }

    return {
        "tiers": tier_meta,
        "npcs": [_npc_summary(n) for n in npcs],
        "count": len(npcs),
        "filter": {"tier": tier} if tier else {},
    }


@mcp.tool()
def dm_npc(npc_id: str) -> Dict[str, Any]:
    """Full record for one NPC, including resolved loadout card payloads.

    Args:
      npc_id: NPC slug (e.g. `sparring_sam`, `doom_paw_doppia`).

    Returns:
      {"npc_id": "...", "name": "...", "tier": "...", "rank": int,
       "flavor": "...", "bio": "...",
       "loadout": [card_id, ...],
       "cards": [{full V2 card dict with display fields}, ...]}

    Use this to inspect an NPC's deck before fighting it; pass the `cards`
    list straight to dm_match (or dm_loadout_validate) to mirror their team.

    Returns {"error": "unknown_npc", ...} if no such NPC, or
    {"error": "internal_error", ...} if a loadout card is missing from the
    catalog (would indicate a packaging bug).
    """
    from daimon.npcs import get_npc as _get_npc, npc_card_dicts

    if not isinstance(npc_id, str) or not npc_id:
        return {"error": "invalid_input",
                "message": "npc_id must be a non-empty string"}

    try:
        npc = _get_npc(npc_id)
    except KeyError as e:
        return {"error": "unknown_npc", "message": str(e), "npc_id": npc_id}

    try:
        cards = npc_card_dicts(npc)
    except Exception as e:  # noqa: BLE001 — structured envelope
        return {
            "error": "internal_error",
            "message": f"failed to resolve loadout for {npc_id!r}: "
                       f"{type(e).__name__}: {e}",
            "npc_id": npc_id,
        }

    out = _npc_summary(npc, include_loadout_ids=True)
    out["bio"] = npc.bio
    out["cards"] = cards
    return out


@mcp.tool()
def dm_match_npc(
    loadout: Any,
    npc_id: str,
    seed: Optional[str] = None,
    include_round_log: bool = False,
) -> Dict[str, Any]:
    """Play your loadout against a named NPC opponent.

    Side A is your loadout; Side B is the NPC's fixed loadout (resolved from
    the bundled v1_alpha catalog). State file gets a normal V2 Match payload
    so the play HUD renders the fight; the response carries an extra
    `npc` block with the opponent's identity.

    Args:
      loadout: Your team. Either {"cards": [...]} or a bare card array.
      npc_id: NPC slug. Call dm_npcs() first to enumerate.
      seed: Optional 32-byte hex (64 chars). Defaults to all-zeros for
            replay-safe play.
      include_round_log: If True, include per-round action traces.

    Returns:
      {"status": "ok",
       "winner": 0 | 1 | null, "reason": "...", "round_count": int,
       "side_a_final_hp": int, "side_b_final_hp": int, "seed": "<hex>",
       "state_id": "match_...",
       "npc": {"npc_id": "...", "name": "...", "tier": "...",
               "rank": int, "flavor": "..."},
       "rounds": [...]?}

    Failure envelopes:
      {"error": "invalid_input", "message": "..."}     loadout/seed bad
      {"error": "unknown_npc", "message": "..."}       npc_id not in roster
      {"error": "internal_error", "message": "..."}    catalog/loadout resolve failed
    """
    from daimon.catalog import load_catalog
    from daimon.npcs import get_npc as _get_npc
    from daimon.npcs.loader import _resolve_loadout_cards

    if not isinstance(npc_id, str) or not npc_id:
        return {"error": "invalid_input",
                "message": "npc_id must be a non-empty string"}

    # Resolve NPC first so the player sees a clean error if the slug is bad,
    # before they spend cycles validating their own loadout payload.
    try:
        npc = _get_npc(npc_id)
    except KeyError as e:
        return {"error": "unknown_npc", "message": str(e), "npc_id": npc_id}

    # Resolve NPC loadout (raw card dicts -> engine Loadout). Catalog load
    # could fail if package data is missing; surface that as internal_error.
    try:
        catalog = load_catalog()
        npc_raw = _resolve_loadout_cards(npc, catalog)
        npc_lo = Loadout(cards=tuple(load_card_dict(c) for c in npc_raw))
    except Exception as e:  # noqa: BLE001
        return {
            "error": "internal_error",
            "message": f"failed to resolve NPC loadout: {type(e).__name__}: {e}",
            "npc_id": npc_id,
        }

    # Validate player loadout + seed.
    try:
        a_raw = _raw_cards_from_payload(loadout, "loadout")
        a_lo = Loadout(cards=tuple(load_card_dict(c) for c in a_raw))
        seed_bytes = _seed_from_arg(seed)
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}

    result = resolve_match(a_lo, npc_lo, seed_bytes)
    state_id = new_id("match")

    match_payload = match_result_to_match(
        result, a_lo, npc_lo,
        match_id=state_id,
        player=ParticipantInfo(
            name="player", rank="",
            card_displays=_card_displays_from_raw(a_raw),
        ),
        opponent=ParticipantInfo(
            name=npc.name, rank=npc.tier,
            card_displays=_card_displays_from_raw(npc_raw),
        ),
    )
    state_payload: Dict[str, Any] = json.loads(match_payload.model_dump_json())

    full_rounds = [
        {
            "round_number": r.round_number,
            "side_a_hp_total": r.side_a_hp_total,
            "side_b_hp_total": r.side_b_hp_total,
            "actions": list(r.actions),
        }
        for r in result.rounds
    ]

    try:
        write_state("match", state_payload, id=state_id)
    except Exception:  # noqa: BLE001 — best-effort
        pass

    out: Dict[str, Any] = {
        "status": "ok",
        "winner": result.winner,
        "reason": result.reason,
        "side_a_final_hp": result.side_a_final_hp,
        "side_b_final_hp": result.side_b_final_hp,
        "round_count": len(result.rounds),
        "seed": seed_bytes.hex(),
        "state_id": state_id,
        "npc": {
            "npc_id": npc.npc_id,
            "name": npc.name,
            "tier": npc.tier,
            "rank": npc.rank,
            "flavor": npc.flavor,
        },
    }
    if include_round_log:
        out["rounds"] = full_rounds
    return out


@mcp.tool()
def dm_loadout_validate(loadout: Any) -> Dict[str, Any]:
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
def dm_collection() -> Dict[str, Any]:
    """List cards owned by the local identity.

    Reads ~/.config/daimon/collection.json — a JSON document of shape:
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
def dm_mine_status() -> Dict[str, Any]:
    """DEPRECATED — use `dm_whoami` instead. Kept as a thin alias for
    back-compat while callers migrate.

    Returns the same ledger view `dm_whoami` now exposes, under a
    `status: "ok"` envelope for the legacy shape, plus a `deprecation`
    field so agents log a warning.
    """
    data = _mining_stats_or_empty()
    hint = None
    if data.get("ledger_entries", 0) == 0:
        hint = (
            "No ledger yet. Install the Claude Code hook with "
            "`daimon mine install-hook` so productive work is recorded."
        )
    out: Dict[str, Any] = {"status": "ok", **data}
    if hint:
        out["hint"] = hint
    out["deprecation"] = (
        "dm_mine_status is deprecated; balance and recent receipts are now "
        "part of dm_whoami's response."
    )
    return out


@mcp.tool()
def dm_pull(seed: Optional[str] = None,
            catalog: Optional[str] = None) -> Dict[str, Any]:
    """Spend 100 currency on a gacha card pull from the bundled catalog.

    Args:
      seed: Optional 32-byte hex seed (64 chars). Default = random.
            Same seed → same card_id outcome. The minted serial UUID is
            always fresh.
      catalog: Optional catalog id (default "v1_alpha"). The pack ships in
            the engine wheel — additional packs land via OCI in V1.5.

    Returns on success:
      {"status": "ok", "serial": "uuid", "card_id": "...", "rarity": "...",
       "pack": "v1_alpha", "balance_after": int, "ledger_entry_hash": "...",
       "seed_hex": "...", "payload": {full card JSON}}

    Returns on failure (never raises) — normalized to the `error:` envelope
    (aligned with every other tool; 2026-04-21 fix):
      {"error": "no_identity", "hint": "..."}
      {"error": "insufficient_balance", "balance": int, "needed": int, "cost": int}
      {"error": "ledger_corrupt", "message": "..."}
      {"error": "invalid_input", "message": "..."}
      {"error": "internal_error", "message": "..."}
    """
    from daimon.catalog import DEFAULT_CATALOG_ID
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.pulls import perform_pull

    seed_bytes: Optional[bytes] = None
    if seed:
        try:
            seed_bytes = bytes.fromhex(seed)
            if len(seed_bytes) != 32:
                return {"error": "invalid_input",
                        "message": f"seed must be 32 bytes, got {len(seed_bytes)}"}
        except ValueError as e:
            return {"error": "invalid_input", "message": f"seed not hex: {e}"}

    try:
        receipt = perform_pull(
            catalog_name=catalog or DEFAULT_CATALOG_ID,
            seed=seed_bytes,
        )
    except FileNotFoundError:
        return {"error": "no_identity",
                "hint": "Run `dm_init` (MCP) or `daimon init` (CLI) first."}
    except InsufficientBalanceError as e:
        from daimon.pulls import can_pull
        cp = can_pull()
        return {
            "error": "insufficient_balance",
            "message": str(e),
            "balance": cp["balance"],
            "needed": cp["needed"],
            "cost": cp["cost"],
        }
    except RuntimeError as e:
        msg = str(e)
        if "ledger verification failed" in msg:
            return {"error": "ledger_corrupt", "message": msg}
        return {"error": "internal_error", "message": msg}
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    receipt_dict = receipt.to_dict()

    # Side effect: publish to the single state file so the game terminal
    # (if running) picks up this pull and plays the reveal animation. A
    # state-write failure must never block the pull itself — the mint is
    # already committed to the ledger.
    state_id = new_id("pull")
    try:
        write_state("pull", dict(receipt_dict), id=state_id)
    except Exception:  # noqa: BLE001 — state-write is best-effort
        pass

    return {"status": "ok", "state_id": state_id, **receipt_dict}


# ---------------------------------------------------------------------------
# Catalog tools (pure-local)
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_expansions() -> Dict[str, Any]:
    """List all installed card catalogs.

    Returns one entry per catalog directory bundled with the engine:
      {"expansions": [{"pack_id": "...", "version": "...",
                       "description": "...", "card_count": int,
                       "rarity_weights": {...},
                       "rarity_counts": {"common": int, ...}}]}

    Every catalog listed here is a valid target for `dm_pull(catalog=...)`
    and `dm_catalog_list(expansion_id=...)`.
    """
    from daimon.catalog import list_catalogs
    try:
        ids = list_catalogs()
    except Exception as e:  # noqa: BLE001 — surface loader failures cleanly
        return {"error": "catalog_load_failed", "message": str(e)}

    out: List[Dict[str, Any]] = []
    for cid in ids:
        try:
            out.append(_catalog_summary(cid))
        except Exception as e:  # noqa: BLE001
            out.append({
                "pack_id": cid,
                "error": "manifest_invalid",
                "message": str(e),
            })
    return {"expansions": out, "count": len(out)}


@mcp.tool()
def dm_catalog_list(expansion_id: Optional[str] = None) -> Dict[str, Any]:
    """List cards in a catalog.

    Args:
      expansion_id: Catalog id (e.g. "v1_alpha"). Defaults to the engine's
        `DEFAULT_CATALOG_ID` (currently "v1_alpha").

    Returns:
      {"pack_id": "...", "cards": [
          {"card_id": "...", "species": "...", "element": "...",
           "rarity": "...", "atk": int, "def": int, "hp": int, "spd": int,
           "trigger_count": int}
      ], "count": int}

    Card triggers are summarized by count — call `dm_catalog_card` for the
    full definition (triggers + display fields).
    """
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    cid = expansion_id or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        return {"error": "unknown_expansion", "expansion_id": cid}
    except Exception as e:  # noqa: BLE001
        return {"error": "catalog_load_failed", "message": str(e)}

    entries: List[Dict[str, Any]] = []
    for cc in cat.cards:
        p = cc.payload
        entries.append({
            "card_id": cc.card_id,
            "species": p.get("species", cc.card_id),
            "element": p.get("element", "UNKNOWN"),
            "rarity": cc.rarity,
            "atk": p.get("atk", 0),
            "def": p.get("def", 0),
            "hp": p.get("hp", 0),
            "spd": p.get("spd", 0),
            "trigger_count": len(p.get("triggers", []) or []),
        })
    return {
        "pack_id": cat.pack_id,
        "version": cat.version,
        "count": len(entries),
        "cards": entries,
    }


@mcp.tool()
def dm_catalog_card(card_id: str,
                    expansion_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the full card definition for a catalog card.

    Args:
      card_id: card identifier (e.g. "voltcat_apex").
      expansion_id: optional catalog id; defaults to DEFAULT_CATALOG_ID.

    Returns:
      {"card_id": "...", "pack": "...", "rarity": "...",
       "payload": {full card JSON, including name / flavor / art / triggers}}

    Returns {"error": "unknown_card"} when the id is not present, or
    {"error": "unknown_expansion"} when the catalog id is bogus.
    """
    if not isinstance(card_id, str) or not card_id:
        return {"error": "invalid_input", "message": "card_id must be non-empty string"}

    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    cid = expansion_id or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        return {"error": "unknown_expansion", "expansion_id": cid}

    cc = cat.by_id.get(card_id)
    if cc is None:
        return {"error": "unknown_card", "card_id": card_id,
                "expansion_id": cid}
    return {
        "card_id": cc.card_id,
        "pack": cc.pack,
        "rarity": cc.rarity,
        "payload": cc.payload,
    }


@mcp.tool()
def dm_card_compare(a: str, b: str,
                    expansion_id: Optional[str] = None) -> Dict[str, Any]:
    """Compare two catalog cards side-by-side.

    Args:
      a, b: card_ids to compare (both must live in the same catalog).
      expansion_id: optional catalog id; defaults to DEFAULT_CATALOG_ID.

    Returns:
      {"a": {...summary...}, "b": {...summary...},
       "diff": {"atk": {"a": int, "b": int, "delta": int},
                "def": ..., "hp": ..., "spd": ...,
                "element": {"a": "...", "b": "...", "same": bool},
                "rarity": {"a": "...", "b": "...", "same": bool}},
       "trigger_diff": {"a_only": [...], "b_only": [...],
                        "shared": [...]}}
    """
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    cid = expansion_id or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        return {"error": "unknown_expansion", "expansion_id": cid}

    cc_a = cat.by_id.get(a)
    cc_b = cat.by_id.get(b)
    if cc_a is None or cc_b is None:
        missing = [x for x, cc in ((a, cc_a), (b, cc_b)) if cc is None]
        return {"error": "unknown_card", "missing": missing,
                "expansion_id": cid}

    pa = cc_a.payload
    pb = cc_b.payload

    def summary(cc, p):
        return {
            "card_id": cc.card_id,
            "rarity": cc.rarity,
            "element": p.get("element"),
            "atk": p.get("atk", 0),
            "def": p.get("def", 0),
            "hp": p.get("hp", 0),
            "spd": p.get("spd", 0),
            "name": p.get("name"),
        }

    def stat_delta(key):
        av = pa.get(key, 0)
        bv = pb.get(key, 0)
        return {"a": av, "b": bv, "delta": bv - av}

    diff = {
        "atk": stat_delta("atk"),
        "def": stat_delta("def"),
        "hp": stat_delta("hp"),
        "spd": stat_delta("spd"),
        "element": {
            "a": pa.get("element"),
            "b": pb.get("element"),
            "same": pa.get("element") == pb.get("element"),
        },
        "rarity": {
            "a": cc_a.rarity,
            "b": cc_b.rarity,
            "same": cc_a.rarity == cc_b.rarity,
        },
    }

    # Trigger signatures for comparison — (when, op, target, value).
    def tsigs(p):
        triggers = p.get("triggers", []) or []
        return [
            (t.get("when"), t.get("op"), t.get("target"), t.get("value"))
            for t in triggers if isinstance(t, dict)
        ]

    sa = tsigs(pa)
    sb = tsigs(pb)
    shared = [t for t in sa if t in sb]
    a_only = [t for t in sa if t not in sb]
    b_only = [t for t in sb if t not in sa]

    def trigger_render(tup):
        when, op, tgt, val = tup
        return {"when": when, "op": op, "target": tgt, "value": val}

    return {
        "a": summary(cc_a, pa),
        "b": summary(cc_b, pb),
        "diff": diff,
        "trigger_diff": {
            "a_only": [trigger_render(t) for t in a_only],
            "b_only": [trigger_render(t) for t in b_only],
            "shared": [trigger_render(t) for t in shared],
        },
    }


# ---------------------------------------------------------------------------
# Loadout CRUD (pure-local)
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_loadout_save(loadout: Any, name: str) -> Dict[str, Any]:
    """Persist a validated loadout to ~/.config/daimon/loadouts/<name>.json.

    The loadout is run through the engine's strict loader before being saved
    — a name-collision replaces the old file; a validation failure refuses
    to write anything.

    Args:
      loadout: `{"cards": [...]}` or bare array of card dicts.
      name: 1–48 chars of `[A-Za-z0-9_-]`. Used as the filename.

    Returns:
      {"status": "ok", "name": "...", "path": "...", "card_count": 6,
       "overwrote": bool}
    """
    try:
        safe_name = _validate_loadout_name(name)
    except ValueError as e:
        return {"error": "invalid_name", "message": str(e)}

    try:
        raw = _raw_cards_from_payload(loadout, "loadout")
        # Validate via the strict engine loader — catches bad schemas early.
        for c in raw:
            load_card_dict(c)
    except (ValueError, TypeError) as e:
        return {"error": "invalid_loadout", "message": str(e)}

    LOADOUTS_DIR.mkdir(parents=True, exist_ok=True)
    target = _loadout_path(safe_name)
    overwrote = target.exists()
    # Persist the raw card dicts — round-trip works with display metadata
    # intact. A saved loadout is conceptually a named deck, not an engine
    # artifact.
    doc = {"name": safe_name, "cards": list(raw)}
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "name": safe_name,
        "path": str(target),
        "card_count": len(raw),
        "overwrote": overwrote,
    }


@mcp.tool()
def dm_loadout_list() -> Dict[str, Any]:
    """List saved loadout names on the local machine.

    Returns:
      {"loadouts": [{"name": "...", "card_count": int,
                     "path": "...", "mtime": float}], "count": int}

    Malformed JSON files are skipped with a `corrupt` marker in their entry.
    """
    if not LOADOUTS_DIR.exists():
        return {"loadouts": [], "count": 0}

    out: List[Dict[str, Any]] = []
    for entry in sorted(LOADOUTS_DIR.iterdir()):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        name = entry.stem
        try:
            doc = json.loads(entry.read_text(encoding="utf-8"))
            cards = doc.get("cards", [])
            if not isinstance(cards, list):
                raise ValueError("cards not a list")
            out.append({
                "name": name,
                "card_count": len(cards),
                "path": str(entry),
                "mtime": entry.stat().st_mtime,
            })
        except Exception as e:  # noqa: BLE001
            out.append({
                "name": name,
                "corrupt": True,
                "message": str(e),
                "path": str(entry),
            })
    return {"loadouts": out, "count": len(out)}


@mcp.tool()
def dm_loadout_load(name: str) -> Dict[str, Any]:
    """Fetch a saved loadout by name.

    Args:
      name: the same name used with `dm_loadout_save`.

    Returns:
      {"status": "ok", "name": "...", "cards": [...]}

    Returns {"error": "unknown_loadout"} if no file matches.
    """
    try:
        safe_name = _validate_loadout_name(name)
    except ValueError as e:
        return {"error": "invalid_name", "message": str(e)}

    target = _loadout_path(safe_name)
    if not target.is_file():
        return {"error": "unknown_loadout", "name": safe_name}

    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"error": "corrupt_loadout", "message": str(e)}

    cards = doc.get("cards", [])
    if not isinstance(cards, list):
        return {"error": "corrupt_loadout", "message": "cards not a list"}
    return {"status": "ok", "name": safe_name, "cards": cards}


# ---------------------------------------------------------------------------
# Arena-bound stubs
#
# These tools need the `daimon-arena` GitHub repo + arbiter workflow to be
# live to do their real work. Until then they return a documented
# `not_yet_implemented` envelope whose `issue_shape` shows the exact payload
# an agent will post once wiring lands. Agents + skills docs can code against
# the shape today.
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_register(handle: Optional[str] = None) -> Dict[str, Any]:
    """Register this local identity with the arena.

    Opens an identity Issue in `DAIMON_ARENA_REPO` carrying a signed
    assertion `{"pubkey_hex": "...", "handle": "...", "signed_at": "..."}`
    so the arbiter can bind pubkey → GitHub account + display handle.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    # Surface the caller's pubkey in the docstring shape so agents can see
    # what we *would* post. Safe — pubkeys are public.
    try:
        from daimon.identity import load_identity
        identity = load_identity()
        pubkey_hex = identity.pubkey_hex
    except FileNotFoundError:
        return {"error": "no_identity", "hint": "Run `daimon init` first."}

    return _stub_arena_response(
        "dm_register",
        issue_shape={
            "title": f"register: {pubkey_hex[:16]}…",
            "body": {
                "pubkey_hex": pubkey_hex,
                "handle": handle or "(auto-derived from github)",
                "signed_at": "ISO-8601 UTC",
                "signature": "ed25519(pubkey || handle || ts) hex",
            },
            "labels": ["identity", "pending-arbiter"],
        },
        hint=(
            "arena wiring lands with V1 launch; until then this is local-only"
        ),
    )


@mcp.tool()
def dm_pvp_challenge(opponent_pubkey: str,
                     loadout: Any,
                     memo: Optional[str] = None) -> Dict[str, Any]:
    """Open an async PvP challenge against another registered player.

    Commits the challenger's loadout (commit half of commit-reveal) to the
    arena queue. The opponent reveals their loadout via `dm_pvp_accept`;
    arbiter then runs `resolve_match` and writes the result.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(opponent_pubkey, str) or len(opponent_pubkey) != 64:
        return {"error": "invalid_input",
                "message": "opponent_pubkey must be 64-char hex"}
    try:
        _raw_cards_from_payload(loadout, "loadout")
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}

    return _stub_arena_response(
        "dm_pvp_challenge",
        issue_shape={
            "title": "pvp-challenge",
            "body": {
                "challenger_pubkey": "hex(local)",
                "opponent_pubkey": opponent_pubkey,
                "loadout_commit": "sha256(loadout_json || salt) hex",
                "challenged_at": "ISO-8601 UTC",
                "memo": memo,
                "signature": "ed25519(title || body) hex",
            },
            "labels": ["pvp", "pending-accept"],
        },
        hint="reveal via dm_pvp_accept once opponent accepts",
    )


@mcp.tool()
def dm_pvp_accept(challenge_id: str, loadout: Any) -> Dict[str, Any]:
    """Accept a pending PvP challenge + reveal responder's loadout.

    Posts the reveal payload (responder loadout + salt + signature) as a
    comment on the challenge Issue. Arbiter picks it up + resolves.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(challenge_id, str) or not challenge_id:
        return {"error": "invalid_input",
                "message": "challenge_id must be non-empty string"}
    try:
        _raw_cards_from_payload(loadout, "loadout")
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}

    return _stub_arena_response(
        "dm_pvp_accept",
        issue_shape={
            "target_issue": challenge_id,
            "comment_body": {
                "responder_pubkey": "hex(local)",
                "responder_loadout": "full loadout JSON",
                "salt": "hex(32 bytes)",
                "accepted_at": "ISO-8601 UTC",
                "signature": "ed25519(...) hex",
            },
            "labels_to_add": ["pending-arbiter"],
        },
        hint="arbiter writes result on the same Issue after ~1 min",
    )


@mcp.tool()
def dm_pvp_status(challenge_id: str) -> Dict[str, Any]:
    """Poll the arbiter result for a PvP challenge.

    Returns the current phase (`pending-accept` / `pending-arbiter` /
    `resolved` / `disputed`) and, when resolved, the match_id + winner.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(challenge_id, str) or not challenge_id:
        return {"error": "invalid_input",
                "message": "challenge_id must be non-empty string"}

    return _stub_arena_response(
        "dm_pvp_status",
        issue_shape={
            "target_issue": challenge_id,
            "response_shape_when_resolved": {
                "phase": "resolved",
                "match_id": "...",
                "winner_pubkey": "hex",
                "loser_pubkey": "hex",
                "round_count": "int",
                "trace_path": "matches/<match_id>/trace.json",
            },
        },
        hint="arbiter updates Issue body with result JSON",
    )


@mcp.tool()
def dm_pvp_my_matches(limit: int = 20) -> Dict[str, Any]:
    """List open + recent PvP matches for this identity.

    Queries the arena repo for Issues labeled `pvp` where the local pubkey
    is either challenger or responder.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        return {"error": "invalid_input",
                "message": "limit must be int in [1, 100]"}

    return _stub_arena_response(
        "dm_pvp_my_matches",
        issue_shape={
            "query": (
                f"repo:{ARENA_REPO} is:issue label:pvp "
                "(involves:<pubkey_hex>)"
            ),
            "response_shape": {
                "matches": [
                    {
                        "challenge_id": "...",
                        "phase": "resolved|pending-accept|pending-arbiter",
                        "role": "challenger|responder",
                        "opponent_pubkey": "hex",
                        "outcome": "win|loss|draw|pending",
                        "updated_at": "ISO-8601 UTC",
                    }
                ],
                "count": "int",
            },
        },
        hint="backed by GitHub search API when wiring lands",
    )


@mcp.tool()
def dm_leaderboard(limit: int = 25) -> Dict[str, Any]:
    """Read the arena leaderboard.

    Fetches `leaderboard.json` from the arena repo root. The arbiter updates
    this file after every resolved match + every tournament round.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        return {"error": "invalid_input",
                "message": "limit must be int in [1, 100]"}

    return _stub_arena_response(
        "dm_leaderboard",
        issue_shape={
            "source": f"https://raw.githubusercontent.com/{ARENA_REPO}"
                      "/main/leaderboard.json",
            "response_shape": {
                "updated_at": "ISO-8601 UTC",
                "ranks": [
                    {
                        "rank": 1,
                        "handle": "...",
                        "pubkey_hex": "...",
                        "tier": "Champion|Elite|Veteran|Novice|Rookie",
                        "wins": "int", "losses": "int",
                        "rating": "int",
                    }
                ],
            },
        },
        hint="cached locally for 5 min once wiring lands",
    )


@mcp.tool()
def dm_my_rank() -> Dict[str, Any]:
    """Return the local identity's arena standing.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    try:
        from daimon.identity import load_identity
        pubkey_hex = load_identity().pubkey_hex
    except FileNotFoundError:
        return {"error": "no_identity", "hint": "Run `daimon init` first."}

    return _stub_arena_response(
        "dm_my_rank",
        issue_shape={
            "pubkey_hex": pubkey_hex,
            "source": f"https://raw.githubusercontent.com/{ARENA_REPO}"
                      "/main/leaderboard.json",
            "response_shape": {
                "pubkey_hex": pubkey_hex,
                "rank": "int or null",
                "tier": "Rookie|Novice|Veteran|Elite|Champion",
                "wins": "int", "losses": "int", "draws": "int",
                "rating": "int",
            },
        },
        hint="tier gates which NPC pool dm_match draws from",
    )


@mcp.tool()
def dm_dispute_open(match_id: str, reason: str,
                    evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Open an arbiter dispute on a resolved match.

    Costs 50 currency (refunded if upheld). Arbiter re-runs the engine
    deterministically; if their result differs from the original, the
    dispute is upheld and the match result is reversed.

    V1 STUB — arena wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(match_id, str) or not match_id:
        return {"error": "invalid_input",
                "message": "match_id must be non-empty string"}
    if not isinstance(reason, str) or not reason.strip():
        return {"error": "invalid_input",
                "message": "reason must be non-empty string"}

    return _stub_arena_response(
        "dm_dispute_open",
        issue_shape={
            "title": f"dispute: {match_id}",
            "body": {
                "match_id": match_id,
                "reason": reason,
                "evidence": evidence or {},
                "disputant_pubkey": "hex(local)",
                "bond_amount": 50,
                "opened_at": "ISO-8601 UTC",
                "signature": "ed25519(match_id || reason || ts) hex",
            },
            "labels": ["dispute", "pending-arbiter"],
        },
        hint=(
            "bond is deducted from balance on open; refunded on upheld, "
            "forfeited on rejected"
        ),
    )


@mcp.tool()
def dm_card_propose(card_def: Dict[str, Any],
                    rationale: Optional[str] = None) -> Dict[str, Any]:
    """Propose a new card definition for inclusion in a future catalog.

    Opens an Issue in `daimon-cards` with the signed card JSON. Human
    CODEOWNERS review + approve for the next catalog release.

    V1 STUB — cards-repo wiring not yet live; returns issue_shape doc.
    """
    if not isinstance(card_def, dict):
        return {"error": "invalid_input",
                "message": "card_def must be a JSON object"}
    # Best-effort schema check: loader accepts it? Don't raise — return a hint.
    try:
        load_card_dict(card_def)
        schema_ok = True
        schema_error: Optional[str] = None
    except (ValueError, TypeError) as e:
        schema_ok = False
        schema_error = str(e)

    return {
        "status": "not_yet_implemented",
        "tool": "dm_card_propose",
        "cards_repo": "aurorasuperbot/daimon-cards",
        "schema_valid": schema_ok,
        "schema_error": schema_error,
        "issue_shape": {
            "title": f"card proposal: {card_def.get('card_id', '(no id)')}",
            "body": {
                "card_def": card_def,
                "rationale": rationale or "",
                "proposer_pubkey": "hex(local)",
                "proposed_at": "ISO-8601 UTC",
                "signature": "ed25519(card_def || ts) hex",
            },
            "labels": ["card-proposal", "pending-review"],
        },
        "hint": (
            "proposals route through CODEOWNERS PR review; approval bundles "
            "into the next catalog release"
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
