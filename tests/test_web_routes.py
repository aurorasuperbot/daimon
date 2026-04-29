"""Tests for daimon/web/routes.py.

Validates the Phase 1 API surface: ``/api/home`` returns the dm_home
envelope, ``/art/{card_id}`` resolves equipped-skin-aware PNGs and
404s when the art is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_paths(monkeypatch, tmp_path):
    """Mirror the standard mcp/cli isolation pattern so dm_home() runs
    against a fresh tmp identity instead of touching the real ~/.config/daimon."""
    cfg = tmp_path / "config"
    cfg.mkdir()

    from daimon.identity import keys as identity_keys
    from daimon.mining import buffer as buffer_mod
    from daimon.mining import ledger as ledger_mod
    from daimon import collection as collection_mod
    from daimon.mcp import server as mcp_server

    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH", cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", cfg / "loadouts")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))

    return cfg


# ---------------------------------------------------------------------------
# /api/home
# ---------------------------------------------------------------------------

def test_api_home_returns_no_identity_envelope_before_init(isolated_paths: Path):
    """When no identity exists yet, dm_home returns the structured no-identity
    envelope. The route must surface that verbatim — not 500."""
    from daimon.web.server import create_app
    client = TestClient(create_app())
    r = client.get("/api/home")
    assert r.status_code == 200
    body = r.json()
    assert body.get("error") == "no_identity"


def test_api_home_returns_full_envelope_after_init(isolated_paths: Path):
    from daimon.identity import generate_identity
    generate_identity()
    from daimon.web.server import create_app
    client = TestClient(create_app())
    r = client.get("/api/home")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert "identity" in body
    assert "balance" in body
    assert "rank" in body
    assert "pull" in body
    assert "daily_quests" in body
    # Pubkey shape: 64 hex chars (ed25519).
    assert len(body["identity"]["pubkey_hex"]) == 64


# ---------------------------------------------------------------------------
# /art/{card_id}
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_art(monkeypatch, tmp_path):
    """Build a tiny synthetic art-pack so /art/<id> has something to serve."""
    art_root = tmp_path / "art" / "v1_alpha"
    art_root.mkdir(parents=True)
    monkeypatch.setenv("DAIMON_ART_DIR", str(tmp_path))

    # Minimal valid PNG — 1x1 transparent.
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da63fa0f00000100015b8c1d480000000049454e44ae426082"
    )
    card_dir = art_root / "aegis_lion"
    card_dir.mkdir()
    (card_dir / "base.png").write_bytes(png_bytes)
    return art_root


def test_art_route_serves_png_for_known_card(isolated_art: Path):
    from daimon.web.server import create_app
    client = TestClient(create_app())
    r = client.get("/art/aegis_lion")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_art_route_404s_for_unknown_card(isolated_art: Path):
    from daimon.web.server import create_app
    client = TestClient(create_app())
    r = client.get("/art/nonexistent_card")
    assert r.status_code == 404


def test_art_route_does_not_shadow_static_index(isolated_art: Path):
    """Phase 1 mounts statics at '/' last — the API routes (incl. /art) must
    not be eaten by the static handler. Verifies the include-router-then-mount
    ordering in server.py."""
    from daimon.web.server import create_app
    client = TestClient(create_app())
    # Static "/" still serves index.html.
    r = client.get("/")
    assert r.status_code == 200
    assert "DAIMON" in r.text
    # And the art route still wins for /art/*.
    r = client.get("/art/aegis_lion")
    assert r.status_code == 200
