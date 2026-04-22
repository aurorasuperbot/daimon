"""Side-effect helpers for publishing match / pull state to the spectator HUD.

Both surfaces (MCP tools in ``daimon.mcp.server`` and the ``daimon`` CLI in
``daimon.cli``) must produce IDENTICAL state.json side-effects so that an
agent doing ``dm_match(...)`` and a human doing ``daimon match ...`` both end
up with the same animation in ``daimon play``.

This module is the single canonical implementation of "I just resolved a
match / minted a card — make it visible to the HUD." Both call-sites import
from here. Failure to publish is logged but never propagated — the result of
the underlying action is what the caller actually wants.

Refactor history (2026-04-22): the CLI commands ``daimon pull`` /
``daimon match`` / ``daimon match-npc`` did not write state.json — only the
MCP equivalents did, leaving humans/CLI users invisible to the HUD.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from daimon.cards import extract_display_fields
from daimon.engine import Loadout
from daimon.engine.combat import MatchResult
from daimon.play.adapter import (
    CardDisplay,
    ParticipantInfo,
    match_result_to_match,
)
from daimon.play.state import new_id, write_state

logger = logging.getLogger(__name__)


def _display_override_from_fields(df) -> Optional[CardDisplay]:
    """Convert CardDisplayFields → CardDisplay (or None when nothing to show).

    Mirrors ``daimon.mcp.server._display_override_from_fields`` byte-for-byte
    so both surfaces synthesize the same animation payloads from the same raw
    loadout JSON.
    """
    if not any((df.name, df.short_name, df.rarity, df.art_path)):
        return None
    return CardDisplay(
        name=df.name,
        short_name=df.short_name,
        rarity=df.rarity,
        art_path=df.art_path,
    )


def _displays_from_raw(cards_raw: list[Any]) -> tuple[Optional[CardDisplay], ...]:
    out: list[Optional[CardDisplay]] = []
    for c in cards_raw:
        if isinstance(c, dict):
            out.append(_display_override_from_fields(extract_display_fields(c)))
        else:
            out.append(None)
    return tuple(out)


def publish_match_state(
    *,
    result: MatchResult,
    loadout_a: Loadout,
    loadout_b: Loadout,
    a_raw: list[Any],
    b_raw: list[Any],
    player_name: str = "player",
    player_rank: str = "",
    opponent_name: str = "opponent",
    opponent_rank: str = "",
) -> Optional[str]:
    """Publish a freshly resolved match to state.json. Best-effort.

    Returns the ``state_id`` on success, ``None`` on any failure (logged).
    The caller's primary action — printing the result, returning JSON to MCP,
    etc. — must NOT be gated on this succeeding.
    """
    state_id = new_id("match")
    try:
        match_payload = match_result_to_match(
            result, loadout_a, loadout_b,
            match_id=state_id,
            player=ParticipantInfo(
                name=player_name, rank=player_rank,
                card_displays=_displays_from_raw(a_raw),
            ),
            opponent=ParticipantInfo(
                name=opponent_name, rank=opponent_rank,
                card_displays=_displays_from_raw(b_raw),
            ),
        )
        state_payload: Dict[str, Any] = json.loads(match_payload.model_dump_json())
        write_state("match", state_payload, id=state_id)
        return state_id
    except Exception:  # noqa: BLE001 — state-write is best-effort
        logger.exception("publish_match_state failed (non-fatal)")
        return None


def publish_pull_state(*, receipt_dict: Dict[str, Any]) -> Optional[str]:
    """Publish a fresh pull receipt to state.json. Best-effort.

    Returns the ``state_id`` on success, ``None`` on any failure.
    """
    state_id = new_id("pull")
    try:
        write_state("pull", dict(receipt_dict), id=state_id)
        return state_id
    except Exception:  # noqa: BLE001
        logger.exception("publish_pull_state failed (non-fatal)")
        return None
