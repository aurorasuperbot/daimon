"""Tests for the FastAPI app factory (daimon/web/server.py).

Phase 0 surface: /health probe + static file mount serving index.html.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_returns_ok():
    from daimon.web.server import create_app
    app = create_app()
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_serves_index_html():
    from daimon.web.server import create_app
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>DAIMON</title>" in r.text


def test_static_assets_served():
    from daimon.web.server import create_app
    client = TestClient(create_app())
    for asset in (
        "/app.js",
        "/store.js",
        "/styles/tokens.css",
        "/styles/base.css",
        "/styles/components/dm-card.css",
        "/styles/components/screen-chrome.css",
        "/components/dm-card.js",
    ):
        r = client.get(asset)
        assert r.status_code == 200, f"{asset} returned {r.status_code}"
