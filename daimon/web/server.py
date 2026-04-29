"""FastAPI app factory for the local game backend.

Bound to ``127.0.0.1`` only — no CORS, no auth, no telemetry. The server
runs in a daemon thread inside the same process as the pywebview window
(see :mod:`daimon.daemon.entry`).

Layered routing: API routes (``/api/*`` and ``/art/*``) land first, then
the static mount at ``/`` catches everything else (so ``GET /`` returns
``index.html``).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from daimon.web.routes import router


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build the FastAPI app. Idempotent — call once per process."""
    app = FastAPI(title="daimon", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def _health() -> dict:
        return {"status": "ok"}

    app.include_router(router)

    # Static mount serves index.html at "/" plus app.js, /styles/*, /components/*, etc.
    # MUST come last so /api/* and /art/* aren't shadowed.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
