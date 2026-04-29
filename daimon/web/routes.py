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
  GET  /art/{card_id}         — equipped-skin-aware PNG
  WS   /ws                    — live balance + receipt push (post-pull, post-buy)
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from daimon.web.live import broadcaster


router = APIRouter()


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
def post_skin_equip(body: SkinEquipBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_skin_equip
    return _call_tool(dm_skin_equip, card_id=body.card_id, skin_slug=body.skin_slug)


class SkinUnequipBody(BaseModel):
    card_id: str


@router.post("/api/skin/unequip")
def post_skin_unequip(body: SkinUnequipBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_skin_unequip
    return _call_tool(dm_skin_unequip, card_id=body.card_id)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

@router.get("/api/collection")
def get_collection() -> Dict[str, Any]:
    from daimon.mcp.server import dm_collection
    return _call_tool(dm_collection)


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
    """
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
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
    return {
        "card_id": card.card_id,
        "rarity": card.rarity,
        "pack": card.pack,
        "payload": card.payload,
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
def post_loadout(name: str, body: LoadoutSaveBody) -> Dict[str, Any]:
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
    return _call_tool(dm_loadout_save, name=name, loadout=loadout_payload)


@router.delete("/api/loadout/{name}")
def delete_loadout(name: str) -> Dict[str, Any]:
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
    except Exception as e:  # noqa: BLE001
        return {"error": "internal_error", "message": f"{type(e).__name__}: {e}"}


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

    out = _call_tool(dm_match_npc, npc_id=body.npc_id, loadout=loadout_payload)
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
    from daimon.mcp.server import dm_pull
    out = _call_tool(dm_pull)
    if "error" not in out:
        await broadcaster.push({
            "kind": "pull",
            "balance": out.get("balance_after"),
            "card_id": out.get("card_id"),
            "rarity": out.get("rarity"),
        })
    return out


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------

@router.get("/art/{card_id}")
def get_art(card_id: str) -> FileResponse:
    """Serve a card's PNG. JIT-fetches via the lazy art pipeline if needed.

    First request for a card_id triggers an in-process download from the
    art-pack release (~50–500 KB). The 4-phase pull reveal animation is
    >2s long, so the fetch lands well before the user sees the front of
    the card. Subsequent requests are local-disk reads.

    Returns 404 only when the manifest doesn't list this card OR the fetch
    failed (network down, asset missing). Renderers fall through to a
    placeholder block on 404.
    """
    from daimon.cards import art_path_for
    from daimon.update.lazy import ensure_art_for
    ensure_art_for(card_id)  # noop if already cached; soft-fails on net err
    path = art_path_for(card_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no art for card_id {card_id!r}")
    return FileResponse(path, media_type="image/png")


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
