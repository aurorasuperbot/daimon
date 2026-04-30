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
from typing import Any, Dict, List, Optional

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
# PvP Arena
# ---------------------------------------------------------------------------


class PvpRegisterBody(BaseModel):
    handle: Optional[str] = None


class PvpChallengeBody(BaseModel):
    opponent_pubkey: str
    loadout_name: str
    memo: Optional[str] = None


class PvpAcceptBody(BaseModel):
    challenge_id: str
    loadout_name: str


class PvpRevealBody(BaseModel):
    challenge_id: str


@router.get("/api/pvp/leaderboard")
def get_pvp_leaderboard(limit: int = 25) -> Dict[str, Any]:
    from daimon.mcp.server import dm_leaderboard
    return _call_tool(dm_leaderboard, limit=limit)


@router.get("/api/pvp/my-rank")
def get_pvp_my_rank() -> Dict[str, Any]:
    from daimon.mcp.server import dm_my_rank
    return _call_tool(dm_my_rank)


@router.get("/api/pvp/matches")
def get_pvp_matches(limit: int = 20) -> Dict[str, Any]:
    from daimon.mcp.server import dm_pvp_my_matches
    return _call_tool(dm_pvp_my_matches, limit=limit)


@router.get("/api/pvp/status/{challenge_id}")
def get_pvp_status(challenge_id: str) -> Dict[str, Any]:
    from daimon.mcp.server import dm_pvp_status
    return _call_tool(dm_pvp_status, challenge_id=challenge_id)


@router.post("/api/pvp/register")
def post_pvp_register(body: PvpRegisterBody) -> Dict[str, Any]:
    from daimon.mcp.server import dm_register
    return _call_tool(dm_register, handle=body.handle)


@router.post("/api/pvp/challenge")
def post_pvp_challenge(body: PvpChallengeBody) -> Dict[str, Any]:
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
    from daimon.mcp.server import dm_pvp_reveal
    return _call_tool(dm_pvp_reveal, challenge_id=body.challenge_id)


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
# Dev surface — agent-driven UI navigation
# ---------------------------------------------------------------------------
#
# Lets a CLI / test driver navigate the running pywebview window without
# manual clicking. Bound to 127.0.0.1 like the rest of the API; the
# screen + params allowlists below are belt-and-braces against the
# evaluate_js call (string-interpolated JS, so we want strict input).

_ALLOWED_SCREENS = frozenset({
    "menu", "shop", "collection", "loadouts", "pull", "match", "pvp",
})
_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class GotoBody(BaseModel):
    screen: str
    params: Optional[List[str]] = None


class EvalBody(BaseModel):
    js: str


@router.post("/api/_dev/eval")
def post_dev_eval(body: EvalBody) -> Dict[str, Any]:
    """Run an arbitrary JS expression in the live pywebview window.

    Local-only debugging surface — bound to 127.0.0.1, no auth. Used by
    the agent driver to inspect the rendered DOM (resolved CSS variables,
    attribute values, computed styles) without manual devtools poking.
    """
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        raise HTTPException(status_code=503, detail="pywebview unavailable")
    windows = list(getattr(webview, "windows", []) or [])
    if not windows:
        raise HTTPException(status_code=503, detail="no pywebview window open")
    try:
        result = windows[0].evaluate_js(body.js)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"evaluate_js failed: {type(e).__name__}: {e}",
        )
    return {"result": result}


@router.post("/api/_dev/goto")
def post_dev_goto(body: GotoBody) -> Dict[str, Any]:
    """Navigate the live pywebview window to ``#<screen>[/<param>...]``.

    Only available when the daemon owns a real pywebview window — in
    browser-fallback mode we can't reach the renderer process.
    """
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
    except Exception as e:  # noqa: BLE001 — pywebview wraps platform errors
        raise HTTPException(
            status_code=500,
            detail=f"evaluate_js failed: {type(e).__name__}: {e}",
        )
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
