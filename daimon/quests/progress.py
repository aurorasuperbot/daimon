"""Quest progress evaluation + auto-claim.

Progress is *derived*, not stored. Every call rescans today's ledger +
mining ticker and re-computes ``(progress, target, complete)`` for each
of the 3 rolled quests. This is the "single source of truth" rule the
ledger imposes — never persist a balance/progress that can drift
relative to what the ledger actually says.

## Auto-claim

When ``progress >= target`` AND no ``quest_reward`` ledger entry exists
yet for ``(date, quest_id)``, ``evaluate_and_claim`` writes one — kind
``"quest_reward"``, positive amount = ``quest["reward"]``, with
``idempotency_key=f"quest_{date}_{quest_id}"``. The idempotency key
guarantees that re-running ``evaluate_and_claim`` 100 times in a day
mints exactly one reward per quest.

## Why auto-claim instead of explicit `dm_claim`?

Two reasons:

1. **Friction.** A daily-quest layer that requires a manual claim is
   hostile to agentic play — the agent would have to remember to call
   ``dm_claim`` after every match. Auto-claim lets the agent just play.
2. **Idempotency is already free.** The ledger already rejects duplicate
   ``idempotency_key`` writes; we get the "exactly-once reward" semantic
   without any new bookkeeping.

The cost is one extra ledger scan per ``dm_pull`` / ``dm_match*`` call,
which is cheap (<1 ms for ledgers under a few thousand entries — and
ledgers grow at ~10 entries/day).

## Template → matcher dispatch

Each ``template_id`` is mapped to a function that consumes
``(quest, ledger_today, ticker_today)`` and returns the integer
progress. Adding a new template requires adding both:
  1. The template entry in ``catalog.QUEST_TEMPLATES``.
  2. A matcher in this module's ``_MATCHERS`` table.

Forgetting the matcher is loud — ``evaluate_progress`` returns
progress=0 and logs a warning, so the quest just never completes
(rather than silently breaking the ledger).
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from daimon.identity import Identity
from daimon.mining import buffer as _buffer
from daimon.mining import ledger as _ledger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuestProgress:
    """Per-quest progress snapshot.

    Attributes:
      quest_id: Stable id from the materialized quest (``catalog.materialize``).
      template_id: Template id for matcher dispatch.
      title: Human-readable quest title (carried through for UI rendering).
      tier: ``easy`` / ``medium`` / ``hard``.
      reward: Currency amount paid when complete.
      progress: Current count toward the goal.
      target: Goal value — quest is complete when ``progress >= target``.
      complete: ``progress >= target``. Convenience boolean.
      claimed: True if a ``quest_reward`` ledger entry exists for this
        ``(date, quest_id)``. UI uses this to show "Claimed ✓" vs
        "Ready to claim".
    """

    quest_id: str
    template_id: str
    title: str
    tier: str
    reward: int
    progress: int
    target: int
    complete: bool
    claimed: bool


# ---------------------------------------------------------------------------
# "Today" filter — UTC date alignment with the daily seed
# ---------------------------------------------------------------------------

def _today_iso(now: Optional[_dt.datetime] = None) -> str:
    """``"YYYY-MM-DD"`` for today (UTC). Mirrors ``state.today_str``."""
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).date().isoformat()


def _entry_is_today(entry: Dict[str, Any], today: str) -> bool:
    """Cheap date-prefix check on the entry's ISO timestamp.

    ISO format starts with ``YYYY-MM-DD`` so a ``startswith`` match is
    equivalent to a UTC-date comparison without parsing — and the
    ledger/ticker both write timestamps in UTC (see
    ``ledger._now_iso`` / ``buffer._now_iso``).
    """
    ts = entry.get("ts")
    return isinstance(ts, str) and ts.startswith(today)


# ---------------------------------------------------------------------------
# Per-template matchers
# ---------------------------------------------------------------------------

# Matcher signature: (quest, ledger_today, ticker_today) -> progress (int)
#
# Conventions:
#   - quest is the materialized dict from catalog.materialize.
#   - ledger_today / ticker_today are pre-filtered to UTC-today entries.
#   - Matchers MUST be pure (no I/O, no side effects).
#   - Return 0 on any input shape error — never raise. The quest just
#     doesn't make progress this tick; the user can play more.
Matcher = Callable[
    [Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]],
    int,
]


def _count_matches(ticker: List[Dict[str, Any]],
                   *,
                   outcome: Optional[str] = None,
                   opponent_tier: Optional[str] = None,
                   loadout_element: Optional[str] = None) -> int:
    """Count match ticker entries matching the given filters.

    Filters are AND-composed; ``None`` means "don't filter on this axis".
    Used by every match-related matcher so the filter logic lives in one
    place.
    """
    n = 0
    for e in ticker:
        if e.get("kind") != "match":
            continue
        if outcome is not None and e.get("outcome") != outcome:
            continue
        if opponent_tier is not None and e.get("opponent_tier") != opponent_tier:
            continue
        if loadout_element is not None and e.get("loadout_element") != loadout_element:
            continue
        n += 1
    return n


def _m_play_match(quest, ledger_today, ticker_today) -> int:
    return _count_matches(ticker_today)


def _m_win_match(quest, ledger_today, ticker_today) -> int:
    return _count_matches(ticker_today, outcome="win")


def _m_win_3_matches(quest, ledger_today, ticker_today) -> int:
    # Same matcher as win_match — the *target* differs, not the count.
    return _count_matches(ticker_today, outcome="win")


def _m_win_with_element(quest, ledger_today, ticker_today) -> int:
    el = quest.get("params", {}).get("element")
    if not isinstance(el, str):
        return 0
    return _count_matches(ticker_today, outcome="win", loadout_element=el)


def _m_beat_tier(quest, ledger_today, ticker_today) -> int:
    tier = quest.get("params", {}).get("tier")
    if not isinstance(tier, str):
        return 0
    return _count_matches(ticker_today, outcome="win", opponent_tier=tier)


def _m_pull(quest, ledger_today, ticker_today) -> int:
    # Pulls are durable — count from the ledger, not the ticker. The
    # ledger is the source of truth (ticker is HUD chrome and may
    # truncate). A pull is always kind="pull" and amount<0.
    return sum(1 for e in ledger_today if e.get("kind") == "pull")


def _m_mine(quest, ledger_today, ticker_today) -> int:
    # Sum today's mined currency. Ledger only — ticker entries have
    # ``amount=0`` for non-mine kinds and would understate the total.
    return sum(int(e.get("amount", 0))
               for e in ledger_today if e.get("kind") == "mine")


# Dispatch table — one entry per template_id in the catalog. Adding a
# new template without adding here logs a warning + treats as "no
# progress" (loud failure mode, never silent).
_MATCHERS: Dict[str, Matcher] = {
    "play_match": _m_play_match,
    "pull_card": _m_pull,
    "mine_easy": _m_mine,
    "win_match": _m_win_match,
    "win_with_element": _m_win_with_element,
    "mine_medium": _m_mine,
    "beat_tier_medium": _m_beat_tier,
    "win_3_matches": _m_win_3_matches,
    "pull_2_cards": _m_pull,
    "mine_hard": _m_mine,
    "beat_tier_hard": _m_beat_tier,
}


# ---------------------------------------------------------------------------
# Target inference
# ---------------------------------------------------------------------------

def _target_for(quest: Dict[str, Any]) -> int:
    """Derive the goal value from a quest's params.

    Centralized so renderers / matchers / claim-checker all agree on
    what "complete" means. Falls back to 1 for templates whose params
    don't include an obvious quantity (win_with_element, beat_tier).
    """
    params = quest.get("params", {})
    if not isinstance(params, dict):
        return 1
    n = params.get("n")
    if isinstance(n, int) and n > 0:
        return n
    amount = params.get("amount")
    if isinstance(amount, int) and amount > 0:
        return amount
    return 1


# ---------------------------------------------------------------------------
# Reward-claim detection
# ---------------------------------------------------------------------------

def _has_quest_reward_today(
    quest_id: str,
    today: str,
    ledger_entries: List[Dict[str, Any]],
) -> bool:
    """True if a ``kind="quest_reward"`` entry already exists for this quest today.

    We match on both the idempotency_key shape we use when writing
    (``f"quest_{date}_{quest_id}"``) and on the explicit ``quest_id`` /
    ``date`` fields stored in the entry — belt-and-braces so a
    hand-written ledger entry without an idempotency_key still counts.
    """
    target_idemp = f"quest_{today}_{quest_id}"
    for e in ledger_entries:
        if e.get("kind") != "quest_reward":
            continue
        if e.get("idempotency_key") == target_idemp:
            return True
        if e.get("quest_id") == quest_id and e.get("date") == today:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _read_ledger_today(
    today: str,
    *,
    ledger_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return today's ledger entries. Empty list on missing/unreadable file."""
    path = ledger_path or _ledger.LEDGER_PATH
    try:
        all_entries = _ledger._read_entries(path)  # noqa: SLF001
    except (_ledger.LedgerCorruptError, OSError):
        # A corrupt ledger is a separate problem (verify_ledger reports
        # it). For quest progress we just return empty — the user will
        # see "0 progress" and know something's off.
        return []
    return [e for e in all_entries if _entry_is_today(e, today)]


