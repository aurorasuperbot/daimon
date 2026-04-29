"""daimon.play — match wire protocol + state.json IPC.

Surviving modules (post-refactor):
  - schema — Pydantic models for the match.json wire protocol
  - state  — read/write state.json (MCP <-> webview IPC)
  - publish — publish state from MCP server side

The terminal renderers (animator/primitives/pil/hud/menu_ui/...) were removed
in the pywebview migration — see refactor.md.
"""

from daimon.play.schema import Match, Action, Round, Outcome  # noqa: F401
