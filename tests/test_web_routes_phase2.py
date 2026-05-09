"""Phase 2 route tests — shop, collection, pull, and the live WebSocket.

These exercise the FastAPI surface that the new web frontend talks to.
All routes delegate to MCP tools, so we mostly assert that the envelope
shapes match (status/error keys), state-mutating routes have side
effects, and the WebSocket pushes the right messages on POST writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared isolation: ports identity / collection / ledger / shop into tmp_path.
# Mirrors the patterns in tests/test_pulls.py + tests/test_shop.py.
# ---------------------------------------------------------------------------

def _png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da63fa0f00000100015b8c1d480000000049454e44ae426082"
    )


def _write_card(art_root: Path, card_id: str, skins: List[dict]) -> None:
    card_dir = art_root / card_id
    card_dir.mkdir(parents=True, exist_ok=True)
    variants = [{
        "id": "v0", "seed": 0, "seed_offset": 0,
        "created_at": "2026-04-23T00:00:00Z",
        "status": "active",
        "model": "test", "prompt_version": "test_v1",
    }]
    (card_dir / "variants").mkdir(exist_ok=True)
    (card_dir / "variants" / "v0.png").write_bytes(_png_bytes())
    (card_dir / "base.png").write_bytes(_png_bytes())
    for i, sk in enumerate(skins, start=1):
        vid = f"v{i}"
        variants.append({
            "id": vid, "seed": 100 + i, "seed_offset": 0,
            "created_at": "2026-04-24T00:00:00Z",
            "status": "active",
            "model": "test", "prompt_version": "skin_v1_2axis",
            "kind": "skin",
            "skin_slug": sk["slug"],
            "skin_name": sk["name"],
            "skin_axis": sk["axis"],
            "rarity": sk["rarity"],
        })
        (card_dir / "variants" / f"{vid}.png").write_bytes(_png_bytes())
    (card_dir / "manifest.json").write_text(json.dumps({
        "card_id": card_id,
        "canonical": "v0",
        "variants": variants,
    }, indent=2), encoding="utf-8")


def _build_synthetic_pack(pack_root: Path, *, rare: int = 6, super_rare: int = 4) -> None:
    pack_root.mkdir(parents=True, exist_ok=True)
    for i in range(rare):
        _write_card(pack_root, f"card_rare_{i:02d}", [
            {"slug": f"cultural_{i:02d}", "name": f"Cultural {i:02d}",
             "axis": "cultural", "rarity": "rare"},
        ])
    for i in range(super_rare):
        _write_card(pack_root, f"card_super_{i:02d}", [
            {"slug": f"anatomical_{i:02d}", "name": f"Anatomical {i:02d}",
             "axis": "anatomical", "rarity": "super_rare"},
        ])


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Full path isolation + minted identity + tiny art-pack."""
    from daimon import collection as collection_mod
    from daimon.identity import generate_identity
    from daimon.identity import keys as identity_keys
    from daimon.mcp import server as mcp_server
    from daimon.mining import buffer as buffer_mod
    from daimon.mining import formula as formula_mod
    from daimon.mining import ledger as ledger_mod
    from daimon.shop import equipped as equipped_mod
    from daimon.shop import owned as owned_mod

    cfg = tmp_path / "config"
    cfg.mkdir()

    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", cfg / "loadouts")
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(formula_mod, "_NOVELTY_MEMORY", {})
    monkeypatch.setattr(owned_mod, "OWNED_PATH", cfg / "owned_skins.json")
    monkeypatch.setattr(equipped_mod, "EQUIPPED_PATH", cfg / "equipped_skins.json")
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))

    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))
    pack_root = tmp_path / "art" / "v1_alpha"
    _build_synthetic_pack(pack_root)

    generate_identity(force=True)

    return {"cfg": cfg, "pack": pack_root}


def _fund(amount: int) -> None:
    """Mine `amount` ¤ into the ledger (in 50-unit chunks to stay under the cap)."""
    from daimon.mining.ledger import append_mine_entry
    i = 0
    while amount > 0:
        chunk = min(amount, 50)
        append_mine_entry(
            tool_name="Edit", amount=chunk, factors={},
            novelty_key=f"k{i}", idempotency_key=f"ik{i}",
        )
        amount -= chunk
        i += 1


def _client():
    from daimon.web.server import create_app
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# /api/shop
# ---------------------------------------------------------------------------

