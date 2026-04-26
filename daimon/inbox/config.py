"""Inbox config — webapp URL + auth token resolution.

Resolved at first use (not at import) so changing env vars between
test cases takes effect without module reload.

## Auth model

The webapp's SSE endpoint accepts EITHER:

  1. ``Authorization: Bearer <internal_api_key>`` — preferred for
     server-to-server calls (VPS-side admin scripts, Santiago's own
     Claude Code session), constant-time-compared on the backend.
  2. ``Authorization: Bearer <jwt_token>`` — what an OAuth-logged-in
     browser session would send. Equivalent for our purposes — just
     a different opaque bearer token.

We don't care which it is — both go in the ``Authorization`` header
unchanged. The user provides one of:

  * ``DAIMON_WEBAPP_TOKEN`` env var (most flexible — works for both
    JWT and internal-key flows).
  * ``DAIMON_WEBAPP_TOKEN_FILE`` env var pointing at a file on disk
    (better for JWTs that get refreshed by an external process).
  * Implicit fallback: read from ``/opt/agents/secrets/internal_api.key``
    (Santiago's VPS deployment convention — won't exist on a normal
    user install, in which case we error clearly).

## Env vars (full surface)

  ``DAIMON_WEBAPP_URL``         default ``https://santiagodcalvo.org``
  ``DAIMON_WEBAPP_TOKEN``       inline bearer token (highest priority)
  ``DAIMON_WEBAPP_TOKEN_FILE``  path to a file containing the token
  ``DAIMON_WEBAPP_CHANNEL``     default ``group``
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Single canonical default, easy to override for tests / non-default deployments.
DEFAULT_WEBAPP_URL = "https://santiagodcalvo.org"
DEFAULT_CHANNEL = "group"

# Where the VPS keeps the rotated internal API key. Falls through to None
# on any normal user install (the file simply won't exist).
_FALLBACK_TOKEN_FILE = Path("/opt/agents/secrets/internal_api.key")


@dataclass(frozen=True)
class InboxConfig:
    """Resolved inbox settings — passed by value to all consumers.

    Frozen so that tests can monkeypatch ``load_config`` to return a
    deterministic snapshot without worrying about later mutation.
    """

    webapp_url: str
    token: str
    channel: str

    @property
    def stream_url(self) -> str:
        """Full URL of the SSE stream endpoint, no trailing slash."""
        return self.webapp_url.rstrip("/") + "/api/events/stream"

    @property
    def auth_header(self) -> str:
        """Value for the ``Authorization`` header — already includes the
        ``Bearer`` prefix so callers don't have to compose it."""
        return f"Bearer {self.token}"


class _ConfigError(RuntimeError):
    """Raised when no auth token can be resolved.

    Surfaced as ``error: config_missing`` in the MCP tool envelope so
    the user sees a clear "set DAIMON_WEBAPP_TOKEN" hint instead of an
    HTTP 401 mystery.
    """


def _read_token_file(path: Path) -> Optional[str]:
    """Read a token from disk, stripped, or None on any failure."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def load_config(
    *,
    webapp_url: Optional[str] = None,
    token: Optional[str] = None,
    channel: Optional[str] = None,
) -> InboxConfig:
    """Resolve inbox config from explicit args + env vars + filesystem fallback.

    Precedence (highest first):
      1. Explicit kwargs (used by tests).
      2. Env vars (``DAIMON_WEBAPP_URL`` / ``DAIMON_WEBAPP_TOKEN`` /
         ``DAIMON_WEBAPP_TOKEN_FILE`` / ``DAIMON_WEBAPP_CHANNEL``).
      3. Filesystem fallback for the token (``/opt/agents/secrets/
         internal_api.key`` — VPS-side convention, not present on a
         normal user install).

    Raises:
      ``_ConfigError`` (subclass of ``RuntimeError``) if no token
      could be resolved. Caller (MCP tool) catches and turns into a
      structured ``{"error": "config_missing", ...}`` envelope.
    """
    url = webapp_url or os.environ.get("DAIMON_WEBAPP_URL", DEFAULT_WEBAPP_URL)
    chan = channel or os.environ.get("DAIMON_WEBAPP_CHANNEL", DEFAULT_CHANNEL)

    if token is None:
        token = os.environ.get("DAIMON_WEBAPP_TOKEN")
    if token is None:
        token_file = os.environ.get("DAIMON_WEBAPP_TOKEN_FILE")
        if token_file:
            token = _read_token_file(Path(token_file))
    if token is None:
        # Last resort: VPS-side convention. Won't exist on user installs.
        token = _read_token_file(_FALLBACK_TOKEN_FILE)
    if not token:
        raise _ConfigError(
            "no webapp token found — set DAIMON_WEBAPP_TOKEN "
            "(or DAIMON_WEBAPP_TOKEN_FILE pointing to a file)"
        )

    return InboxConfig(webapp_url=url, token=token, channel=chan)


# Re-exported alias so callers can ``except inbox.ConfigError`` cleanly
# without importing the underscore-prefixed name. We keep the underscore
# on the class itself so unrelated code can't grow a dependency on the
# specific exception class without going through the module's API.
ConfigError = _ConfigError
