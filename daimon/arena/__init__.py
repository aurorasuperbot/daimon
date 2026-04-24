"""DAIMON arena client — engine ↔ daimon-arena GitHub Issue protocol.

This package implements the **engine side** of the commit-reveal PvP protocol
plus the supporting identity / leaderboard / dispute / card-proposal flows.
The matching **arbiter side** lives in `daimon-arena/scripts/arbitrate.py`
and is invoked by `.github/workflows/arbiter.yml` on Issue comments.

Architecture
------------
- :mod:`daimon.arena.encoding` — Pure functions: canonical JSON, body format /
  parse, signing payloads, commit hashes. **No I/O.** Testable in isolation
  and shared verbatim with the arbiter (same canonical bytes both sides).
- :mod:`daimon.arena.client` — Thin subprocess wrapper around the ``gh`` CLI.
  Returns structured dicts. Handles missing-binary, auth-error, rate-limit,
  and parse failures with documented error envelopes.
- :mod:`daimon.arena.state` — Local secret state for in-flight PvP matches
  (per-issue ``{nonce, loadout, side}`` so the reveal phase can sign its
  payload without re-deriving anything).
- :mod:`daimon.arena.ops` — High-level operations that compose the three
  layers above. One function per agent-facing operation; the MCP tools in
  :mod:`daimon.mcp.server` are thin shims over these.

The protocol shapes are documented in detail in
:mod:`daimon.arena.encoding` and mirror the arbiter's expectations exactly.
Any change here that breaks compatibility with arbitrate.py is a bug in
*both* repos and must land in one PR pair.
"""

from daimon.arena.encoding import (
    PROTOCOL_VERSION_PVP,
    PROTOCOL_VERSION_REGISTER,
    PROTOCOL_VERSION_DISPUTE,
    PROTOCOL_VERSION_CARD_PROPOSE,
    canonical_json,
    loadout_commit_hash,
    pvp_signing_payload,
    register_signing_payload,
    dispute_signing_payload,
    card_propose_signing_payload,
    format_kv_body,
    parse_kv_body,
    extract_json_block,
    derive_joint_seed,
)

__all__ = [
    "PROTOCOL_VERSION_PVP",
    "PROTOCOL_VERSION_REGISTER",
    "PROTOCOL_VERSION_DISPUTE",
    "PROTOCOL_VERSION_CARD_PROPOSE",
    "canonical_json",
    "loadout_commit_hash",
    "pvp_signing_payload",
    "register_signing_payload",
    "dispute_signing_payload",
    "card_propose_signing_payload",
    "format_kv_body",
    "parse_kv_body",
    "extract_json_block",
    "derive_joint_seed",
]
