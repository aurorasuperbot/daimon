"""DAIMON inbox — long-poll bridge from the LivingAgent webapp chat.

The inbox is the trigger plumbing for the chat home card and any other
``@daimon …`` directives the user posts in the group channel. It lets
the user's local Claude Code session enter a "watcher loop":

    while True:
        msgs = dm_inbox_wait(timeout_s=60)   # blocks up to 60s on SSE
        for m in msgs:
            # parse + dispatch to the right dm_* tool
            reply(handle(m))
        dm_inbox_ack([m["id"] for m in msgs])

## Why long-poll-from-MCP, not a daemon

See ``docs/animation_design.md`` for the broader DAIMON-is-agentic-first
contract. The agent is the primary actor — if Claude isn't running,
nothing on the DAIMON side is running anyway. So instead of a separate
``daimon watch`` daemon (which would need its own LLM in the loop to
parse natural-language ``@daimon`` mentions), we expose the SSE stream
as a long-poll MCP tool. Claude IS the parser; the MCP tool just
delivers raw mentions.

## Architecture (V1)

  ``daimon.inbox.config``  — webapp URL + auth-token resolution
  ``daimon.inbox.cursor``  — last-acked-message-id persistence
  ``daimon.inbox.sse``     — pure-stdlib SSE consumer (no httpx dep)
  ``daimon.inbox.wait``    — high-level ``wait_for_mentions(timeout_s)``
  ``daimon.mcp.server``    — exposes ``dm_inbox_wait`` + ``dm_inbox_ack``

V1 limitations (documented + tested):
  * No "missed while offline" recovery — SSE doesn't replay, and the
    chat history "since" endpoint isn't wired in yet (V2).
  * Single-channel by default ("group"); the constant is exposed in
    ``config`` for users on other deployments.
  * Cursor is for dedup *within* a watcher loop, not across long
    Claude-offline windows.
"""

from __future__ import annotations

from .config import ConfigError, InboxConfig, load_config
from .cursor import (
    CURSOR_PATH,
    get_last_acked,
    set_last_acked,
)
from .sse import SSEClosed, SSEEvent, stream_events
from .wait import MENTION_TOKEN, MentionMessage, wait_for_mentions

__all__ = [
    "CURSOR_PATH",
    "ConfigError",
    "InboxConfig",
    "MENTION_TOKEN",
    "MentionMessage",
    "SSEClosed",
    "SSEEvent",
    "get_last_acked",
    "load_config",
    "set_last_acked",
    "stream_events",
    "wait_for_mentions",
]
