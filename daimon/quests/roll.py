"""Daily quest rolling — deterministic from (pubkey, UTC date).

Picks 1 easy + 1 medium + 1 hard template, materializes each into a
concrete quest dict, returns the list of 3.

## Determinism

Same ``(pubkey_hex, date)`` → same 3 quests. We use the same HMAC-SHA256
primitive as the skin-shop rotation (``daimon.shop.rotation.daily_seed``)
so quests + shop both rollover at the same UTC midnight and a single
seed-rotation invalidates both at once.

The seed feeds a deterministic uint32 stream (``_drbg_uint32`` style) —
each "draw" pulls from a different sub-stream label so picking the easy
template can't shadow the medium pick or vice versa. Same trick the
shop's Fisher-Yates shuffle uses.

## Why 1-of-each-tier?

Marvel Snap variant: "play / progress / mastery" tier mix per day. Keeps
reward potential predictable (~175¤/day if all complete) and prevents
"3 hard quests" frustration days. If we want variance later, we can
weight tier picks (e.g. 60% standard mix, 30% double-medium, 10% three
of the same tier) — but for V1 the flat distribution is the right
boring choice.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
from typing import Any, Callable, Dict, List, Optional, Tuple

from .catalog import (
    DIFFICULTY_TIERS,
    QUEST_TEMPLATES,
    QuestTemplate,
    materialize,
    templates_by_tier,
)


# Tier order for the 3 daily picks. Stable so `quests[0]` is always easy,
# `quests[1]` always medium, `quests[2]` always hard — the home card
# renders them in this order.
TIER_ORDER: Tuple[str, ...] = ("easy", "medium", "hard")


def _daily_seed(pubkey_hex: str, day: _dt.date) -> bytes:
    """HMAC-SHA256(pubkey_hex, "YYYY-MM-DD"). Mirrors ``shop.rotation.daily_seed``.

    Re-implemented here (not imported) so the quests module can run
    without the shop module being importable. The two implementations
    MUST stay byte-identical — there's a test in ``test_quests.py`` that
    asserts ``quests._daily_seed == shop.daily_seed`` for the same args.
    """
    msg = day.strftime("%Y-%m-%d").encode("ascii")
    key = pubkey_hex.encode("ascii")
    return hmac.new(key, msg, hashlib.sha256).digest()


def _drbg_uint32(seed: bytes, label: bytes, idx: int) -> int:
    """Deterministic uint32 from (seed, label, index). Mirrors ``shop.rotation``."""
    h = hashlib.sha256()
    h.update(seed)
    h.update(b"|")
    h.update(label)
    h.update(b"|")
    h.update(idx.to_bytes(8, "big"))
    return int.from_bytes(h.digest()[:4], "big")


def _rng_for(seed: bytes, label: bytes) -> Callable[[int], int]:
    """Return a counter-based ``rng(max)`` over ``(seed, label)``.

    Each call advances an internal counter and returns
    ``_drbg_uint32(seed, label, counter) % max``. Distinct labels give
    independent sub-streams so picking easy / medium / hard quests can't
    accidentally correlate.
    """
    counter = 0

    def _next(max_excl: int) -> int:
        nonlocal counter
        counter += 1
        return _drbg_uint32(seed, label, counter) % max_excl

    return _next


def roll_today(
    pubkey_hex: str,
    *,
    day: Optional[_dt.date] = None,
) -> List[Dict[str, Any]]:
    """Roll today's 3 quests for the given identity.

    Args:
      pubkey_hex: 64-char hex pubkey of the player.
      day: UTC date to roll for. Defaults to today (UTC).

    Returns:
      A list of exactly 3 materialized quest dicts (1 easy, 1 medium,
      1 hard, in that order). See ``catalog.materialize`` for the dict
      shape.

    The returned list is freshly materialized on every call — callers
    that want stable id-equality across calls within a day should
    persist via ``state.save_quests`` and reload via ``state.load_quests``.
    """
    if day is None:
        day = _dt.datetime.now(_dt.timezone.utc).date()

    seed = _daily_seed(pubkey_hex, day)
    quests: List[Dict[str, Any]] = []

    for tier in TIER_ORDER:
        # Distinct sub-stream label per tier so the easy pick doesn't
        # influence the medium pick. ``b"tier:easy"`` etc.
        rng = _rng_for(seed, f"tier:{tier}".encode("ascii"))
        templates = templates_by_tier(tier)
        if not templates:
            # Defensive — can't happen given the static catalog, but if a
            # future tier is added without templates we'd rather skip than
            # crash the whole roll.
            continue
        # Pick a template index, then materialize with a fresh RNG so the
        # template's params draws don't shadow the picker's draws.
        pick_idx = rng(len(templates))
        template = templates[pick_idx]
        materialize_rng = _rng_for(
            seed, f"tier:{tier}:params".encode("ascii"),
        )
        quest = materialize(template, materialize_rng)
        quests.append(quest)

    return quests
