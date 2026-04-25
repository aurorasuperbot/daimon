"""DAIMON MCP server.

Exposes engine + identity + collection as `dm_*` MCP tools so AI agents can
play autonomously. The server is **read-only with respect to the engine** —
all engine entry points are pure functions, the server just adapts arguments
and serializes results.

V1 scope (this file) — 32 tools total (21 locked 2026-04-21 + dm_init
follow-up shipped same day + 3 NPC roster tools shipped 2026-04-22 +
dm_mine_status deprecated alias + dm_pvp_reveal added 2026-04-24 when
arena wiring landed + 5 shop tools shipped 2026-04-24 alongside the
400-PNG skin pack):

Identity + currency:
  dm_init              → bootstrap identity + BIP39 mnemonic (one-time)
  dm_whoami            → pubkey + handle + balance + totals (absorbed mine_status)
  dm_register          → open identity registration Issue in arena
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
  dm_pvp_challenge     → open PvP challenge Issue (commit phase)
  dm_pvp_accept        → accept a pending challenge (commit phase, responder side)
  dm_pvp_reveal        → publish loadout + signature (reveal phase, both sides)
  dm_pvp_status        → poll arbiter result for a challenge
  dm_pvp_my_matches    → list my open + recent PvP matches

Arena state:
  dm_leaderboard       → read leaderboard.json from arena repo
  dm_my_rank           → my standing + record

Disputes + contributions:
  dm_dispute_open      → appeal a resolved match (costs 50 currency)
  dm_card_propose      → propose a new card definition

Skin shop (cosmetic-only):
  dm_shop              → list today's 6 slots (rotates daily 00:00 UTC)
  dm_shop_buy          → purchase a slot (atomic at the ledger boundary)
  dm_skins_owned       → list owned skins + per-card equipped status
  dm_skin_equip        → mount an owned skin on a card
  dm_skin_unequip      → revert a card to its canonical art

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
from daimon.arena import ops as arena_ops
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

# Import-time binding of the single shared config dir (DAIMON_HOME /
# XDG_CONFIG_HOME-aware). Do NOT recompute on each call — tests that need a
# different path monkeypatch CONFIG_DIR on this module (and the derived
# constants below) in the same style as _isolate_paths in test_mcp.py.
from daimon.identity.keys import CONFIG_DIR  # noqa: E402

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

def _resolve_loadout_payload(
    payload: Any, side_label: str
) -> tuple[Loadout, list[Any]]:
    """Normalize an inline loadout payload → (engine.Loadout, raw_payloads).

    Accepts any of three shapes (auto-detected):

      * Bare list: ``[{cardobj}, ...]`` (legacy)
      * Cards dict: ``{"cards": [{cardobj}, ...]}``
      * Showcase dict: ``{"loadout_id":..., "loadout":["card_id", ...]}``
        (resolved against the default catalog, ``v1_alpha``)

    The returned raw list still carries render-only fields (name, rarity,
    art) so display extraction works downstream — ``load_card_dict``
    drops those but leaves the originals untouched.

    Delegates to ``daimon.loadouts.loadout_from_data`` so the MCP and CLI
    surfaces accept identical inputs.
    """
    from daimon.loadouts import loadout_from_data

    return loadout_from_data(payload, source=side_label)


def _raw_cards_from_payload(payload: Any, side_label: str) -> list[Any]:
    """Pull the raw card-dict list out of a loadout payload.

    Back-compat wrapper around ``_resolve_loadout_payload`` — call sites
    that need both the engine.Loadout and the raw list should call the
    new helper directly to avoid double-resolution (especially expensive
    for showcase payloads which hit the catalog cache).
    """
    _lo, raw = _resolve_loadout_payload(payload, side_label)
    return raw


def _loadout_from_payload(payload: Any, side_label: str) -> Loadout:
    """Accept any supported loadout shape and return engine.Loadout."""
    lo, _raw = _resolve_loadout_payload(payload, side_label)
    return lo


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
            "total_purchased": 0,
            "mine_count": 0,
            "pull_count": 0,
            "purchase_count": 0,
            "ledger_entries": 0,
            "verified": True,
            "recent": [],
        }
    stats = get_stats()
    verification = verify_ledger()
    recent = [
        {k: v for k, v in e.items()
         if k in ("ts", "kind", "amount", "tool_name", "card_id",
                  "rarity", "pack", "skin_slug", "skin_axis")}
        for e in get_recent_entries(limit=10)
    ]
    out: Dict[str, Any] = {
        "balance": stats.balance,
        "total_mined": stats.total_mined,
        "total_pulled": stats.total_pulled,
        "total_purchased": stats.total_purchased,
        "mine_count": stats.mine_count,
        "pull_count": stats.pull_count,
        "purchase_count": stats.purchase_count,
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


# NOTE: the old `_stub_arena_response()` helper was removed 2026-04-24 when
# the arena ops module landed. The arena-bound tools below now do real work
# and return real envelopes from `daimon.arena.ops`. Tests that asserted on
# `status == "not_yet_implemented"` were updated in the same commit.


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
        a, a_raw = _resolve_loadout_payload(loadout_a, "loadout_a")
        b, b_raw = _resolve_loadout_payload(loadout_b, "loadout_b")
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
        a_lo, a_raw = _resolve_loadout_payload(loadout, "loadout")
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
    """List cards owned by the local identity, with rollup summaries.

    Reads ~/.config/daimon/collection.json — a JSON document of shape:
      {"serials": [{"serial": "uuid", "card_id": "...", "pack": "...",
                    "rarity": "...", "minted_at": "...", "minted_via": "..."}]}

    Returns:
      {
        "status": "ok",
        "count": int,                        # total serials owned
        "unique_cards": int,                 # distinct card_ids
        "rarity_counts": {                   # serial counts by rarity
            "common": int, "uncommon": int, "rare": int,
            "epic": int, "legendary": int
        },
        "by_card": [                         # one row per unique card_id,
            {                                # sorted by rarity then card_id
                "card_id": "...",
                "rarity": "...",
                "count": int                 # number of serials of this card
            },
            ...
        ],
        "serials": [...]                     # full raw list (unchanged)
      }

    On error:
      {"error": "no_collection", ...}     — fresh install, nothing owned
      {"error": "corrupt_collection", "message": "..."}
    """
    if not COLLECTION_PATH.exists():
        return {
            "error": "no_collection",
            "count": 0,
            "unique_cards": 0,
            "rarity_counts": {},
            "by_card": [],
            "serials": [],
        }
    try:
        data = json.loads(COLLECTION_PATH.read_text())
    except json.JSONDecodeError as e:
        return {"error": "corrupt_collection", "message": str(e)}

    serials = data.get("serials", [])
    if not isinstance(serials, list):
        return {"error": "corrupt_collection", "message": "serials is not a list"}

    # Rollup: rarity bucket counts + per-card row aggregation.
    _RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")

    def _rarity_sort_key(r: str) -> int:
        try:
            return _RARITY_ORDER.index(r)
        except ValueError:
            return len(_RARITY_ORDER)

    rarity_counts: Dict[str, int] = {}
    by_card_map: Dict[str, Dict[str, Any]] = {}
    for s in serials:
        if not isinstance(s, dict):
            continue
        cid = s.get("card_id") or "?"
        rar = s.get("rarity") or "?"
        rarity_counts[rar] = rarity_counts.get(rar, 0) + 1
        row = by_card_map.setdefault(
            cid, {"card_id": cid, "rarity": rar, "count": 0}
        )
        row["count"] += 1

    by_card = sorted(
        by_card_map.values(),
        key=lambda r: (_rarity_sort_key(r["rarity"]), r["card_id"]),
    )

    return {
        "status": "ok",
        "count": len(serials),
        "unique_cards": len(by_card_map),
        "rarity_counts": rarity_counts,
        "by_card": by_card,
        "serials": serials,
    }


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
# Arena-bound tools
#
# Every tool below is a thin shim over `daimon.arena.ops` — input
# validation lives here, the actual gh-CLI dance + signing + state
# persistence lives in the ops layer. The contract for each tool is
# documented in detail in the corresponding `arena.ops` function.
#
# All envelopes follow the convention used by neighboring tools:
#   success: {"status": "ok", ...}
#   failure: {"error": "<category>", "message": "..."}
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_register(handle: Optional[str] = None) -> Dict[str, Any]:
    """Register this local identity with the arena.

    Opens an identity Issue in ``DAIMON_ARENA_REPO`` carrying a signed
    assertion ``{pubkey_hex, handle, signed_at, signature}`` so the arbiter
    can bind pubkey → GitHub account + display handle. The signature uses
    the `daimon-register-v1` domain-separated payload so it can't be
    replayed in another protocol context.

    Args:
      handle: Display handle to bind to this pubkey. Optional; defaults to
              ``"(auto)"`` (the arbiter may derive one from the GitHub
              account on its side).

    Returns on success::
        {"status": "ok", "issue_number": int, "url": "...",
         "pubkey_hex": "...", "handle": "...", "phase": "pending-arbiter"}

    Returns on failure::
        {"error": "no_identity", "hint": "..."}              # no key on disk
        {"error": "<gh_*>", "message": "..."}                # gh CLI failed
    """
    return arena_ops.register(handle=handle)


@mcp.tool()
def dm_pvp_challenge(opponent_pubkey: str,
                     loadout: Any,
                     memo: Optional[str] = None,
                     pack_pin: str = "starter-v1.0.0",
                     rule_set: str = "standard-v1") -> Dict[str, Any]:
    """Open an async PvP challenge — commit phase, challenger side.

    Generates a fresh 32-byte nonce, commits SHA-256(canonical(loadout) ||
    nonce) to the arena Issue, and saves the loadout + nonce locally for
    a later ``dm_pvp_reveal``. Two-phase commit-reveal so neither side can
    react to the other's loadout.

    Args:
      opponent_pubkey: 64-char hex pubkey of the player you're challenging.
      loadout: Either ``{"cards": [...]}`` or a bare card list.
      memo: Optional human-readable note attached to the Issue body.
      pack_pin: Catalog version pin (default ``"starter-v1.0.0"``); arbiter
                rejects matches whose loadouts reference cards outside this
                pack version.
      rule_set: Engine rule-set tag (default ``"standard-v1"``).

    Returns on success::
        {"status": "ok", "challenge_id": "<issue_number>",
         "issue_number": int, "url": "...", "loadout_commit": "<hex>",
         "phase": "pending-accept", "next_step": "..."}

    Returns on failure::
        {"error": "no_identity", "hint": "..."}
        {"error": "invalid_input", "message": "..."}
        {"error": "<gh_*>", "message": "..."}

    Next step after success: wait for opponent's ``dm_pvp_accept``, then
    call ``dm_pvp_reveal(challenge_id)`` to publish your loadout.
    """
    if not isinstance(opponent_pubkey, str) or len(opponent_pubkey) != 64:
        return {"error": "invalid_input",
                "message": "opponent_pubkey must be 64-char hex"}
    # Reject early on shape; the ops layer will re-validate but it's a worse
    # UX to discover this after spending a gh round-trip.
    try:
        _raw_cards_from_payload(loadout, "loadout")
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}
    return arena_ops.pvp_challenge(
        opponent_pubkey=opponent_pubkey,
        loadout=loadout,
        memo=memo,
        pack_pin=pack_pin,
        rule_set=rule_set,
    )


@mcp.tool()
def dm_pvp_accept(challenge_id: str, loadout: Any) -> Dict[str, Any]:
    """Accept a pending PvP challenge — commit phase, responder side.

    Posts a ``/accept`` comment carrying just the responder's commit hash
    (SHA-256 of canonical(loadout) || nonce). The full loadout is held
    locally until ``dm_pvp_reveal`` — same commit-reveal flow the
    challenger uses.

    Args:
      challenge_id: Issue number from ``dm_pvp_challenge``.
      loadout: Either ``{"cards": [...]}`` or a bare card list.

    Returns on success::
        {"status": "ok", "challenge_id": "...", "loadout_commit": "<hex>",
         "phase": "pending-arbiter", "next_step": "..."}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "invalid_input", "message": "..."}
        {"error": "<gh_*>", "message": "..."}

    Next step after success: call ``dm_pvp_reveal(challenge_id)``.
    """
    if not isinstance(challenge_id, str) or not challenge_id:
        return {"error": "invalid_input",
                "message": "challenge_id must be non-empty string"}
    try:
        _raw_cards_from_payload(loadout, "loadout")
    except (ValueError, TypeError) as e:
        return {"error": "invalid_input", "message": str(e)}
    return arena_ops.pvp_accept(challenge_id=challenge_id, loadout=loadout)


@mcp.tool()
def dm_pvp_reveal(challenge_id: str) -> Dict[str, Any]:
    """Reveal a previously-committed loadout — reveal phase, both sides.

    Reads the local ``~/.daimon/pvp_state/<id>.json`` record (saved by
    ``dm_pvp_challenge`` or ``dm_pvp_accept``), signs the canonical reveal
    payload with the local ed25519 key, and posts a ``/reveal`` comment
    carrying ``{pubkey, nonce, signature, loadout_json}``. The arbiter
    matches reveals to sides by pubkey (not by comment order) so either
    side can reveal first.

    Args:
      challenge_id: Issue number — must match a saved local state file.

    Returns on success::
        {"status": "ok", "challenge_id": "...", "phase": "revealed",
         "next_step": "wait for opponent's reveal + arbiter settlement"}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "invalid_input", ...}
        {"error": "no_local_state", "hint": "..."}      # no saved nonce
        {"error": "identity_mismatch", ...}             # state from a different key
        {"error": "<gh_*>", "message": "..."}
    """
    if not isinstance(challenge_id, str) or not challenge_id:
        return {"error": "invalid_input",
                "message": "challenge_id must be non-empty string"}
    return arena_ops.pvp_reveal(challenge_id=challenge_id)


@mcp.tool()
def dm_pvp_status(challenge_id: str) -> Dict[str, Any]:
    """Poll the current phase + (when settled) result for a PvP match.

    Reads the Issue body, labels, and comments to determine whether the
    match is ``pending-accept`` / ``revealing`` / ``pending-arbiter`` /
    ``resolved``. When resolved, also fetches ``matches/<id>.json`` from
    the arena repo and decodes winner/loser pubkeys.

    Args:
      challenge_id: Issue number from ``dm_pvp_challenge``.

    Returns on success (any phase)::
        {"status": "ok", "challenge_id": "...", "issue_number": int,
         "url": "...", "title": "...", "labels": [...],
         "issue_state": "open|closed", "phase": "...",
         "comment_count": int, "reveal_count": int,
         "match"?: {...}, "winner_pubkey"?: "...", "loser_pubkey"?: "..."}

    Returns on failure::
        {"error": "invalid_input", ...}
        {"error": "<gh_*>", "message": "..."}
    """
    if not isinstance(challenge_id, str) or not challenge_id:
        return {"error": "invalid_input",
                "message": "challenge_id must be non-empty string"}
    return arena_ops.pvp_status(challenge_id=challenge_id)


@mcp.tool()
def dm_pvp_my_matches(limit: int = 20) -> Dict[str, Any]:
    """List open + recent PvP matches involving this identity.

    Pulls all ``pvp``-labeled Issues from the arena repo and filters
    client-side by pubkey embedded in the body kv pairs (challenger or
    opponent). Returns lightweight summaries — call ``dm_pvp_status`` for
    the full per-match record.

    Args:
      limit: Max matches to return. Range [1, 100]. Default 20.

    Returns on success::
        {"status": "ok", "count": int, "matches": [
            {"challenge_id": "...", "issue_number": int, "phase": "...",
             "role": "challenger|responder", "opponent_pubkey": "...",
             "url": "...", "updated_at": "..."}, ...]}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "invalid_input", ...}
        {"error": "<gh_*>", "message": "..."}
    """
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        return {"error": "invalid_input",
                "message": "limit must be int in [1, 100]"}
    return arena_ops.pvp_my_matches(limit=limit)


@mcp.tool()
def dm_leaderboard(limit: int = 25) -> Dict[str, Any]:
    """Read the arena leaderboard.

    Fetches ``leaderboard.json`` from the arena repo root and ranks
    entries by wins desc (losses asc tiebreak). Tier label is computed
    locally from wins via ``arena.ops.tier_of``.

    Args:
      limit: Max ranks to return. Range [1, 100]. Default 25.

    Returns on success::
        {"status": "ok", "updated_at": "...", "total_players": int,
         "count": int, "ranks": [
            {"rank": int, "pubkey_hex": "...", "wins": int, "losses": int,
             "draws": int, "tier": "Rookie|Novice|Veteran|Elite|Champion"},
            ...]}

    Returns on failure::
        {"error": "invalid_input", ...}
        {"error": "<gh_*>", "message": "..."}

    If the leaderboard file doesn't exist yet (cold-start arena), returns
    success with empty ranks and a ``note`` field explaining why.
    """
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        return {"error": "invalid_input",
                "message": "limit must be int in [1, 100]"}
    return arena_ops.leaderboard(limit=limit)


@mcp.tool()
def dm_my_rank() -> Dict[str, Any]:
    """Return the local identity's arena standing.

    Reads the leaderboard, finds the local pubkey, returns rank + record.
    Players with no matches yet get ``rank: null`` and a friendly note.

    Returns on success::
        {"status": "ok", "pubkey_hex": "...", "rank": int|null,
         "tier": "...", "wins": int, "losses": int, "draws": int,
         "total_players": int, "note"?: "..."}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "<gh_*>", "message": "..."}
    """
    return arena_ops.my_rank()


@mcp.tool()
def dm_dispute_open(match_id: str, reason: str,
                    evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Open an arbiter dispute on a resolved match.

    Currently V1: the dispute is recorded with a ``bond_amount: 50`` field
    in the Issue body (documentation-only — the engine-side currency-spend
    layer ships in V1.1; arbiter will refund/forfeit when it lands).
    The dispute body is signed with the ``daimon-dispute-v1`` payload so
    nobody can replay a signature into a different context.

    Args:
      match_id: The match identifier (typically the issue number string).
      reason: Free-text reason — kept on a single line in the kv body.
      evidence: Optional structured payload embedded as a JSON block.

    Returns on success::
        {"status": "ok", "issue_number": int, "url": "...",
         "match_id": "...", "bond_amount": 50,
         "phase": "pending-review", "note": "..."}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "invalid_input", ...}
        {"error": "<gh_*>", "message": "..."}
    """
    if not isinstance(match_id, str) or not match_id:
        return {"error": "invalid_input",
                "message": "match_id must be non-empty string"}
    if not isinstance(reason, str) or not reason.strip():
        return {"error": "invalid_input",
                "message": "reason must be non-empty string"}
    return arena_ops.dispute_open(
        match_id=match_id, reason=reason, evidence=evidence,
    )


