"""HTTP routes for the local game backend.

All endpoints delegate to the existing MCP tool implementations so the
agent surface and the human surface stay in lockstep — same envelopes,
same validation, same side effects (state.json + ledger writes).

Endpoints:
  GET  /api/home              — dm_home() snapshot
  GET  /api/shop              — current 6-slot rotation
  POST /api/shop/buy/{slot}   — purchase one slot
  GET  /api/shop/refresh      — seconds until next rotation
  GET  /api/skins             — owned skins (per-card)
  POST /api/skin/equip        — equip an owned skin
  POST /api/skin/unequip      — revert to canonical art
  GET  /api/collection        — owned card serials
  GET  /api/catalog           — full card catalog (for the loadout editor)
  GET  /api/card/{card_id}    — single card payload (for the dm-card cache)
  GET  /api/loadouts          — list saved loadouts
  GET  /api/loadout/{name}    — fetch one
  POST /api/loadout/{name}    — save (body: {"cards": [...]})
  DELETE /api/loadout/{name}  — drop a saved loadout
  GET  /api/match/recommended — next-NPC recommendation
  POST /api/match/start       — resolve a loadout-vs-NPC match
  POST /api/pull              — perform a gacha pull
  GET  /api/pvp/leaderboard   — arena leaderboard
  GET  /api/pvp/my-rank       — local player's arena standing
  GET  /api/pvp/matches       — open + recent PvP challenges
  GET  /api/pvp/status/{id}   — single challenge status
  POST /api/pvp/register      — register identity on the arena
  POST /api/pvp/challenge     — open a new PvP challenge
  POST /api/pvp/accept        — accept an incoming challenge
  POST /api/pvp/reveal        — reveal loadout for a challenge
  GET  /art/{card_id}         — equipped-skin-aware PNG
  WS   /ws                    — live balance + receipt push (post-pull, post-buy)
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from daimon.web.live import broadcaster


router = APIRouter()


# ---------------------------------------------------------------------------
# Rate limiter — sliding-window, thread-safe
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Per-endpoint sliding-window rate limiter.

    ``max_calls`` is the ceiling within ``window_seconds``.  The limiter is
    process-global (single server, single user) so there is no per-IP
    dimension — every call from any source shares the window.
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self._max = max_calls
        self._window = window_seconds
        self._lock = threading.Lock()
        self._timestamps: list[float] = []

    def acquire_or_raise(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._timestamps = [
                t for t in self._timestamps if now - t < self._window
            ]
            if len(self._timestamps) >= self._max:
                retry = self._window - (now - self._timestamps[0])
                raise HTTPException(
                    status_code=429,
                    detail=f"rate limited — retry in {retry:.0f}s",
                    headers={"Retry-After": str(int(retry))},
                )
            self._timestamps.append(now)


_ISSUE_CREATE_LIMITER = _RateLimiter(max_calls=3, window_seconds=60)
_COMMENT_LIMITER = _RateLimiter(max_calls=6, window_seconds=60)

_CARD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _call_tool(tool, **kwargs):
    """FastMCP wraps each function in a tool descriptor; ``.fn`` is the
    underlying callable. Local routes hit the raw function so we don't pay
    the FastMCP wrapper overhead."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@router.get("/api/home")
def get_home() -> Dict[str, Any]:
    from daimon.mcp.server import dm_home
    return _call_tool(dm_home)


# ---------------------------------------------------------------------------
# Shop
# ---------------------------------------------------------------------------

@router.get("/api/shop")
def get_shop(slot: Optional[int] = None) -> Dict[str, Any]:
    from daimon.mcp.server import dm_shop
    return _call_tool(dm_shop, slot=slot)


@router.post("/api/shop/buy/{slot}")
async def post_shop_buy(slot: int) -> Dict[str, Any]:
    from daimon.mcp.server import dm_shop_buy
    out = _call_tool(dm_shop_buy, slot=slot)
    if "error" not in out:
        await broadcaster.push({
            "kind": "purchase",
            "balance": out.get("balance_after"),
            "card_id": out.get("card_id"),
            "skin_slug": out.get("skin_slug"),
        })
    return out


