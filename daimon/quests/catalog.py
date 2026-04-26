"""Quest templates + difficulty tiers.

A quest template is a *parameterized* goal — e.g. "win N matches" or
"win with element X". The roller picks a template, picks parameters,
and stamps a concrete quest dict with id + title + condition.

## Difficulty tiers

  ``easy``   — 25¤ reward, low-effort (any match played, small mine).
  ``medium`` — 50¤ reward, focused goal (win a match, mono-element).
  ``hard``   — 100¤ reward, multi-step (win 3, beat veteran, mine 200).

Per-day rolling typically picks 1 of each tier so reward potential is
balanced — ~175¤/day if all three complete (about 1.75 pulls).

## Why parameterized templates and not enumerated quests?

Two reasons:

1. **Variety.** "Win with VOLT" and "Win with FIRE" are functionally the
   same template with different params. Enumerating each as a separate
   quest would 10x the catalog without adding interesting content.
2. **Tunable reward formulas.** The reward is computed from
   ``(template_tier, params)`` — so a "win 5 matches" hard variant and a
   "win 3 matches" hard variant can scale the reward without splitting
   into two templates.

## Adding a new template

1. Add an entry to ``QUEST_TEMPLATES`` with a unique ``template_id``,
   ``tier``, and a ``params_options`` callable that yields valid params
   given an RNG.
2. Add a matcher to ``daimon.quests.progress.evaluate_progress`` that
   reads ledger + ticker entries and computes ``(progress, target)`` for
   the new template kind.
3. Write a test in ``tests/test_quests.py`` that proves the template
   rolls + evaluates correctly for a representative seed.

The catalog is pure data — no I/O — so adding templates can't break
anything else by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Difficulty tiers
# ---------------------------------------------------------------------------

# Tier label → reward currency. The roller picks 1 of each tier per day,
# so total daily reward potential = 25 + 50 + 100 = 175¤ (~1.75 pulls).
DIFFICULTY_TIERS: Dict[str, int] = {
    "easy": 25,
    "medium": 50,
    "hard": 100,
}

# Element pool used by ``win_with_element`` template variants. Mirrors the
# 5-element split shipped with the v1_alpha catalog; NORMAL is omitted
# because it's a splash element, not a build-around.
ELEMENTS: Tuple[str, ...] = ("FIRE", "WATER", "NATURE", "VOLT", "VOID")

# NPC tiers that the ``beat_tier`` template can target. Ordered so the
# roller can map difficulty → tier (easy = rookie, hard = veteran+).
NPC_TIERS: Tuple[str, ...] = (
    "rookie", "novice", "veteran", "elite", "champion",
)


# ---------------------------------------------------------------------------
# Template definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuestTemplate:
    """Pure data — no behavior. Rendered into a concrete quest by ``materialize``.

    Attributes:
      template_id: Stable identifier — used to dispatch progress matchers.
        Examples: ``"play_n_matches"``, ``"win_with_element"``, ``"mine_n"``.
      tier: ``"easy"`` / ``"medium"`` / ``"hard"`` — controls reward + roll
        weight.
      title_fmt: Python format string for the user-facing title. Receives
        the materialized params as kwargs (e.g. ``"Win {n} matches"``).
      params_options: Callable that takes an ``rng_int(max)`` function and
        returns a concrete params dict. The roller calls this with a
        deterministic RNG so the same (pubkey, date) produces the same
        params.
    """

    template_id: str
    tier: str
    title_fmt: str
    params_options: Callable[[Callable[[int], int]], Dict[str, Any]] = field(
        repr=False,
    )


# Concrete params-option helpers. Each takes a ``rng(max)`` callable
# (returns an int in [0, max)) and emits a params dict. Kept as
# top-level functions (not lambdas) so the dataclass repr stays clean
# and so the test suite can assert on referential identity.

def _params_n_matches_easy(rng: Callable[[int], int]) -> Dict[str, Any]:
    # "Play a match" — easy is just participation, not winning.
    return {"n": 1, "outcome": "any"}


def _params_n_matches_medium(rng: Callable[[int], int]) -> Dict[str, Any]:
    # "Win a match" — medium is one win.
    return {"n": 1, "outcome": "win"}


def _params_n_matches_hard(rng: Callable[[int], int]) -> Dict[str, Any]:
    # "Win 3 matches" — hard is multi-win.
    return {"n": 3, "outcome": "win"}


def _params_win_with_element(rng: Callable[[int], int]) -> Dict[str, Any]:
    # Pick one of the 5 build-around elements. Mono-element loadout
    # required (all 6 cards same element).
    el = ELEMENTS[rng(len(ELEMENTS))]
    return {"element": el}


def _params_pull_easy(rng: Callable[[int], int]) -> Dict[str, Any]:
    return {"n": 1}


def _params_pull_hard(rng: Callable[[int], int]) -> Dict[str, Any]:
    return {"n": 2}


def _params_mine_easy(rng: Callable[[int], int]) -> Dict[str, Any]:
    return {"amount": 50}


def _params_mine_medium(rng: Callable[[int], int]) -> Dict[str, Any]:
    return {"amount": 100}


def _params_mine_hard(rng: Callable[[int], int]) -> Dict[str, Any]:
    return {"amount": 200}


def _params_beat_tier_medium(rng: Callable[[int], int]) -> Dict[str, Any]:
    # Medium tier targets rookie / novice (the two lowest tiers).
    tier = NPC_TIERS[rng(2)]
    return {"tier": tier}


def _params_beat_tier_hard(rng: Callable[[int], int]) -> Dict[str, Any]:
    # Hard targets veteran / elite / champion. Always a step up from
    # the player's typical opponent.
    tier = NPC_TIERS[2 + rng(3)]
    return {"tier": tier}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

# Note: ``play_n_matches`` is intentionally split into easy/medium/hard
# variants (rather than one template with a difficulty-conditional
# params function) so the roller can weight tiers without inventing a
# difficulty-modulation layer. Keeps the catalog flat and inspectable.
QUEST_TEMPLATES: Tuple[QuestTemplate, ...] = (
    # ---------- easy (25¤) ----------
    QuestTemplate(
        template_id="play_match",
        tier="easy",
        title_fmt="Play a match",
        params_options=_params_n_matches_easy,
    ),
    QuestTemplate(
        template_id="pull_card",
        tier="easy",
        title_fmt="Pull {n} card",
        params_options=_params_pull_easy,
    ),
    QuestTemplate(
        template_id="mine_easy",
        tier="easy",
        title_fmt="Mine {amount}¤",
        params_options=_params_mine_easy,
    ),
    # ---------- medium (50¤) ----------
    QuestTemplate(
        template_id="win_match",
        tier="medium",
        title_fmt="Win a match",
        params_options=_params_n_matches_medium,
    ),
    QuestTemplate(
        template_id="win_with_element",
        tier="medium",
        title_fmt="Win with a mono-{element} loadout",
        params_options=_params_win_with_element,
    ),
    QuestTemplate(
        template_id="mine_medium",
        tier="medium",
        title_fmt="Mine {amount}¤",
        params_options=_params_mine_medium,
    ),
    QuestTemplate(
        template_id="beat_tier_medium",
        tier="medium",
        title_fmt="Beat a {tier}-tier NPC",
        params_options=_params_beat_tier_medium,
    ),
    # ---------- hard (100¤) ----------
    QuestTemplate(
        template_id="win_3_matches",
        tier="hard",
        title_fmt="Win {n} matches",
        params_options=_params_n_matches_hard,
    ),
    QuestTemplate(
        template_id="pull_2_cards",
        tier="hard",
        title_fmt="Pull {n} cards",
        params_options=_params_pull_hard,
    ),
    QuestTemplate(
        template_id="mine_hard",
        tier="hard",
        title_fmt="Mine {amount}¤",
        params_options=_params_mine_hard,
    ),
    QuestTemplate(
        template_id="beat_tier_hard",
        tier="hard",
        title_fmt="Beat a {tier}-tier NPC",
        params_options=_params_beat_tier_hard,
    ),
)


def templates_by_tier(tier: str) -> List[QuestTemplate]:
    """Return all templates of the given tier. Stable order (catalog order)."""
    return [t for t in QUEST_TEMPLATES if t.tier == tier]


def materialize(
    template: QuestTemplate,
    rng: Callable[[int], int],
) -> Dict[str, Any]:
    """Render a template into a concrete quest dict.

    The quest dict is JSON-serializable and self-describing:

      {"id": "<template_id>__<param_summary>",
       "template_id": "win_with_element",
       "title": "Win with a mono-VOLT loadout",
       "tier": "medium",
       "reward": 50,
       "params": {"element": "VOLT"}}

    The ``id`` field is unique per (template_id, params) so two distinct
    materializations of the same template (e.g. "Win with VOLT" vs
    "Win with FIRE") get distinct ids and don't collide in the
    progress + reward tracking.
    """
    params = template.params_options(rng)
    title = template.title_fmt.format(**params)
    reward = DIFFICULTY_TIERS[template.tier]

    # id = template_id + sorted(params) joined into a deterministic string.
    # Stable across runs, human-readable, no separator collisions because
    # template_ids are snake_case and params are scalar strings/ints.
    param_summary = "_".join(
        f"{k}-{v}" for k, v in sorted(params.items())
    )
    quest_id = f"{template.template_id}__{param_summary}"

    return {
        "id": quest_id,
        "template_id": template.template_id,
        "title": title,
        "tier": template.tier,
        "reward": reward,
        "params": params,
    }