@mcp.tool()
def dm_card_propose(card_def: Dict[str, Any],
                    rationale: Optional[str] = None) -> Dict[str, Any]:
    """Propose a new card definition for inclusion in a future catalog.

    Validates ``card_def`` against the engine's card loader BEFORE opening
    an Issue (proposing a card the engine can't load wastes everyone's
    time). On schema failure returns ``{"error": "invalid_card", ...}``
    immediately. On schema pass, opens an Issue in the cards repo with
    the signed card JSON. Human CODEOWNERS review + approve for the next
    catalog release.

    Args:
      card_def: Full card-definition dict (engine-loadable shape).
      rationale: Optional design rationale, kept single-line in the body.

    Returns on success::
        {"status": "ok", "issue_number": int, "url": "...",
         "card_id": "...", "phase": "pending-review"}

    Returns on failure::
        {"error": "no_identity", ...}
        {"error": "invalid_input", ...}
        {"error": "invalid_card", "schema_error": "..."}
        {"error": "<gh_*>", "message": "..."}
    """
    if not isinstance(card_def, dict):
        return {"error": "invalid_input",
                "message": "card_def must be a JSON object"}
    return arena_ops.card_propose(card_def=card_def, rationale=rationale)


# ---------------------------------------------------------------------------
# Shop tools — V1 cosmetic skin marketplace (5 tools)
# ---------------------------------------------------------------------------

