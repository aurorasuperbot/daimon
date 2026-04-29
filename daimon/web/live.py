"""WebSocket broadcast layer.

A tiny in-process pub/sub used by the FastAPI routes to push balance
updates and receipt notifications to the active game window. Only one
client connection is allowed at a time — when a new WebSocket connects,
the previous one is closed with code 4001 ("superseded") so the newest
window always wins.

Thread-safe: WebSocket sends from FastAPI handlers run on the asyncio
loop; the connect/disconnect callbacks also run there. We hold no locks.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket


logger = logging.getLogger(__name__)


class _Broadcaster:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._active: Optional[WebSocket] = None

    async def connect(self, ws: WebSocket) -> None:
        old = self._active
        if old is not None:
            try:
                await old.close(code=4001, reason="superseded")
            except Exception:  # noqa: BLE001
                pass
            self._clients.discard(old)

        await ws.accept()
        self._clients.add(ws)
        self._active = ws

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        if self._active is ws:
            self._active = None

    async def push(self, payload: Dict[str, Any]) -> None:
        """Best-effort fan-out. Dead sockets are dropped silently."""
        if not self._clients:
            return
        encoded = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(encoded)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
            if self._active is ws:
                self._active = None


broadcaster = _Broadcaster()
