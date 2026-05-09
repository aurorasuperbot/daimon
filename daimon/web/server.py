"""FastAPI app factory for the local game backend.

Bound to ``127.0.0.1`` only — the server runs in a daemon thread inside
the same process as the pywebview window (see :mod:`daimon.daemon.entry`).

Security layers:
  - _LocalOriginMiddleware rejects requests whose ``Origin`` header
    points outside localhost, blocking browser-based cross-origin attacks.
  - _dev endpoints require a per-process session token.
  - All state-mutating PvP routes are rate-limited.

Layered routing: API routes (``/api/*`` and ``/art/*``) land first, then
the static mount at ``/`` catches everything else (so ``GET /`` returns
``index.html``).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from daimon.web.routes import router


STATIC_DIR = Path(__file__).resolve().parent / "static"

_SESSION_TOKEN = secrets.token_hex(32)


def get_session_token() -> str:
    return _SESSION_TOKEN


_LOCALHOST_PREFIXES = (
    "http://127.0.0.1", "http://localhost", "http://[::1]",
)


_CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:;"
)


class _CSPMiddleware(BaseHTTPMiddleware):
    """Attach a Content-Security-Policy header to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP_VALUE
        return response


class _LocalOriginMiddleware(BaseHTTPMiddleware):
    """Reject any request whose Origin header points outside localhost.

    Browsers set the Origin header on all cross-origin requests (including
    preflights). By blocking non-localhost origins at the middleware level,
    we prevent a malicious webpage from reaching ANY endpoint — GET or POST,
    simple or preflighted — even if specific route handlers have no auth.
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")
        if origin and not any(origin.startswith(p) for p in _LOCALHOST_PREFIXES):
            return Response(
                "Forbidden: cross-origin requests not allowed",
                status_code=403,
            )
        response = await call_next(request)
        return response


def create_app() -> FastAPI:
    """Build the FastAPI app. Idempotent — call once per process."""
    app = FastAPI(title="daimon", docs_url=None, redoc_url=None, openapi_url=None)

    app.add_middleware(_CSPMiddleware)
    app.add_middleware(_LocalOriginMiddleware)

    @app.get("/health")
    def _health() -> dict:
        return {"status": "ok"}

    app.include_router(router)

    # Static mount serves index.html at "/" plus app.js, /styles/*, /components/*, etc.
    # MUST come last so /api/* and /art/* aren't shadowed.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