@mcp.tool()
def dm_shop(slot: Optional[int] = None) -> Dict[str, Any]:
    """List today's 6-slot skin shop (or detail one slot).

    The shop refreshes every 00:00 UTC. Slot composition is 4 rare + 2
    super_rare, deterministically shuffled per (pubkey, date) — different
    agents see different shops on the same day, the same agent sees a
    fresh shop every UTC midnight.

    Slots are STABLE intra-day: when you buy a slot, the listing stays at
    the same index marked ``sold=true`` so other slots don't shift. At
    00:00 UTC tomorrow today's purchases drop out and a fresh rotation
    fills their indices.

    Args:
      slot: Optional 0-based slot index. When given, return only that slot.
            Otherwise return all slots + the wallet/cap/refresh-clock summary.

    Returns on success (no slot):
      {"status": "ok", "balance": int, "weekly_count": int, "weekly_cap": int,
       "weekly_remaining": int, "seconds_until_rotation": int,
       "slot_count": int, "slots": [{"index": int, "card_id": "...",
       "skin_slug": "...", "skin_name": "...", "skin_axis": "cultural"|"anatomical",
       "rarity": "rare"|"super_rare", "cost": int, "variant_id": "...",
       "art_path": "...", "sold": bool, "purchased_at": "iso-ts"|absent}]}

    Returns on success (slot given):
      {"status": "ok", **slot_payload}

    Returns on failure:
      {"error": "no_identity", "hint": "..."}
      {"error": "slot_out_of_range", "message": "..."}
      {"error": "internal_error", "message": "..."}
    """
    try:
        from daimon.shop import get_shop_state
        state = get_shop_state()
    except FileNotFoundError:
        return {"error": "no_identity",
                "hint": "Run `dm_init` (MCP) or `daimon init` (CLI) first."}
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    if slot is None:
        return {"status": "ok", **state.to_dict()}

    if not isinstance(slot, int):
        return {"error": "invalid_input",
                "message": f"slot must be int, got {type(slot).__name__}"}
    if slot < 0 or slot >= len(state.slots):
        return {"error": "slot_out_of_range",
                "message": f"slot {slot} out of range (0..{len(state.slots) - 1})",
                "slot_count": len(state.slots)}

    return {"status": "ok", **state.slots[slot].to_dict()}


