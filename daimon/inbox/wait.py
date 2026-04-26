"""High-level mention waiter — the entry point ``dm_inbox_wait`` calls.

Wraps ``stream_events`` with:

  * Wall-clock deadline (``timeout_s`` upper bound on total time).
  * Cursor filter (skip anything ``id <= last_acked``).
  * ``@daimon`` mention pattern match.
  * Channel filter (default ``group``).
  * Early-return on first match (the watcher loop is most responsive
    when wakeups happen at message granularity, not batch).
  * Soft cap on returned messages (``max_messages``) so a chatty
    burst doesn't blow up the agent's context.

## What counts as an ``@daimon`` mention

  Case-insensitive. The token must be word-bounded — ``@daimonyx``
  doesn't count. Both ``@daimon`` and ``@DAIMON`` are accepted.
  The token can appear anywhere in the message body — beginning,
  middle, or end. Future expansion (``@dmn`` short-form, etc.)
  goes through ``_match_mention``.

## Return shape

  ``MentionMessage`` dataclass — small, frozen, JSON-serializable
  via ``asdict``. The MCP tool flattens it into a plain dict before
  returning so the agent doesn't need to know about dataclasses.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from .config import InboxConfig, load_config
from .cursor import NO_CURSOR, get_last_acked
from .sse import SSEClosed, is_chat_message, stream_events

logger = logging.getLogger(__name__)


# Public token — re-exported via ``daimon.inbox.MENTION_TOKEN``. Lowercased
# at compare time, so case in the source doesn't matter.
MENTION_TOKEN = "@daimon"

# Word-bounded so ``@daimony`` doesn't trigger us. Compile once.
# We use a lookahead for the trailing boundary to cover end-of-string and
# any non-word char (whitespace, punctuation). Leading boundary is the
# standard ``\b`` since ``@`` is itself a non-word char.
_MENTION_RE = re.compile(r"@daimon(?![A-Za-z0-9_])", re.IGNORECASE)


@dataclass(frozen=True)
class MentionMessage:
    """One ``@daimon`` mention, normalized for downstream dispatch.

    ``text`` is the FULL chat message (not the trimmed remainder). The
    dispatcher decides what to do with it — V1 grammar parses it, V2
    might hand it to an LLM.
    """

    id: int
    sender: str
    sender_name: str
    text: str
    channel: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "text": self.text,
            "channel": self.channel,
        }


def _match_mention(text: str) -> bool:
    """Return True iff ``text`` contains a word-bounded ``@daimon``."""
    if not isinstance(text, str):
        return False
    return _MENTION_RE.search(text) is not None


def _maybe_message(payload: dict, *, channel: str,
                   min_id: int) -> Optional[MentionMessage]:
    """Promote a raw chat_message dict to a MentionMessage if it qualifies."""
    if payload.get("channel") != channel:
        return None
    msg_id = payload.get("id")
    if not isinstance(msg_id, int) or msg_id <= min_id:
        return None
    text = payload.get("text", "") or ""
    if not _match_mention(text):
        return None

    # Skip messages we ourselves posted — the watcher loop should react
    # only to user-driven mentions, not echoes of replies the agent just
    # sent. Coda + Claude Code both post as ``claude_code`` / agents,
    # never as ``user``. So gate on sender == "user".
    sender = payload.get("sender") or ""
    if sender != "user":
        return None

    return MentionMessage(
        id=msg_id,
        sender=sender,
        sender_name=payload.get("sender_name") or "",
        text=text,
        channel=channel,
    )


def wait_for_mentions(
    *,
    timeout_s: float = 60.0,
    config: Optional[InboxConfig] = None,
    cursor: Optional[int] = None,
    max_messages: int = 10,
    socket_timeout: float = 30.0,
) -> List[MentionMessage]:
    """Block up to ``timeout_s`` for ``@daimon`` mentions, return matches.

    Args:
      timeout_s: Wall-clock upper bound on total time spent. Returns
        an empty list if nothing matches before the deadline.
      config: Pre-resolved ``InboxConfig`` (tests pass this directly).
        Defaults to ``load_config()`` which reads env + filesystem.
      cursor: Skip messages with id ``<=`` this value. Defaults to
        ``get_last_acked()`` so the cursor file is the source of truth.
        Pass an explicit value (e.g. ``-1``) to disable cursor filtering.
      max_messages: Cap on returned mentions per call. Once we hit the
        cap we close the stream and return early — the next ``wait`` call
        will pick up where we left off (after the cursor advances).
      socket_timeout: Per-read socket timeout passed through to
        ``stream_events``. Server keep-alive is 15 s, so anything above
        20 s is safe.

    Raises:
      SSEClosed: For auth / transport errors that the caller should
        surface as structured envelopes (not retry blindly).
    """
    if config is None:
        config = load_config()
    if cursor is None:
        cursor = get_last_acked()

    # Sentinel -1 means "every message is new"; `>` semantics still work.
    min_id = cursor if cursor != NO_CURSOR else -1

    deadline = time.monotonic() + timeout_s
    matched: List[MentionMessage] = []

    try:
        for event in stream_events(
            config.stream_url,
            auth_header=config.auth_header,
            timeout=socket_timeout,
        ):
            # Wall-clock check — the SSE generator only enforces socket
            # silence, so we still need our own deadline.
            if time.monotonic() >= deadline:
                break

            if not is_chat_message(event):
                continue
            mention = _maybe_message(event.data, channel=config.channel,
                                     min_id=min_id)
            if mention is None:
                continue
            matched.append(mention)
            # Advance min_id locally so a burst doesn't return the same
            # message twice if the server happens to re-emit.
            min_id = mention.id
            if len(matched) >= max_messages:
                break
    except SSEClosed as e:
        # Re-raise auth failures; transient transport errors are
        # logged + swallowed (we return whatever we managed to match
        # before the connection died — caller will retry on its own
        # cadence by re-invoking the MCP tool).
        if e.reason == "auth_failed":
            raise
        logger.info("inbox SSE closed (%s): %s", e.reason, e.detail)

    return matched