def test_shop_list_returns_slots_with_flat_listing_fields(env):
    r = _client().get("/api/shop")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    # slot_count matches len(slots) and is at least 1.
    assert body.get("slot_count") == len(body.get("slots", []))
    assert body["slot_count"] >= 1
    # Every slot has the keys the frontend reads off it (flat — listing
    # fields are spread into the slot dict by RotationSlot.to_dict()).
    for s in body["slots"]:
        assert "index" in s
        assert "cost" in s
        assert s["card_id"]
        assert s["skin_slug"]
        assert s["rarity"] in ("rare", "super_rare")


def test_shop_refresh_returns_seconds_until_rotation(env):
    r = _client().get("/api/shop/refresh")
    assert r.status_code == 200
    body = r.json()
    assert "seconds_until_rotation" in body
    assert isinstance(body["seconds_until_rotation"], int)
    assert 0 < body["seconds_until_rotation"] <= 24 * 3600


def test_shop_buy_insufficient_returns_envelope_not_500(env):
    """Frontend reads `error` off a 200 body; we MUST NOT 500 here."""
    r = _client().post("/api/shop/buy/0")
    assert r.status_code == 200
    body = r.json()
    assert body.get("error") == "insufficient_balance"


def test_shop_buy_happy_path_drops_balance_and_marks_sold(env):
    _fund(2000)  # super_rare slots cost 1500; rare cost 600 — fund either.
    cli = _client()
    pre = cli.get("/api/shop").json()
    slot_idx = next(s["index"] for s in pre["slots"] if not s["sold"])
    pre_balance = pre["balance"]
    cost = next(s["cost"] for s in pre["slots"] if s["index"] == slot_idx)

    r = cli.post(f"/api/shop/buy/{slot_idx}")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body["balance_after"] == pre_balance - cost

    # And the slot now reads as sold.
    post = cli.get("/api/shop").json()
    assert post["slots"][slot_idx]["sold"] is True


# ---------------------------------------------------------------------------
# /api/collection
# ---------------------------------------------------------------------------

def test_collection_empty_envelope_before_any_pull(env):
    r = _client().get("/api/collection")
    assert r.status_code == 200
    body = r.json()
    # Fresh install path returns an `error: no_collection` envelope with empty arrays.
    assert body.get("count") == 0
    assert body.get("serials") == []
    assert body.get("by_card") == []


def test_collection_includes_owned_serial_after_pull(env):
    _fund(150)
    pull = _client().post("/api/pull").json()
    assert pull.get("status") == "ok"

    coll = _client().get("/api/collection").json()
    assert coll.get("status") == "ok"
    assert coll["count"] == 1
    assert any(s["serial"] == pull["serial"] for s in coll["serials"])


# ---------------------------------------------------------------------------
# /api/pull
# ---------------------------------------------------------------------------

def test_pull_no_balance_returns_insufficient_envelope(env):
    r = _client().post("/api/pull")
    assert r.status_code == 200
    body = r.json()
    assert body.get("error") == "insufficient_balance"
    assert "message" in body


def test_pull_happy_path_returns_full_receipt(env):
    _fund(150)
    r = _client().post("/api/pull")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    # Receipt shape — what pull.js reads off it.
    assert body["card_id"]
    assert body["rarity"]
    assert "balance_after" in body
    # `serial` is a flat UUID string on the receipt — pull.js reads it raw.
    assert isinstance(body["serial"], str)
    assert len(body["serial"]) > 10


# ---------------------------------------------------------------------------
# /art/{card_id}
# ---------------------------------------------------------------------------

def test_art_route_serves_card_from_synthetic_pack(env):
    r = _client().get("/art/card_rare_00")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# WebSocket /ws — live broadcast
# ---------------------------------------------------------------------------

def test_ws_sends_hello_on_connect(env):
    cli = _client()
    with cli.websocket_connect("/ws") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["kind"] == "hello"


def test_ws_broadcasts_pull_events(env):
    """A successful POST /api/pull must push a `pull` event to live sockets."""
    _fund(150)
    cli = _client()
    with cli.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["kind"] == "hello"
        # Trigger a pull while the socket is open.
        r = cli.post("/api/pull")
        assert r.json().get("status") == "ok"
        evt = json.loads(ws.receive_text())
        assert evt["kind"] == "pull"
        assert evt["card_id"]
        assert "balance" in evt
