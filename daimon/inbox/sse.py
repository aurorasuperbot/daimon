"""Pure-stdlib SSE consumer.

The webapp's ``/api/events/stream`` endpoint is plain
``Content-Type: text/event-stream`` — one event per ``data:`` line,
events separated by blank lines, comments start with ``:``. Spec:
https://html.spec.whatwg.org/multipage/server-sent-events.html

We don't need the full client (no Last-Event-ID resume, no automatic
reconnect, no event-type dispatch — we only listen for the default
``message`` event). So instead of pulling in ``httpx`` or ``sseclient``,
we parse it inline with ``urllib.request`` + a line iterator.

## Why stdlib

DAIMON's runtime deps are deliberately small (``pydantic``, ``click``,
``rich``, ``textual``, ``Pillow``, ``mnemonic``, ``cryptography``,
``watchdog``). Adding ``httpx`` for one feature would push install
size up for users who never run the watcher loop. ``urllib.request``
is in stdlib and gets the job done.

## Design notes

  * ``stream_events(url, …, timeout)`` is a *generator* — yield-on-arrival
    semantics so the MCP tool can return as soon as a relevant message
    lands rather than waiting for the full timeout window.
  * ``timeout`` is a SOCKET timeout, not a wall-clock deadline. We
    pass it to ``urlopen`` so ``read()`` will raise
    ``socket.timeout`` after N seconds of silence. The 15-second
    server keep-alive comments (``: keepalive\\n\\n``) keep the
    socket from going silent during normal operation.
  * Caller wraps the generator in their own wall-clock deadline
    (see ``daimon/inbox/wait.py``).

## Failure modes

  * Network error / timeout / HTTP 5xx → raises ``SSEClosed`` with a
    ``reason``. Caller logs + retries on its own cadence.
  * HTTP 401/403 → raises ``SSEClosed(reason="auth_failed")`` so the
    MCP tool surfaces a clear "rotate your token" hint.
  * Malformed event frame → silently skipped (logged at DEBUG).
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSEEvent:
    """One parsed event off the stream.

    ``data`` is whatever JSON the server sent in the ``data:`` line.
    Almost always a dict, but we tolerate scalars / arrays for
    forward compatibility (e.g. heartbeat scalars, batched arrays).
    """

    data: Any
    event_type: str = "message"


class SSEClosed(RuntimeError):
    """Raised when the SSE stream ends or fails to open.

    The ``reason`` attribute is a short slug suitable for surfacing in
    a structured envelope (``error: <reason>``). ``detail`` carries the
    full message for logs.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _parse_data_line(payload: str) -> Optional[Any]:
    """Decode the ``data:`` payload as JSON; return None on malformed."""
    payload = payload.strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        logger.debug("sse: malformed JSON in data: %s", e)
        return None


def stream_events(
    url: str,
    *,
    auth_header: str,
    timeout: float = 30.0,
    user_agent: str = "daimon-inbox/0.1",
) -> Iterator[SSEEvent]:
    """Yield ``SSEEvent`` objects as they arrive on the stream.

    Connects, raises ``SSEClosed`` on auth/transport errors, then
    iterates events until the server closes the connection (in which
    case the generator simply returns).

    The caller is expected to wrap the generator in a wall-clock
    deadline if it needs an upper bound on total time spent — the
    ``timeout`` parameter only governs socket read silence.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/event-stream",
            "Authorization": auth_header,
            "User-Agent": user_agent,
            "Cache-Control": "no-cache",
        },
        method="GET",
    )

    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SSEClosed("auth_failed",
                            f"HTTP {e.code} from SSE endpoint") from e
        raise SSEClosed("http_error",
                        f"HTTP {e.code}: {e.reason}") from e
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise SSEClosed("transport", f"could not reach SSE endpoint: {e}") from e

    # Drain frames as they arrive. The SSE event boundary is a blank
    # line — accumulate ``data:`` continuation lines until we hit one,
    # then yield the assembled event.
    try:
        with response:
            data_buf: list[str] = []
            event_type = "message"
            for raw in response:
                # urlopen returns bytes on Python 3 — decode lenient.
                line = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, (bytes, bytearray))
                    else raw
                )
                # Trim trailing newline only — leading whitespace inside
                # a JSON payload is meaningful and `\r` could appear in
                # CRLF transports.
                line = line.rstrip("\r\n")

                if line == "":
                    # Event boundary. Emit if we've accumulated data.
                    if data_buf:
                        payload = "\n".join(data_buf)
                        parsed = _parse_data_line(payload)
                        if parsed is not None:
                            yield SSEEvent(data=parsed, event_type=event_type)
                    data_buf = []
                    event_type = "message"
                    continue

                if line.startswith(":"):
                    # Comment line (server keep-alives use ``: keepalive``).
                    continue

                if line.startswith("data:"):
                    # ``data:<space?>payload`` per spec.
                    body = line[len("data:"):]
                    if body.startswith(" "):
                        body = body[1:]
                    data_buf.append(body)
                    continue

                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip() or "message"
                    continue
                # ``id:`` and ``retry:`` would land here too — ignored
                # for V1 (we don't do Last-Event-ID resume yet).
    except (socket.timeout, OSError) as e:
        raise SSEClosed("read_timeout",
                        f"SSE socket idle longer than {timeout}s: {e}") from e


def is_chat_message(event: SSEEvent) -> bool:
    """True if ``event.data`` looks like a ``chat_message`` payload.

    Defensive against the stream evolving — we explicitly check the
    type slug rather than assuming every message-shaped event is a
    chat message. Keeps the watcher loop forward-compatible with
    new event types (alerts, presence, etc.) the webapp may add.
    """
    data = event.data
    return (
        isinstance(data, dict)
        and data.get("type") == "chat_message"
        and isinstance(data.get("id"), int)
    )
