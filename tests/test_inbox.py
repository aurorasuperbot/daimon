"""Tests for the DAIMON inbox — SSE consumer + cursor + mention waiter
+ MCP tools.

Strategy: keep the network out of the unit tests. ``stream_events`` is
the only function that talks to the wire — tests inject a fake iterator
via monkeypatch so the upper layers (``wait_for_mentions``, MCP tools)
exercise their full logic without ever touching urllib.

The SSE-parser layer IS unit-tested on its own with a fake byte stream
(see ``TestSSEParser`` — we patch ``urllib.request.urlopen`` with a
``BytesIO`` of pre-canned event-stream bytes).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Iterator, List

import pytest

from daimon.inbox import (
    InboxConfig,
    MentionMessage,
    SSEClosed,
    SSEEvent,
    cursor as cursor_mod,
    sse as sse_mod,
    wait as wait_mod,
)
from daimon.inbox.config import (
    DEFAULT_CHANNEL,
    DEFAULT_WEBAPP_URL,
    ConfigError,
    load_config,
)
from daimon.inbox.cursor import NO_CURSOR, get_last_acked, set_last_acked
from daimon.inbox.wait import _match_mention, wait_for_mentions

from daimon.mcp.server import dm_inbox_ack, dm_inbox_status, dm_inbox_wait
from tests.test_mcp import _call


# ===========================================================================
# Config
# ===========================================================================

class TestInboxConfig:
    def test_explicit_args_win(self, monkeypatch):
        monkeypatch.delenv("DAIMON_WEBAPP_TOKEN", raising=False)
        monkeypatch.delenv("DAIMON_WEBAPP_URL", raising=False)
        cfg = load_config(webapp_url="https://example.test", token="t",
                          channel="c")
        assert cfg.webapp_url == "https://example.test"
        assert cfg.token == "t"
        assert cfg.channel == "c"

    def test_env_vars_win_over_defaults(self, monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_URL", "https://env.test")
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "envtoken")
        monkeypatch.setenv("DAIMON_WEBAPP_CHANNEL", "envchan")
        cfg = load_config()
        assert cfg.webapp_url == "https://env.test"
        assert cfg.token == "envtoken"
        assert cfg.channel == "envchan"

    def test_token_file_used_when_token_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DAIMON_WEBAPP_TOKEN", raising=False)
        token_file = tmp_path / "tok.key"
        token_file.write_text("filetoken\n")
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN_FILE", str(token_file))
        cfg = load_config()
        assert cfg.token == "filetoken"

    def test_no_token_raises_config_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DAIMON_WEBAPP_TOKEN", raising=False)
        monkeypatch.delenv("DAIMON_WEBAPP_TOKEN_FILE", raising=False)
        # Redirect the fallback file to a path that doesn't exist so we
        # don't accidentally read the real /opt/agents/secrets/ file.
        monkeypatch.setattr(
            "daimon.inbox.config._FALLBACK_TOKEN_FILE",
            tmp_path / "nonexistent.key",
        )
        with pytest.raises(ConfigError):
            load_config()

    def test_stream_url_strips_trailing_slash(self):
        cfg = InboxConfig(webapp_url="https://x/", token="t",
                          channel="group")
        assert cfg.stream_url == "https://x/api/events/stream"

    def test_auth_header_includes_bearer_prefix(self):
        cfg = InboxConfig(webapp_url="https://x", token="abc",
                          channel="group")
        assert cfg.auth_header == "Bearer abc"

    def test_default_url_constant(self):
        assert DEFAULT_WEBAPP_URL == "https://santiagodcalvo.org"

    def test_default_channel_constant(self):
        assert DEFAULT_CHANNEL == "group"


# ===========================================================================
# Cursor
# ===========================================================================

class TestCursor:
    def test_no_cursor_when_file_missing(self, tmp_path):
        path = tmp_path / "cursor.json"
        assert get_last_acked(path=path) == NO_CURSOR

    def test_no_cursor_when_malformed(self, tmp_path):
        path = tmp_path / "cursor.json"
        path.write_text("not json {{{")
        assert get_last_acked(path=path) == NO_CURSOR

    def test_no_cursor_when_field_missing(self, tmp_path):
        path = tmp_path / "cursor.json"
        path.write_text(json.dumps({"updated_at": "2026-01-01"}))
        assert get_last_acked(path=path) == NO_CURSOR

    def test_no_cursor_when_field_non_int(self, tmp_path):
        path = tmp_path / "cursor.json"
        path.write_text(json.dumps({"last_acked_id": "42"}))  # str, not int
        assert get_last_acked(path=path) == NO_CURSOR

    def test_set_then_get(self, tmp_path):
        path = tmp_path / "cursor.json"
        set_last_acked(7, path=path)
        assert get_last_acked(path=path) == 7
        # Persisted file shape
        data = json.loads(path.read_text())
        assert data["last_acked_id"] == 7
        assert "updated_at" in data

    def test_monotonic_no_op_when_lower(self, tmp_path):
        path = tmp_path / "cursor.json"
        set_last_acked(10, path=path)
        set_last_acked(5, path=path)
        assert get_last_acked(path=path) == 10  # didn't regress

    def test_monotonic_no_op_when_equal(self, tmp_path):
        path = tmp_path / "cursor.json"
        set_last_acked(10, path=path)
        # Capture mtime before — monotonic no-op should NOT rewrite the file.
        mtime_before = path.stat().st_mtime_ns
        set_last_acked(10, path=path)
        assert path.stat().st_mtime_ns == mtime_before

    def test_advances_when_higher(self, tmp_path):
        path = tmp_path / "cursor.json"
        set_last_acked(1, path=path)
        set_last_acked(2, path=path)
        set_last_acked(3, path=path)
        assert get_last_acked(path=path) == 3

    def test_set_non_int_ignored(self, tmp_path, caplog):
        path = tmp_path / "cursor.json"
        set_last_acked("oops", path=path)  # type: ignore[arg-type]
        assert not path.exists()


# ===========================================================================
# SSE parser — fed via fake urlopen
# ===========================================================================

def _fake_urlopen(body: bytes):
    """Return a callable that produces a BytesIO-with-context-manager response.

    The real urlopen response is iterable (yields lines), supports `with`,
    and exposes `.read()`. urlopen-line iter returns trailing `\n` per line
    by default; BytesIO does the same.
    """

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

    def _open(*args, **kwargs):
        return _FakeResponse(body)

    return _open


class TestSSEParser:
    def test_parses_single_event(self, monkeypatch):
        body = b'data: {"type":"chat_message","id":1,"text":"hi","sender":"user","channel":"group","sender_name":"Santiago"}\n\n'
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(body))
        events = list(sse_mod.stream_events(
            "http://x", auth_header="Bearer t", timeout=1.0,
        ))
        assert len(events) == 1
        assert events[0].data["type"] == "chat_message"
        assert events[0].data["id"] == 1

    def test_parses_multiple_events(self, monkeypatch):
        body = (
            b'data: {"type":"chat_message","id":1,"text":"a","sender":"user","channel":"group","sender_name":""}\n\n'
            b'data: {"type":"chat_message","id":2,"text":"b","sender":"user","channel":"group","sender_name":""}\n\n'
        )
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(body))
        events = list(sse_mod.stream_events("http://x", auth_header="t",
                                            timeout=1.0))
        assert [e.data["id"] for e in events] == [1, 2]

    def test_skips_comment_lines(self, monkeypatch):
        body = (
            b': keepalive\n'
            b'\n'
            b'data: {"type":"chat_message","id":7,"text":"x","sender":"user","channel":"group","sender_name":""}\n\n'
        )
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(body))
        events = list(sse_mod.stream_events("http://x", auth_header="t",
                                            timeout=1.0))
        assert len(events) == 1
        assert events[0].data["id"] == 7

    def test_skips_malformed_data(self, monkeypatch):
        body = (
            b'data: {not valid json}\n\n'
            b'data: {"type":"chat_message","id":3,"text":"y","sender":"user","channel":"group","sender_name":""}\n\n'
        )
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(body))
        events = list(sse_mod.stream_events("http://x", auth_header="t",
                                            timeout=1.0))
        assert len(events) == 1
        assert events[0].data["id"] == 3

    def test_handles_data_with_leading_space(self, monkeypatch):
        # Per spec, "data:value" and "data: value" are equivalent (the
        # single leading space is stripped). We test with the spaced form.
        body = b'data: {"type":"chat_message","id":42,"text":"sp","sender":"user","channel":"group","sender_name":""}\n\n'
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(body))
        events = list(sse_mod.stream_events("http://x", auth_header="t",
                                            timeout=1.0))
        assert events[0].data["id"] == 42

    def test_auth_failure_raises_sseclosed(self, monkeypatch):
        import urllib.error

        def _fail(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://x", 401, "Unauthorized", {}, None,
            )
        monkeypatch.setattr("urllib.request.urlopen", _fail)
        with pytest.raises(SSEClosed) as exc:
            list(sse_mod.stream_events("http://x", auth_header="t",
                                       timeout=1.0))
        assert exc.value.reason == "auth_failed"

    def test_transport_error_raises_sseclosed(self, monkeypatch):
        import urllib.error

        def _fail(*args, **kwargs):
            raise urllib.error.URLError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", _fail)
        with pytest.raises(SSEClosed) as exc:
            list(sse_mod.stream_events("http://x", auth_header="t",
                                       timeout=1.0))
        assert exc.value.reason == "transport"

    def test_is_chat_message_filter(self):
        good = SSEEvent(data={"type": "chat_message", "id": 1})
        not_chat = SSEEvent(data={"type": "agent_status"})
        no_id = SSEEvent(data={"type": "chat_message"})
        not_dict = SSEEvent(data="ping")
        assert sse_mod.is_chat_message(good)
        assert not sse_mod.is_chat_message(not_chat)
        assert not sse_mod.is_chat_message(no_id)
        assert not sse_mod.is_chat_message(not_dict)


# ===========================================================================
# Mention pattern matching
# ===========================================================================

class TestMentionMatching:
    @pytest.mark.parametrize("text", [
        "@daimon home",
        "hey @daimon pull",
        "@DAIMON match-npc Sparring Sam",
        "  @Daimon  please",
        "@daimon",
    ])
    def test_matches(self, text):
        assert _match_mention(text)

    @pytest.mark.parametrize("text", [
        "daimon without at",
        "@daimony random",      # word-bounded — `y` after must not match
        "@daimon123 nope",
        "email me at @daimonexample",
        "",
        None,
    ])
    def test_does_not_match(self, text):
        assert not _match_mention(text)


# ===========================================================================
# wait_for_mentions — full plumbing with stubbed stream_events
# ===========================================================================

def _evt(msg_id, text, *, sender="user", channel="group",
         sender_name="Santiago", msg_type="chat_message"):
    return SSEEvent(data={
        "type": msg_type,
        "id": msg_id,
        "sender": sender,
        "sender_name": sender_name,
        "text": text,
        "channel": channel,
    })


def _patch_stream(monkeypatch, events: List[SSEEvent]):
    """Replace stream_events with a generator that yields the canned events."""
    def _fake(*args, **kwargs):
        yield from events
    monkeypatch.setattr(wait_mod, "stream_events", _fake)


def _cfg() -> InboxConfig:
    return InboxConfig(webapp_url="https://x", token="t", channel="group")


class TestWaitForMentions:
    def test_returns_matching_user_mention(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(1, "@daimon home"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert len(out) == 1
        assert out[0].id == 1
        assert out[0].text == "@daimon home"

    def test_skips_non_chat_events(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(1, "@daimon", msg_type="agent_status"),
            _evt(2, "@daimon home"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert [m.id for m in out] == [2]

    def test_skips_other_channels(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(1, "@daimon", channel="dms"),
            _evt(2, "@daimon", channel="group"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert [m.id for m in out] == [2]

    def test_skips_text_without_mention(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(1, "no mention here"),
            _evt(2, "@daimon yes"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert [m.id for m in out] == [2]

    def test_skips_messages_below_cursor(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(5, "@daimon old"),
            _evt(6, "@daimon also old"),
            _evt(7, "@daimon new"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=6)
        assert [m.id for m in out] == [7]

    def test_skips_agent_echoes(self, monkeypatch):
        """Coda + the user's local CC post as `claude_code`. Their messages
        often quote @daimon (e.g. when explaining a button). Don't react to
        our own echoes — only react to genuine user-driven mentions."""
        _patch_stream(monkeypatch, [
            _evt(1, "@daimon home", sender="claude_code"),
            _evt(2, "@daimon home", sender="alpaca_agent"),
            _evt(3, "@daimon home", sender="user"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert [m.id for m in out] == [3]

    def test_max_messages_caps_batch(self, monkeypatch):
        events = [_evt(i, f"@daimon msg {i}") for i in range(1, 11)]
        _patch_stream(monkeypatch, events)
        out = wait_for_mentions(
            timeout_s=1.0, config=_cfg(), cursor=-1, max_messages=3,
        )
        assert len(out) == 3
        assert [m.id for m in out] == [1, 2, 3]

    def test_returns_empty_on_no_matches(self, monkeypatch):
        _patch_stream(monkeypatch, [
            _evt(1, "no mentions"),
            _evt(2, "still none"),
        ])
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert out == []

    def test_transport_error_returns_partial_batch(self, monkeypatch):
        """If the stream dies mid-iteration, we keep whatever we already
        matched. Caller will retry on next call."""
        def _fake(*args, **kwargs):
            yield _evt(1, "@daimon early")
            raise SSEClosed("transport", "kaboom")
        monkeypatch.setattr(wait_mod, "stream_events", _fake)
        out = wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert [m.id for m in out] == [1]

    def test_auth_error_propagates(self, monkeypatch):
        def _fake(*args, **kwargs):
            raise SSEClosed("auth_failed", "401")
            yield  # pragma: no cover
        monkeypatch.setattr(wait_mod, "stream_events", _fake)
        with pytest.raises(SSEClosed) as exc:
            wait_for_mentions(timeout_s=1.0, config=_cfg(), cursor=-1)
        assert exc.value.reason == "auth_failed"


# ===========================================================================
# MCP tool surface
# ===========================================================================

@pytest.fixture
def isolated_inbox(monkeypatch, tmp_path):
    """Redirect cursor + token-file fallback so MCP tests are hermetic."""
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setattr(cursor_mod, "CURSOR_PATH", cursor_path)

    # Ensure the inbox module's re-export sees the patched constant too —
    # daimon.inbox.__init__ exports CURSOR_PATH from cursor at import time.
    import daimon.inbox as inbox_pkg
    monkeypatch.setattr(inbox_pkg, "CURSOR_PATH", cursor_path)

    # Also redirect the fallback file so a missing DAIMON_WEBAPP_TOKEN env
    # doesn't accidentally read /opt/agents/secrets/ during tests.
    monkeypatch.setattr(
        "daimon.inbox.config._FALLBACK_TOKEN_FILE",
        tmp_path / "no_such_file.key",
    )
    # Strip any inherited inbox env so each test starts clean.
    for k in ("DAIMON_WEBAPP_TOKEN", "DAIMON_WEBAPP_TOKEN_FILE",
              "DAIMON_WEBAPP_URL", "DAIMON_WEBAPP_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


class TestDmInboxAck:
    def test_persists_id(self, isolated_inbox):
        result = _call(dm_inbox_ack, message_id=42)
        assert result["status"] == "ok"
        assert result["cursor_after"] == 42
        assert result["advanced"] is True

    def test_monotonic_returns_advanced_false(self, isolated_inbox):
        _call(dm_inbox_ack, message_id=10)
        result = _call(dm_inbox_ack, message_id=5)
        assert result["status"] == "ok"
        assert result["cursor_after"] == 10
        assert result["advanced"] is False

    def test_invalid_input(self, isolated_inbox):
        result = _call(dm_inbox_ack, message_id="not-an-int")
        assert result["error"] == "invalid_input"
        assert "status" not in result


class TestDmInboxStatus:
    def test_no_token_reports_unresolved(self, isolated_inbox):
        result = _call(dm_inbox_status)
        assert result["status"] == "ok"
        assert result["token_resolved"] is False
        assert "token_hint" in result
        # Webapp URL still falls back to the default for UI display
        assert result["webapp_url"] == DEFAULT_WEBAPP_URL
        assert result["channel"] == DEFAULT_CHANNEL

    def test_with_token_reports_redacted_prefix(self, isolated_inbox,
                                                monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "supersecrettoken123")
        result = _call(dm_inbox_status)
        assert result["status"] == "ok"
        assert result["token_resolved"] is True
        # Only the first 6 chars should appear in the redacted prefix.
        assert result["token_prefix"].startswith("supers")
        # The full token MUST NOT be in the response.
        assert "supersecrettoken123" not in json.dumps(result)

    def test_includes_cursor_value(self, isolated_inbox):
        _call(dm_inbox_ack, message_id=99)
        result = _call(dm_inbox_status)
        assert result["cursor"] == 99


class TestDmInboxWait:
    def test_no_token_returns_config_missing(self, isolated_inbox):
        result = _call(dm_inbox_wait, timeout_s=0.1)
        assert result["error"] == "config_missing"
        assert "DAIMON_WEBAPP_TOKEN" in result["hint"]
        assert "status" not in result

    def test_returns_messages_with_cursor_advance(self, isolated_inbox,
                                                  monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "t")
        # Fake the underlying wait_for_mentions to skip the network entirely.
        def _fake_wait(*, timeout_s, config, cursor, max_messages,
                       socket_timeout=30.0):
            return [MentionMessage(id=42, sender="user",
                                   sender_name="Santiago",
                                   text="@daimon home", channel="group")]
        # Patch on the mcp.server module — that's where dm_inbox_wait
        # imported it from at call time. The lazy import inside the tool
        # means we patch via the inbox package.
        import daimon.inbox as inbox_pkg
        monkeypatch.setattr(inbox_pkg, "wait_for_mentions", _fake_wait)

        result = _call(dm_inbox_wait, timeout_s=1.0)
        assert result["status"] == "ok"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["id"] == 42
        assert result["messages"][0]["text"] == "@daimon home"
        assert result["cursor_after"] == 42

    def test_auth_failure_returns_structured_error(self, isolated_inbox,
                                                   monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "t")
        def _fake_wait(*args, **kwargs):
            raise SSEClosed("auth_failed", "401 from server")
        import daimon.inbox as inbox_pkg
        monkeypatch.setattr(inbox_pkg, "wait_for_mentions", _fake_wait)

        result = _call(dm_inbox_wait, timeout_s=1.0)
        assert result["error"] == "auth_failed"
        assert "rotate" in result["hint"].lower()

    def test_transport_failure_returns_empty_with_note(self, isolated_inbox,
                                                      monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "t")
        def _fake_wait(*args, **kwargs):
            raise SSEClosed("transport", "DNS bork")
        import daimon.inbox as inbox_pkg
        monkeypatch.setattr(inbox_pkg, "wait_for_mentions", _fake_wait)

        result = _call(dm_inbox_wait, timeout_s=1.0)
        # Transport errors are NOT errors as far as the watcher loop is
        # concerned — they're "no messages, retry next tick."
        assert result["status"] == "ok"
        assert result["messages"] == []
        assert "transport" in result["note"]

    def test_empty_batch_when_no_matches(self, isolated_inbox, monkeypatch):
        monkeypatch.setenv("DAIMON_WEBAPP_TOKEN", "t")
        def _fake_wait(*args, **kwargs):
            return []
        import daimon.inbox as inbox_pkg
        monkeypatch.setattr(inbox_pkg, "wait_for_mentions", _fake_wait)

        result = _call(dm_inbox_wait, timeout_s=0.1)
        assert result["status"] == "ok"
        assert result["messages"] == []
        # cursor_after stays at the input cursor (NO_CURSOR == -1 here).
        assert result["cursor_after"] == NO_CURSOR
