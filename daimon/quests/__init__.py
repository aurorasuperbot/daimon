"""DAIMON daily quests — the daily-return hook.

Three rolled-fresh-per-day quests with currency rewards. Quest state is
local-only (``~/.config/daimon/daily_quests.json``) — same as identity,
collection, and the mining ledger. The arena does not see quests; they're
a single-player progression layer that only matters to the local agent.

## Architecture

  ``catalog``  — quest templates + difficulty tiers (easy/medium/hard) +
                  reward scaling. Pure data + a single ``materialize()``
                  function that turns a template id + RNG into a concrete
                  quest dict.
  ``roll``     — given (pubkey, date), deterministically roll today's
                  3 quests. HMAC-seeded so two calls in the same UTC day
                  return the same set, but a fresh pubkey or a new day
                  re-rolls. Reuses the daily-seed primitive from
                  ``daimon.shop.rotation``.
  ``state``    — JSON persistence of the rolled quest list.
                  ``~/.config/daimon/daily_quests.json``. Atomic writes.
  ``progress`` — ledger + ticker scanning to compute current progress on
                  each quest, and the auto-claim flow that emits a
                  ``quest_reward`` ledger entry the moment progress hits
                  the target. Idempotent — running it 10x produces 1
                  reward entry per quest per day.

## Why local + ledger-backed?

DAIMON's invariant: state lives where the user lives. The arena holds
public commitments only (PvP results, leaderboard totals). Quest progress
is single-player, so it stays local. Auto-claim writes to the *same
mining ledger* that holds mines + pulls + purchases — so quest rewards
are signed with the user's identity key and survive ledger verification
exactly like every other balance change.

## Why deterministic rolling?

If quests re-rolled on every read, an agent that called ``dm_quests``
twice in a row would see different goals — incoherent UX. By keying the
roll on ``(pubkey, UTC date)`` we get:

  * Same pubkey + same day → same 3 quests (idempotent reads).
  * Same pubkey + new day → fresh roll (the daily refresh).
  * Different pubkey + same day → different roll (no two players share
    today's quests, but the *distribution* is the same).

The roll uses the same HMAC primitive as the skin-shop rotation — see
``daimon.shop.rotation.daily_seed``.
"""

from __future__ import annotations

from .catalog import (
    DIFFICULTY_TIERS,
    QUEST_TEMPLATES,
    QuestTemplate,
    materialize,
)
from .progress import (
    QuestProgress,
    evaluate_progress,
    evaluate_and_claim,
)
from .roll import roll_today
from .state import (
    QUESTS_PATH,
    load_quests,
    save_quests,
    today_str,
)

__all__ = [
    "DIFFICULTY_TIERS",
    "QUEST_TEMPLATES",
    "QUESTS_PATH",
    "QuestProgress",
    "QuestTemplate",
    "evaluate_and_claim",
    "evaluate_progress",
    "load_quests",
    "materialize",
    "roll_today",
    "save_quests",
    "today_str",
]