def _read_ticker_today(
    today: str,
    *,
    buffer_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return today's mining-ticker entries. Empty on missing/unreadable file.

    The ticker is bounded (250–500 entries) so we tail() the whole
    window rather than streaming. For agentic play volumes that's
    plenty — a player generating 500 ticker events in a single day
    would already have completed every quest.
    """
    entries = _buffer.tail(n=_buffer.MAX_ENTRIES, path=buffer_path)
    return [e for e in entries if _entry_is_today(e, today)]


def evaluate_progress(
    quests: List[Dict[str, Any]],
    *,
    today: Optional[str] = None,
    ledger_path: Optional[Path] = None,
    buffer_path: Optional[Path] = None,
) -> List[QuestProgress]:
    """Compute current progress for each rolled quest. Read-only.

    Args:
      quests: The list returned by ``roll_today`` (or the ``quests``
        field of a persisted state record).
      today: UTC date string ``"YYYY-MM-DD"``. Defaults to today.
      ledger_path / buffer_path: Override paths (test seam).

    Returns:
      One ``QuestProgress`` per input quest, in the same order. Never
      raises — a missing/corrupt ledger just yields zeros, an unknown
      template logs a warning and yields zero. The UI is always
      renderable.
    """
    if today is None:
        today = _today_iso()

    ledger_today = _read_ledger_today(today, ledger_path=ledger_path)
    ticker_today = _read_ticker_today(today, buffer_path=buffer_path)

    # Full ledger (not just today's) for claim detection — a reward
    # written earlier today still counts even though we filter the
    # progress-input by date. Reward entries are always written within
    # the same UTC day they're earned, but we read the whole tail to be
    # safe against clock-skew edge cases.
    try:
        full_ledger = _ledger._read_entries(  # noqa: SLF001
            ledger_path or _ledger.LEDGER_PATH,
        )
    except (_ledger.LedgerCorruptError, OSError):
        full_ledger = []

    out: List[QuestProgress] = []
    for q in quests:
        template_id = q.get("template_id", "")
        matcher = _MATCHERS.get(template_id)
        if matcher is None:
            logger.warning(
                "quest progress: no matcher for template_id=%r — "
                "treating as zero progress",
                template_id,
            )
            progress = 0
        else:
            try:
                progress = matcher(q, ledger_today, ticker_today)
            except Exception:  # noqa: BLE001 — never break HUD on a matcher bug
                logger.exception(
                    "quest matcher %r raised — treating as zero", template_id,
                )
                progress = 0

        target = _target_for(q)
        complete = progress >= target
        claimed = _has_quest_reward_today(
            q.get("id", ""), today, full_ledger,
        )
        out.append(QuestProgress(
            quest_id=q.get("id", ""),
            template_id=template_id,
            title=q.get("title", ""),
            tier=q.get("tier", ""),
            reward=int(q.get("reward", 0)),
            progress=int(progress),
            target=int(target),
            complete=complete,
            claimed=claimed,
        ))
    return out


def evaluate_and_claim(
    quests: List[Dict[str, Any]],
    *,
    identity: Optional[Identity] = None,
    today: Optional[str] = None,
    ledger_path: Optional[Path] = None,
    buffer_path: Optional[Path] = None,
) -> List[QuestProgress]:
    """Evaluate progress AND auto-claim any complete-but-unclaimed quests.

    This is the canonical entrypoint called from ``dm_pull`` /
    ``dm_match*`` after each play action. Idempotent — safe to call
    multiple times per session.

    Args:
      quests: Same shape as ``evaluate_progress``.
      identity: Override the signing identity (test seam).
      today / ledger_path / buffer_path: Test seams.

    Returns:
      Same shape as ``evaluate_progress`` — but if a claim was just
      written, the corresponding entry's ``claimed`` flag is True.

    Side effects:
      For each ``(complete and not claimed)`` quest, appends one
      ``kind="quest_reward"`` entry to the ledger with the quest's
      reward as the amount. Idempotent via
      ``idempotency_key=f"quest_{today}_{quest_id}"``.
    """
    if today is None:
        today = _today_iso()

    snapshot = evaluate_progress(
        quests, today=today,
        ledger_path=ledger_path, buffer_path=buffer_path,
    )

    # Identify what needs claiming. We do this *before* writing any
    # entries so a transient I/O failure doesn't leave us with a
    # half-claimed state — and so we can re-evaluate fresh after.
    to_claim = [p for p in snapshot if p.complete and not p.claimed]
    if not to_claim:
        return snapshot

    for prog in to_claim:
        try:
            _ledger.append_quest_reward_entry(
                quest_id=prog.quest_id,
                template_id=prog.template_id,
                tier=prog.tier,
                reward=prog.reward,
                date=today,
                idempotency_key=f"quest_{today}_{prog.quest_id}",
                identity=identity,
                path=ledger_path,
            )
        except Exception:  # noqa: BLE001
            # A claim failure is rare (would only happen on disk full,
            # corrupt ledger, etc.). Log + continue — the next call to
            # evaluate_and_claim will retry naturally.
            logger.exception(
                "quest reward claim failed for quest_id=%r", prog.quest_id,
            )

    # Re-evaluate so the returned snapshot reflects the just-written
    # rewards. This is one extra ledger scan, but it's cheap and avoids
    # the caller having to mutate snapshots in-place.
    return evaluate_progress(
        quests, today=today,
        ledger_path=ledger_path, buffer_path=buffer_path,
    )
