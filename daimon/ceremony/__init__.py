"""DAIMON tier-up ceremonies — the rare, high-stakes promotion event.

When the local agent crosses an arena tier threshold (Rookie → Novice at
3 wins, → Veteran at 10, → Elite at 25, → Champion at 50), the home card
surfaces a one-time celebratory banner with a currency reward. The
agent claims it explicitly via ``dm_tier_up_claim`` — unlike daily quests
(auto-claim), ceremonies are explicit because they're rare, large, and
worth a beat of UI attention.

## Architecture

  ``state``    — JSON persistence of ``claimed_tier`` + ``claim_history``.
                  ``~/.config/daimon/tier_progress.json``. Atomic writes.
                  Mirrors the ``quests/state.py`` pattern down to the
                  schema-version header + None-on-corruption silent reload.
  ``tier_up``  — pending-detection + claim logic. Reads ``arena.my_rank``
                  for the canonical wins count, compares to the local
                  ``claimed_tier``, and either returns a ``Pending``
                  descriptor or executes the multi-tier-jump claim path.

## State invariants

  * ``claimed_tier`` is **monotonic** — never decremented even if the
    leaderboard's wins count drops (after a dispute settlement, say).
    Rationale: the ceremony was experienced; revoking the reward would
    feel hostile.
  * Each tier crossing mints exactly **one** ledger entry, keyed by
    ``idempotency_key=f"tier_up_{TierName}"``. A multi-tier jump
    (e.g. Rookie → Veteran from a streak) mints all skipped tiers in
    one ``claim_pending`` call — separate idempotency keys per tier so
    re-running ``claim_pending`` is a strict no-op.
  * The local state is the **single source of truth for "what's been
    claimed"** — the ledger is the source of truth for "what's been
    *paid*". Both are append-only / monotonic so they can't drift
    silently; verification cross-checks them via
    ``audit_state_against_ledger``.

## Why local + ledger-backed?

DAIMON's invariant: state lives where the user lives. Tier *thresholds*
are public (the arena exposes `my_rank.wins`); tier *claims* are local
(no one else needs to know which ceremonies you've watched). This
mirrors the quests model exactly.
"""

from __future__ import annotations

from .state import (
    CEREMONY_PATH,
    SCHEMA_VERSION,
    load_state,
    save_state,
)
from .tier_up import (
    PendingCeremony,
    REWARD_SCHEDULE,
    TIER_ORDER,
    audit_state_against_ledger,
    claim_pending,
    pending_ceremony,
    tier_index,
)

__all__ = [
    "CEREMONY_PATH",
    "PendingCeremony",
    "REWARD_SCHEDULE",
    "SCHEMA_VERSION",
    "TIER_ORDER",
    "audit_state_against_ledger",
    "claim_pending",
    "load_state",
    "pending_ceremony",
    "save_state",
    "tier_index",
]
