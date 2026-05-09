"""Pity system — soft + hard rarity protection for gacha pulls.

Counts pulls since the last rare-or-better result and adjusts
rarity weights to prevent extended dry streaks.

- Pulls 0–29:  no bonus (pure weighted random)
- Pulls 30–49: soft pity — rare+ weight scales up each pull
- Pull 50+:    hard pity — guaranteed rare+

The counter resets to 0 whenever a rare/epic/legendary card drops.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from daimon.mining import ledger as _ledger_mod


SOFT_PITY_START = 30
HARD_PITY_AT = 50
SOFT_PITY_BONUS_PER_PULL = 0.03

RARE_PLUS = frozenset({"rare", "epic", "legendary"})


def get_pity_state(ledger_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the current pity counter by scanning pull entries."""
    if ledger_path is None:
        ledger_path = _ledger_mod.LEDGER_PATH

    pulls_since_rare_plus = 0
    total_pulls = 0

    if ledger_path.is_file():
        with open(ledger_path, "r", encoding="utf-8") as f:
            pull_rarities: list[str] = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("kind") == "pull":
                    pull_rarities.append(entry.get("rarity", "common"))

            total_pulls = len(pull_rarities)
            pulls_since_rare_plus = 0
            for rarity in reversed(pull_rarities):
                if rarity in RARE_PLUS:
                    break
                pulls_since_rare_plus += 1

    soft_pity_active = pulls_since_rare_plus >= SOFT_PITY_START
    pity_bonus = 0.0
    if soft_pity_active:
        pity_bonus = (pulls_since_rare_plus - SOFT_PITY_START) * SOFT_PITY_BONUS_PER_PULL

    return {
        "pulls_since_rare_plus": pulls_since_rare_plus,
        "total_pulls": total_pulls,
        "soft_pity_start": SOFT_PITY_START,
        "hard_pity_at": HARD_PITY_AT,
        "soft_pity_active": soft_pity_active,
        "pity_bonus": round(pity_bonus, 2),
        "next_is_guaranteed": pulls_since_rare_plus >= HARD_PITY_AT,
    }


def adjusted_rarity_weights(
    base_weights: Dict[str, int],
    pulls_since_rare_plus: int,
) -> Dict[str, int]:
    """Return rarity weights adjusted for the current pity state.

    Returns the original weights unchanged when below the soft-pity
    threshold, boosted rare+ weights during soft pity, and rare+-only
    weights at hard pity.
    """
    if pulls_since_rare_plus < SOFT_PITY_START:
        return dict(base_weights)

    if pulls_since_rare_plus >= HARD_PITY_AT:
        return {r: w for r, w in base_weights.items() if r in RARE_PLUS}

    bonus = (pulls_since_rare_plus - SOFT_PITY_START) * SOFT_PITY_BONUS_PER_PULL
    adjusted: Dict[str, int] = {}
    for rarity, weight in base_weights.items():
        if rarity in RARE_PLUS:
            adjusted[rarity] = max(1, int(weight * (1 + bonus * 3)))
        else:
            adjusted[rarity] = max(1, int(weight * max(0.1, 1 - bonus)))
    return adjusted
