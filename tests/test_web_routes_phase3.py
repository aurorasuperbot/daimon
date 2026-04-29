"""Phase 3 route tests — loadout CRUD + match flow.

The loadout endpoints accept either ``card_ids`` (frontend's path; resolved
against the catalog) or full ``cards`` dicts (legacy compatibility). Match
endpoints exercise NPC enumeration + match resolution end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(monkeypatch, tmp_path):
    from daimon import collection as collection_mod
    from daimon.identity import generate_identity
    from daimon.identity import keys as identity_keys
    from daimon.mcp import server as mcp_server
    from daimon.mining import buffer as buffer_mod
    from daimon.mining import formula as formula_mod
    from daimon.mining import ledger as ledger_mod

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
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))

    generate_identity(force=True)
    return cfg


def _client():
    from daimon.web.server import create_app
    return TestClient(create_app())


def _six_card_ids():
    """First 6 ids from the bundled catalog — stable enough for tests."""
    from daimon.catalog import DEFAULT_CATALOG_ID, load_catalog
    cat = load_catalog(DEFAULT_CATALOG_ID)
    return [c.card_id for c in cat.cards[:6]]


# ---------------------------------------------------------------------------
# /api/catalog
# ---------------------------------------------------------------------------

def test_catalog_returns_cards_with_id_and_rarity(env):
    r = _client().get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["pack_id"]
    assert isinstance(body["cards"], list) and body["cards"]
    sample = body["cards"][0]
    assert sample["card_id"]
    assert sample["rarity"]


def test_catalog_unknown_expansion_404s(env):
    r = _client().get("/api/catalog?expansion=nonexistent_pack")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/loadouts CRUD
# ---------------------------------------------------------------------------

def test_loadouts_list_empty_on_fresh_install(env):
    r = _client().get("/api/loadouts")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["loadouts"] == []
    assert body["active_loadout"] is None


def test_loadout_save_via_card_ids_succeeds(env):
    cli = _client()
    ids = _six_card_ids()
    r = cli.post("/api/loadout/test_team", json={"card_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["card_count"] == 6
    assert body["set_active"] is True  # first save auto-activates

    # And the GET round-trips it.
    got = cli.get("/api/loadout/test_team").json()
    assert got["status"] == "ok"
    assert [c["card_id"] for c in got["cards"]] == ids


def test_loadout_save_with_no_payload_400s(env):
    r = _client().post("/api/loadout/empty_team", json={})
    assert r.status_code == 400


def test_loadout_save_invalid_card_id_returns_envelope(env):
    r = _client().post("/api/loadout/bad_team",
                        json={"card_ids": ["not_a_real_card"] * 6})
    assert r.status_code == 200
    assert r.json().get("error") == "invalid_loadout"


def test_loadout_get_unknown_404s(env):
    r = _client().get("/api/loadout/never_saved")
    assert r.status_code == 404


def test_loadout_delete_round_trip(env):
    cli = _client()
    cli.post("/api/loadout/disposable", json={"card_ids": _six_card_ids()})
    assert cli.get("/api/loadout/disposable").status_code == 200

    r = cli.delete("/api/loadout/disposable")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    assert cli.get("/api/loadout/disposable").status_code == 404


def test_loadout_delete_unknown_404s(env):
    r = _client().delete("/api/loadout/never_existed")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/npcs + /api/npc/{id}
# ---------------------------------------------------------------------------

def test_npcs_lists_tiers(env):
    r = _client().get("/api/npcs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    tiers = body.get("tiers", [])
    assert any(t["tier_id"] == "rookie" for t in tiers)


def test_npc_detail_returns_loadout(env):
    r = _client().get("/api/npc/sparring_sam")
    assert r.status_code == 200
    body = r.json()
    assert body["npc_id"] == "sparring_sam"
    assert isinstance(body["loadout"], list) and len(body["loadout"]) == 6
    assert isinstance(body["cards"], list) and len(body["cards"]) == 6


def test_npc_unknown_404s(env):
    r = _client().get("/api/npc/not_a_real_npc_slug")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/match
# ---------------------------------------------------------------------------

def test_match_recommended_envelope(env):
    r = _client().get("/api/match/recommended")
    assert r.status_code == 200
    # The key always exists; value may be None on a fresh install.
    assert "recommended_npc" in r.json()


def test_match_start_no_active_loadout_returns_envelope(env):
    """Pre-save state — match should refuse with no_active_loadout."""
    r = _client().post("/api/match/start", json={"npc_id": "sparring_sam"})
    assert r.status_code == 200
    assert r.json().get("error") == "no_active_loadout"


def test_match_start_with_named_loadout_resolves(env):
    cli = _client()
    cli.post("/api/loadout/team_alpha", json={"card_ids": _six_card_ids()})
    r = cli.post("/api/match/start",
                 json={"npc_id": "sparring_sam", "loadout": "team_alpha"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("winner") in (0, 1, None)
    assert body.get("round_count", 0) >= 0
    assert body.get("npc", {}).get("npc_id") == "sparring_sam"


def test_match_start_unknown_npc_returns_envelope(env):
    r = _client().post("/api/match/start", json={"npc_id": "not_a_real_npc"})
    assert r.status_code == 200
    assert r.json().get("error") == "unknown_npc"