@router.get("/api/shop/refresh")
def get_shop_refresh() -> Dict[str, Any]:
    from daimon.shop import seconds_until_next_rotation
    secs = seconds_until_next_rotation()
    return {"seconds_until_rotation": secs}


# ---------------------------------------------------------------------------
# Skins
# ---------------------------------------------------------------------------

@router.get("/api/skins")
def get_skins() -> Dict[str, Any]:
    from daimon.mcp.server import dm_skins_owned
    return _call_tool(dm_skins_owned)


class SkinEquipBody(BaseModel):
    card_id: str
    skin_slug: str


@router.post("/api/skin/equip")
async def post_skin_equip(body: SkinEquipBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_skin_equip
    out = _call_tool(dm_skin_equip, card_id=body.card_id, skin_slug=body.skin_slug)
    if "error" not in out:
        await broadcaster.push({
            "kind": "skin", "action": "equip",
            "card_id": body.card_id, "skin_slug": body.skin_slug,
        })
    return out


class SkinUnequipBody(BaseModel):
    card_id: str


@router.post("/api/skin/unequip")
async def post_skin_unequip(body: SkinUnequipBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_skin_unequip
    out = _call_tool(dm_skin_unequip, card_id=body.card_id)
    if "error" not in out:
        await broadcaster.push({
            "kind": "skin", "action": "unequip",
            "card_id": body.card_id,
        })
    return out


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

@router.get("/api/collection")
def get_collection() -> Dict[str, Any]:
    from daimon.mcp.server import dm_collection
    return _call_tool(dm_collection)


# ---------------------------------------------------------------------------
# Imprint — per-serial biography (stats, trophies, provenance)
# ---------------------------------------------------------------------------

@router.get("/api/imprint/card/{card_id}")
def get_imprints_for_card(card_id: str) -> List[Dict[str, Any]]:
    from daimon.collection import list_serials
    from daimon.imprint import get_serial_stats, compute_trophies

    serials = list_serials()
    matching = [s for s in serials if s.get("card_id") == card_id]
    matching.sort(key=lambda s: s.get("mint_number") or float("inf"))

    result = []
    for s in matching:
        sid = s.get("serial", "")
        stats = get_serial_stats(sid) or {
            "wins": 0, "losses": 0, "kills": 0,
            "damage_dealt": 0, "damage_taken": 0,
            "matches_played": 0, "streak": 0, "best_streak": 0,
        }
        trophies = compute_trophies(stats)
        if s.get("edition") == "1st" and "first_edition" not in trophies:
            trophies.insert(0, "first_edition")
        result.append({
            "serial": sid,
            "mint_number": s.get("mint_number"),
            "edition": s.get("edition"),
            "minted_at": s.get("minted_at"),
            "stats": stats,
            "trophies": trophies,
        })
    return result


@router.get("/api/imprint/{serial}")
def get_imprint(serial: str) -> Dict[str, Any]:
    from daimon.collection import list_serials
    from daimon.imprint import get_serial_stats, compute_trophies

    serials = list_serials()
    serial_doc = next((s for s in serials if s.get("serial") == serial), None)
    if serial_doc is None:
        raise HTTPException(status_code=404, detail="serial not found")

    stats = get_serial_stats(serial) or {
        "wins": 0, "losses": 0, "kills": 0,
        "damage_dealt": 0, "damage_taken": 0,
        "matches_played": 0, "streak": 0, "best_streak": 0,
    }
    trophies = compute_trophies(stats)
    if serial_doc.get("edition") == "1st" and "first_edition" not in trophies:
        trophies.insert(0, "first_edition")

    return {
        "serial": serial,
        "card_id": serial_doc.get("card_id"),
        "mint_number": serial_doc.get("mint_number"),
        "edition": serial_doc.get("edition"),
        "original_owner": serial_doc.get("original_owner_pubkey"),
        "minted_at": serial_doc.get("minted_at"),
        "minted_via": serial_doc.get("minted_via"),
        "stats": stats,
        "trophies": trophies,
        "provenance": [{
            "event": "minted",
            "by": serial_doc.get("original_owner_pubkey")
                   or serial_doc.get("pubkey_hex", ""),
            "at": serial_doc.get("minted_at"),
        }],
    }


@router.get("/api/match-history")
def get_match_history(limit: int = 50) -> Dict[str, Any]:
    from daimon.match_history import recent_matches
    matches = recent_matches(limit=min(limit, 200))
    return {"matches": matches, "total": len(matches)}


@router.get("/api/stats")
def get_stats() -> Dict[str, Any]:
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    from daimon.collection import list_serials
    from daimon.imprint import compute_trophies, get_serial_stats
    from daimon.match_history import recent_matches

    serials = list_serials()
    try:
        cat = load_catalog(DEFAULT_CATALOG_ID)
        catalog_size = len(cat.cards)
        catalog_by_rarity: Dict[str, int] = {}
        catalog_by_element: Dict[str, int] = {}
        for c in cat.cards:
            catalog_by_rarity[c.rarity] = catalog_by_rarity.get(c.rarity, 0) + 1
            elem = c.payload.get("element", "NORMAL")
            catalog_by_element[elem] = catalog_by_element.get(elem, 0) + 1
    except Exception:
        catalog_size = 0
        catalog_by_rarity = {}
        catalog_by_element = {}

    card_element: Dict[str, str] = {}
    try:
        for c in cat.cards:
            card_element[c.card_id] = c.payload.get("element", "NORMAL")
    except Exception:
        pass

    unique_ids: set = set()
    owned_by_rarity: Dict[str, int] = {}
    unique_by_element: Dict[str, int] = {}
    for s in serials:
        cid = s.get("card_id", "")
        if cid in unique_ids:
            continue
        unique_ids.add(cid)
        rarity = s.get("rarity", "common")
        owned_by_rarity[rarity] = owned_by_rarity.get(rarity, 0) + 1
        elem = card_element.get(cid, "NORMAL")
        unique_by_element[elem] = unique_by_element.get(elem, 0) + 1

    matches = recent_matches(limit=200)
    total_matches = len(matches)
    wins = sum(1 for m in matches if (m.get("outcome") or "").lower().startswith("w"))
    losses = sum(1 for m in matches if (m.get("outcome") or "").lower().startswith("l"))
    draws = total_matches - wins - losses

    current_streak = 0
    best_match_streak = 0
    streak_type = ""
    for m in reversed(matches):
        outcome = (m.get("outcome") or "").lower()
        if outcome.startswith("w"):
            if streak_type == "w":
                current_streak += 1
            elif streak_type == "":
                streak_type = "w"
                current_streak = 1
            else:
                break
        elif outcome.startswith("l"):
            if streak_type == "l":
                current_streak += 1
            elif streak_type == "":
                streak_type = "l"
                current_streak = 1
            else:
                break
        else:
            break
    temp_streak = 0
    for m in matches:
        if (m.get("outcome") or "").lower().startswith("w"):
            temp_streak += 1
            best_match_streak = max(best_match_streak, temp_streak)
        else:
            temp_streak = 0

    recent_results = []
    for m in matches[-30:]:
        o = (m.get("outcome") or "").lower()
        recent_results.append({
            "outcome": "win" if o.startswith("w") else "loss" if o.startswith("l") else "draw",
            "opponent": m.get("opponent", "?"),
            "ts": m.get("ts", ""),
        })

    card_stats: Dict[str, Dict[str, Any]] = {}
    all_trophies: Dict[str, int] = {}
    trophy_cards = []
    for s in serials:
        sid = s.get("serial", "")
        cid = s.get("card_id", "")
        stats = get_serial_stats(sid)
        if not stats:
            continue
        trophies = compute_trophies(stats)
        if s.get("edition") == "1st" and "first_edition" not in trophies:
            trophies.insert(0, "first_edition")
        for t in trophies:
            all_trophies[t] = all_trophies.get(t, 0) + 1
        if trophies:
            trophy_cards.append({
                "card_id": cid,
                "serial": sid,
                "trophies": trophies,
            })
        if cid not in card_stats:
            card_stats[cid] = {
                "card_id": cid,
                "rarity": s.get("rarity", "common"),
                "total_wins": 0,
                "total_losses": 0,
                "total_kills": 0,
                "total_damage": 0,
            }
        card_stats[cid]["total_wins"] += stats.get("wins", 0)
        card_stats[cid]["total_losses"] += stats.get("losses", 0)
        card_stats[cid]["total_kills"] += stats.get("kills", 0)
        card_stats[cid]["total_damage"] += stats.get("damage_dealt", 0)

    top_performers = sorted(
        card_stats.values(),
        key=lambda c: (c["total_wins"], c["total_kills"], c["total_damage"]),
        reverse=True,
    )[:6]

    return {
        "collection": {
            "unique_owned": len(unique_ids),
            "total_serials": len(serials),
            "catalog_size": catalog_size,
            "by_rarity": owned_by_rarity,
            "catalog_by_rarity": catalog_by_rarity,
            "by_element": unique_by_element,
            "catalog_by_element": catalog_by_element,
        },
        "matches": {
            "total": total_matches,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "current_streak": current_streak,
            "streak_type": streak_type,
            "best_streak": best_match_streak,
            "recent": recent_results,
        },
        "top_performers": top_performers,
        "trophies": {
            "total": sum(all_trophies.values()),
            "unique_types": len(all_trophies),
            "by_type": all_trophies,
            "cards": trophy_cards[:12],
        },
    }


# ---------------------------------------------------------------------------
# Catalog (read-only, drives the loadout editor's left pane)
# ---------------------------------------------------------------------------

@router.get("/api/catalog")
def get_catalog(expansion: Optional[str] = None) -> Dict[str, Any]:
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    cid = expansion or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown catalog {cid!r}")
    return {
        "pack_id": cat.pack_id,
        "version": cat.version,
        "cards": [
            {
                "card_id": c.card_id,
                "rarity": c.rarity,
                **{k: v for k, v in c.payload.items() if k != "triggers"},
                "trigger_count": len(c.payload.get("triggers", []) or []),
            }
            for c in cat.cards
        ],
    }


@router.get("/api/card/{card_id}")
def get_card(card_id: str, expansion: Optional[str] = None) -> Dict[str, Any]:
    """Return the full catalog payload for one card.

    Used by the frontend ``cardStore`` to populate ``<dm-card>`` instances
    on demand. Unlike ``/api/catalog`` this preserves ``triggers`` and
    every other payload field so the card renderer has everything it
    needs in one round-trip per unique id.

    Augments the raw payload with ``rule_change_text`` when the card
    carries a legendary mutation tag — the catalog stores only the
    opaque ID (``L3``); the human-readable description lives in
    ``daimon.engine.types.RULE_CHANGE_DESCRIPTIONS`` and the render
    layer needs both.
    """
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    from daimon.engine.types import RULE_CHANGE_DESCRIPTIONS
    cid = expansion or DEFAULT_CATALOG_ID
    try:
        cat = load_catalog(cid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown catalog {cid!r}")
    card = cat.by_id.get(card_id)
    if card is None:
        raise HTTPException(
            status_code=404, detail=f"unknown card {card_id!r} in {cid!r}",
        )
    payload = dict(card.payload)
    rc = payload.get("rule_change")
    if rc and rc in RULE_CHANGE_DESCRIPTIONS:
        payload["rule_change_text"] = RULE_CHANGE_DESCRIPTIONS[rc]
    return {
        "card_id": card.card_id,
        "rarity": card.rarity,
        "pack": card.pack,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Loadouts
# ---------------------------------------------------------------------------

@router.get("/api/loadouts")
def get_loadouts() -> Dict[str, Any]:
    from daimon.mcp.server import dm_loadout_list
    return _call_tool(dm_loadout_list)


@router.get("/api/loadout/{name}")
def get_loadout(name: str) -> Dict[str, Any]:
    from daimon.mcp.server import dm_loadout_load
    out = _call_tool(dm_loadout_load, name=name)
    if out.get("error") == "unknown_loadout":
        raise HTTPException(status_code=404, detail=out)
    return out


class LoadoutSaveBody(BaseModel):
    """Save a loadout. Two shapes accepted:

    * ``card_ids: ["aegis_lion", ...]`` — frontend's preferred path; the
      catalog resolves them to full card payloads server-side.
    * ``cards: [{full card dict}, ...]`` — for advanced flows that hand-roll
      a team (legacy compatibility with dm_loadout_save).
    """
    cards: Optional[list] = None
    card_ids: Optional[list] = None


@router.post("/api/loadout/{name}")
async def post_loadout(name: str, body: LoadoutSaveBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_loadout_save
    if body.card_ids is not None:
        loadout_payload = {"loadout_id": name, "loadout": body.card_ids}
    elif body.cards is not None:
        loadout_payload = {"cards": body.cards}
    else:
        raise HTTPException(
            status_code=400,
            detail="must provide either `card_ids` or `cards`",
        )
    out = _call_tool(dm_loadout_save, name=name, loadout=loadout_payload)
    if "error" not in out:
        # Broadcast so any open `loadouts` screen (or anyone watching
        # liveStore.seq.loadout) refetches and shows the new state.
        await broadcaster.push({
            "kind": "loadout",
            "action": "save",
            "name": out.get("name") or name,
        })
    return out


@router.post("/api/loadout/{name}/activate")
async def post_loadout_activate(name: str) -> Dict[str, Any]:
    """Designate the named loadout as the active one (used as the player
    deck for ``dm_match_npc`` calls without an explicit loadout)."""
    from daimon.mcp.server import dm_loadout_set
    out = _call_tool(dm_loadout_set, name=name)
    if out.get("error") == "unknown_loadout":
        raise HTTPException(status_code=404, detail=out)
    if out.get("error") == "invalid_name":
        raise HTTPException(status_code=400, detail=out)
    await broadcaster.push({
        "kind": "loadout",
        "action": "activate",
        "name": out.get("active_loadout") or name,
    })
    return out


@router.delete("/api/loadout/{name}")
async def delete_loadout(name: str) -> Dict[str, Any]:
    from daimon.identity.keys import CONFIG_DIR
    from daimon.mcp.server import _validate_loadout_name

    try:
        safe = _validate_loadout_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    target = CONFIG_DIR / "loadouts" / f"{safe}.json"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"unknown loadout {safe!r}")
    target.unlink()
    await broadcaster.push({
        "kind": "loadout",
        "action": "delete",
        "name": safe,
    })
    return {"status": "ok", "deleted": safe}


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------

@router.get("/api/npcs")
def get_npcs(tier: Optional[str] = None) -> Dict[str, Any]:
    from daimon.mcp.server import dm_npcs
    return _call_tool(dm_npcs, tier=tier)


@router.get("/api/npc/{npc_id}")
def get_npc(npc_id: str) -> Dict[str, Any]:
    from daimon.mcp.server import dm_npc
    out = _call_tool(dm_npc, npc_id=npc_id)
    if out.get("error") == "unknown_npc":
        raise HTTPException(status_code=404, detail=out)
    return out


@router.get("/api/match/recommended")
def get_match_recommended() -> Dict[str, Any]:
    """Pull the recommended NPC from dm_home — same source the menu uses."""
    home = _call_home_safe()
    return {"recommended_npc": home.get("recommended_npc")}


def _call_home_safe() -> Dict[str, Any]:
    from daimon.mcp.server import dm_home
    try:
        return _call_tool(dm_home)
    except Exception:  # noqa: BLE001
        return {"error": "internal_error", "message": "failed to load home data"}


class MatchStartBody(BaseModel):
    npc_id: str
    loadout: Optional[str] = None  # name of saved loadout; None → active default


@router.post("/api/match/start")
async def post_match_start(body: MatchStartBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_loadout_load, dm_match_npc

    loadout_payload: Optional[Dict[str, Any]] = None
    if body.loadout:
        # Resolve the named saved loadout into the {cards: [...]} payload
        # that dm_match_npc expects. A bare name string would be misread
        # as a loadout-payload by the engine loader.
        loaded = _call_tool(dm_loadout_load, name=body.loadout)
        if loaded.get("error"):
            return loaded
        loadout_payload = {"cards": loaded.get("cards", [])}

    out = _call_tool(
        dm_match_npc,
        npc_id=body.npc_id,
        loadout=loadout_payload,
        include_transcript=True,
    )
    if "error" not in out:
        await broadcaster.push({
            "kind": "match",
            "winner": out.get("winner"),
            "state_id": out.get("state_id"),
        })
    return out


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

@router.post("/api/pull")
async def post_pull() -> Dict[str, Any]:
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.pity import get_pity_state
    from daimon.pulls import perform_pull

    try:
        receipt = perform_pull()
    except FileNotFoundError:
        return {"error": "no_identity", "message": "run `daimon init` first"}
    except InsufficientBalanceError as exc:
        return {"error": "insufficient_balance", "message": str(exc)}
    except RuntimeError as exc:
        return {"error": "ledger_error", "message": str(exc)}

    out = receipt.to_dict()
    out["status"] = "ok"
    await broadcaster.push({
        "kind": "pull",
        "balance": out.get("balance_after"),
        "card_id": out.get("card_id"),
        "rarity": out.get("rarity"),
    })
    pity = get_pity_state()
    out["pity"] = pity
    return out


class MultiPullBody(BaseModel):
    count: int = Field(10, ge=1, le=10)


@router.post("/api/pull/multi")
async def post_pull_multi(body: MultiPullBody) -> Dict[str, Any]:
    from daimon.pulls import perform_multi_pull
    from daimon.mining.ledger import InsufficientBalanceError
    from daimon.pity import get_pity_state

    try:
        receipts = perform_multi_pull(count=body.count)
    except FileNotFoundError:
        return {"error": "no_identity", "message": "run `daimon init` first"}
    except RuntimeError as exc:
        return {"error": "ledger_error", "message": str(exc)}
    except InsufficientBalanceError as exc:
        return {"error": "insufficient_balance", "message": str(exc)}

    results = [r.to_dict() for r in receipts]
    if results:
        await broadcaster.push({
            "kind": "multi_pull",
            "count": len(results),
            "balance": results[-1].get("balance_after"),
        })
    pity = get_pity_state()
    return {"receipts": results, "count": len(results), "pity": pity}


@router.get("/api/pull/pity")
def get_pull_pity() -> Dict[str, Any]:
    from daimon.pity import get_pity_state
    from daimon.mining.formula import PULL_COST
    from daimon.mining.ledger import get_balance

    pity = get_pity_state()
    balance = get_balance()
    pity["balance"] = balance
    pity["pull_cost"] = PULL_COST
    pity["can_pull"] = balance >= PULL_COST
    pity["can_multi"] = balance >= PULL_COST * 10
    return pity


# ---------------------------------------------------------------------------
# PvP Arena
# ---------------------------------------------------------------------------


class PvpRegisterBody(BaseModel):
    handle: Optional[str] = Field(None, max_length=32)


class PvpChallengeBody(BaseModel):
    opponent_pubkey: str = Field(
        ..., min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$",
    )
    loadout_name: str = Field(..., max_length=48)
    memo: Optional[str] = Field(None, max_length=280)


class PvpAcceptBody(BaseModel):
    challenge_id: str = Field(..., max_length=16, pattern=r"^\d+$")
    loadout_name: str = Field(..., max_length=48)


class PvpRevealBody(BaseModel):
    challenge_id: str = Field(..., max_length=16, pattern=r"^\d+$")


@router.get("/api/pvp/leaderboard")
def get_pvp_leaderboard(limit: int = Query(25, ge=1, le=100)) -> Dict[str, Any]:
    from daimon.mcp.server import dm_leaderboard
    return _call_tool(dm_leaderboard, limit=limit)


@router.get("/api/pvp/my-rank")
def get_pvp_my_rank() -> Dict[str, Any]:
    from daimon.mcp.server import dm_my_rank
    return _call_tool(dm_my_rank)


@router.get("/api/pvp/matches")
def get_pvp_matches(limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    from daimon.mcp.server import dm_pvp_my_matches
    return _call_tool(dm_pvp_my_matches, limit=limit)


@router.get("/api/pvp/status/{challenge_id}")
def get_pvp_status(challenge_id: str) -> Dict[str, Any]:
    from daimon.mcp.server import dm_pvp_status
    return _call_tool(dm_pvp_status, challenge_id=challenge_id)


@router.post("/api/pvp/register")
def post_pvp_register(body: PvpRegisterBody) -> Dict[str, Any]:
    _ISSUE_CREATE_LIMITER.acquire_or_raise()
    from daimon.mcp.server import dm_register
    return _call_tool(dm_register, handle=body.handle)


@router.post("/api/pvp/challenge")
def post_pvp_challenge(body: PvpChallengeBody) -> Dict[str, Any]:
    _ISSUE_CREATE_LIMITER.acquire_or_raise()
    from daimon.mcp.server import dm_loadout_load, dm_pvp_challenge
    loaded = _call_tool(dm_loadout_load, name=body.loadout_name)
    if loaded.get("error"):
        return loaded
    loadout_payload = {"cards": loaded.get("cards", [])}
    return _call_tool(
        dm_pvp_challenge,
        opponent_pubkey=body.opponent_pubkey,
        loadout=loadout_payload,
        memo=body.memo,
    )


@router.post("/api/pvp/accept")
def post_pvp_accept(body: PvpAcceptBody) -> Dict[str, Any]:
    _COMMENT_LIMITER.acquire_or_raise()
    from daimon.mcp.server import dm_loadout_load, dm_pvp_accept
    loaded = _call_tool(dm_loadout_load, name=body.loadout_name)
    if loaded.get("error"):
        return loaded
    loadout_payload = {"cards": loaded.get("cards", [])}
    return _call_tool(
        dm_pvp_accept,
        challenge_id=body.challenge_id,
        loadout=loadout_payload,
    )


@router.post("/api/pvp/reveal")
def post_pvp_reveal(body: PvpRevealBody) -> Dict[str, Any]:
    _COMMENT_LIMITER.acquire_or_raise()
    from daimon.mcp.server import dm_pvp_reveal
    return _call_tool(dm_pvp_reveal, challenge_id=body.challenge_id)


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------

@router.get("/art/{card_id}")
def get_art(card_id: str) -> FileResponse:
    """Serve a card's PNG. JIT-fetches via the lazy art pipeline if needed."""
    if not _CARD_ID_RE.match(card_id):
        raise HTTPException(status_code=400, detail="invalid card_id")
    from daimon.cards import art_path_for
    from daimon.update.lazy import ensure_art_for
    ensure_art_for(card_id)
    path = art_path_for(card_id)
    if path is None:
        raise HTTPException(status_code=404, detail="no art found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Dev surface — agent-driven UI navigation
# ---------------------------------------------------------------------------
#
# Lets a CLI / test driver navigate the running pywebview window without
# manual clicking. Bound to 127.0.0.1 like the rest of the API; the
# screen + params allowlists below are belt-and-braces against the
# evaluate_js call (string-interpolated JS, so we want strict input).

_ALLOWED_SCREENS = frozenset({
    "menu", "shop", "collection", "loadouts", "pull", "match", "pvp", "stats",
})
_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class GotoBody(BaseModel):
    screen: str
    params: Optional[List[str]] = None


class EvalBody(BaseModel):
    js: str


def _require_session_token(token: str) -> None:
    from daimon.web.server import get_session_token
    if not secrets.compare_digest(token, get_session_token()):
        raise HTTPException(status_code=403, detail="invalid session token")


@router.get("/api/_dev/token")
def get_dev_token() -> Dict[str, Any]:
    """Return the per-process session token.

    Only reachable from same-origin requests (the _LocalOriginMiddleware
    blocks cross-origin callers), so a malicious website cannot obtain the
    token even by probing localhost ports.
    """
    from daimon.web.server import get_session_token
    return {"token": get_session_token()}


@router.post("/api/_dev/eval")
def post_dev_eval(
    body: EvalBody,
    x_session_token: str = Header(...),
) -> Dict[str, Any]:
    """Run a JS expression in the live pywebview window.

    Requires the per-process session token (obtain via GET /api/_dev/token).
    """
    _require_session_token(x_session_token)
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        raise HTTPException(status_code=503, detail="pywebview unavailable")
    windows = list(getattr(webview, "windows", []) or [])
    if not windows:
        raise HTTPException(status_code=503, detail="no pywebview window open")
    try:
        result = windows[0].evaluate_js(body.js)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="evaluate_js failed")
    return {"result": result}


@router.post("/api/_dev/goto")
def post_dev_goto(
    body: GotoBody,
    x_session_token: str = Header(...),
) -> Dict[str, Any]:
    """Navigate the live pywebview window to ``#<screen>[/<param>...]``."""
    _require_session_token(x_session_token)
    if body.screen not in _ALLOWED_SCREENS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown screen {body.screen!r}; "
                   f"allowed: {sorted(_ALLOWED_SCREENS)}",
        )
    params = body.params or []
    for p in params:
        if not _PARAM_RE.match(p):
            raise HTTPException(
                status_code=400,
                detail=f"param {p!r} contains forbidden chars; "
                       f"allowed: [A-Za-z0-9_-]",
            )

    hash_val = "#" + "/".join([body.screen, *params])

    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="pywebview is not available in this process",
        )
    windows = list(getattr(webview, "windows", []) or [])
    if not windows:
        raise HTTPException(
            status_code=503,
            detail="no pywebview window open (daemon may be in browser-fallback mode)",
        )

    # Set the hash and force a hashchange dispatch — same-hash navigation
    # otherwise wouldn't re-trigger the router. Inputs already passed
    # the allowlist regex above, so this string interpolation is safe.
    js = (
        f"(function(){{"
        f"  var h = {json.dumps(hash_val)};"
        f"  if (location.hash === h) {{"
        f"    window.dispatchEvent(new HashChangeEvent('hashchange'));"
        f"  }} else {{"
        f"    location.hash = h;"
        f"  }}"
        f"  return location.hash;"
        f"}})()"
    )
    try:
        result = windows[0].evaluate_js(js)
    except Exception:  # noqa: BLE001 — pywebview wraps platform errors
        raise HTTPException(status_code=500, detail="evaluate_js failed")
    return {"status": "ok", "hash": hash_val, "window_hash": result}


# ---------------------------------------------------------------------------
# WebSocket — live updates
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await broadcaster.connect(ws)
    try:
        # Send an initial hello so the client knows the channel is live.
        await ws.send_text(json.dumps({"kind": "hello"}))
        # Keep the socket open until the client closes — we never expect
        # client→server messages in Phase 2 (one-way push).
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.disconnect(ws)
