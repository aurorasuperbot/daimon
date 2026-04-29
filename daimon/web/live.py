"""WebSocket broadcast layer.

A tiny in-process pub/sub used by the FastAPI routes to push balance
updates and receipt notifications to every open window. There's
typically only one window per process (single-instance lock), but the
broadcast set is a generic hook so future multi-window scenarios work
the same way.

Thread-safe: WebSocket sends from FastAPI handlers run on the asyncio
loop; the connect/disconnect callbacks also run there. We hold no locks.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket


logger = logging.getLogger(__name__)


class _Broadcaster:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def push(self, payload: Dict[str, Any]) -> None:
        """Best-effort fan-out. Dead sockets are dropped silently — they'll
        be cleaned up by the WS handler's finally block on the next disconnect."""
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


broadcaster = _Broadcaster()