@mcp.tool()
def dm_shop_buy(slot: Optional[int] = None,
                selector: Optional[str] = None) -> Dict[str, Any]:
    """Purchase one skin from today's rotation.

    Atomic at the ledger boundary — the `kind="purchase"` ledger entry is
    the authoritative spend. owned_skins.json is a convenience cache.

    Args:
      slot: 0-based slot index from `dm_shop`. Most ergonomic for agents.
      selector: Either an int slot index OR an unambiguous string —
                "card_id/skin_slug" (composite, exact) or a bare slug
                (rejected if more than one slot in today's rotation has it).
                Use this when programmatic addressing by slug is needed.

    Exactly ONE of `slot` / `selector` should be provided. If both are
    given, `slot` wins.

    Returns on success:
      {"status": "ok", "card_id": "...", "skin_slug": "...",
       "skin_name": "...", "skin_axis": "...", "rarity": "...",
       "cost": int, "balance_after": int, "purchased_at": "iso-ts",
       "ledger_entry_hash": "..."}

    Returns on failure:
      {"error": "no_identity", "hint": "..."}
      {"error": "invalid_input", "message": "..."}
      {"error": "slot_not_in_rotation", "message": "..."}
      {"error": "already_owned", "message": "..."}
      {"error": "weekly_cap_exceeded", "message": "...", "weekly_cap": int}
      {"error": "insufficient_balance", "balance": int, "needed": int, "cost": int}
      {"error": "internal_error", "message": "..."}
    """
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.shop import (
        AlreadyOwnedError,
        SlotNotInRotationError,
        WEEKLY_CAP,
        WeeklyCapExceededError,
        purchase_slot,
    )

    if slot is None and selector is None:
        return {"error": "invalid_input",
                "message": "must provide either `slot` (int) or `selector` (str)"}

    sel: Any
    if slot is not None:
        if not isinstance(slot, int):
            return {"error": "invalid_input",
                    "message": f"slot must be int, got {type(slot).__name__}"}
        sel = slot
    else:
        if not isinstance(selector, str) or not selector:
            return {"error": "invalid_input",
                    "message": "selector must be a non-empty string"}
        # Allow callers to pass bare digit strings as the selector too.
        sel = int(selector) if selector.isdigit() else selector

    try:
        receipt = purchase_slot(sel)
    except FileNotFoundError:
        return {"error": "no_identity",
                "hint": "Run `dm_init` (MCP) or `daimon init` (CLI) first."}
    except SlotNotInRotationError as e:
        return {"error": "slot_not_in_rotation", "message": str(e)}
    except AlreadyOwnedError as e:
        return {"error": "already_owned", "message": str(e)}
    except WeeklyCapExceededError as e:
        return {"error": "weekly_cap_exceeded",
                "message": str(e), "weekly_cap": WEEKLY_CAP}
    except InsufficientBalanceError as e:
        from daimon.mining.ledger import get_balance
        bal = get_balance()
        msg = str(e)
        # Parse "need N, have M" — fall back to the raw message if parse fails.
        cost = needed = 0
        try:
            parts = msg.replace(",", "").split()
            cost = int(parts[1])
            needed = max(0, cost - bal)
        except (IndexError, ValueError):
            pass
        return {"error": "insufficient_balance", "message": msg,
                "balance": bal, "needed": needed, "cost": cost}
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    return {"status": "ok", **receipt.to_dict()}


@mcp.tool()
def dm_skins_owned() -> Dict[str, Any]:
    """List skins this identity owns. Includes equipped status per entry.

    Returns:
      {"status": "ok", "count": int, "owned": [
         {"card_id": "...", "skin_slug": "...", "skin_name": "...",
          "skin_axis": "...", "rarity": "...", "purchased_at": "iso-ts",
          "cost": int, "ledger_entry_hash": "...", "equipped": bool}
      ]}

    `equipped: true` means this exact skin is currently mounted on its card
    (and thus shows up in battle / replay renders).
    """
    from dataclasses import asdict

    try:
        from daimon.shop import get_equipped, list_owned
        owned = list_owned()
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    rows: List[Dict[str, Any]] = []
    for s in owned:
        d = asdict(s)
        d["equipped"] = get_equipped(s.card_id) == s.skin_slug
        rows.append(d)

    return {"status": "ok", "count": len(rows), "owned": rows}


@mcp.tool()
def dm_skin_equip(card_id: str, skin_slug: str) -> Dict[str, Any]:
    """Equip a skin you own onto a card. The render layer picks this up
    immediately — every subsequent battle / replay shows the skin.

    Args:
      card_id:  card identifier (must own a skin for it).
      skin_slug: which owned skin to mount.

    Returns on success:
      {"status": "ok", "card_id": "...", "skin_slug": "...",
       "equipped": {card_id: skin_slug, ...}}  # full equipped map

    Returns on failure:
      {"error": "skin_not_found", "message": "..."}      # bad inputs
      {"error": "not_owned", "message": "..."}            # don't own this skin
      {"error": "internal_error", "message": "..."}
    """
    from daimon.shop import NotOwnedError, SkinNotFoundError, equip_skin

    try:
        eq = equip_skin(card_id, skin_slug)
    except SkinNotFoundError as e:
        return {"error": "skin_not_found", "message": str(e)}
    except NotOwnedError as e:
        return {"error": "not_owned", "message": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    return {"status": "ok", "card_id": card_id, "skin_slug": skin_slug,
            "equipped": eq}


@mcp.tool()
def dm_skin_unequip(card_id: str) -> Dict[str, Any]:
    """Revert a card to its canonical base art. No-op if no skin equipped.

    Returns:
      {"status": "ok", "card_id": "...", "equipped": {...}}  # full map after
    """
    try:
        from daimon.shop import unequip_skin
        eq = unequip_skin(card_id)
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error",
                "message": f"{type(e).__name__}: {e}"}

    return {"status": "ok", "card_id": card_id, "equipped": eq}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_stdio() -> None:
    """Run the MCP server over stdio (for direct agent integration)."""
    mcp.run()


if __name__ == "__main__":
    run_stdio()
