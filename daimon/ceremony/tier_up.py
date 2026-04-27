"""Tier-up ceremony pending-detection + claim logic.

Public surface:

  * ``pending_ceremony()``  — returns a ``PendingCeremony`` if there's
                              one or more unclaimed tier crossings, else
                              ``None``. Pure read; never mutates state.
  * ``claim_pending()``     — atomically mints all pending tier rewards
                              into the ledger AND advances the local
                              ``claimed_tier`` to the new high-water
                              mark. Idempotent (per-tier idempotency
                              keys).
  * ``audit_state_against_ledger()`` — cross-check: every entry in
                              ``claim_history`` MUST have a
                              corresponding ``tier_up_reward`` entry in
                              the ledger.

## Reward schedule

  Rookie  → Novice    +100¤    (3 wins crossed)
  Novice  → Veteran   +250¤   (10 wins crossed)
  Veteran → Elite     +500¤   (25 wins crossed)
  Elite   → Champion +1000¤   (50 wins crossed)

Total over a full climb: 1850¤ — roughly 18 pulls' worth of currency,
which is meaningful but won't dwarf the daily-quest economy (3 quests/day
× ~50¤ avg = ~150¤/day, so a full tier climb is ~12 days of dailies).

## Multi-tier jump

If a streak takes the player from Rookie (0 wins) directly to Veteran
(10 wins) without an intermediate ``claim_pending`` call, the next claim
mints **both** the Novice ceremony AND the Veteran ceremony in one
call:: total reward = 100 + 250 = 350¤. Each tier's ledger entry has
its own idempotency key (``tier_up_Novice``, ``tier_up_Veteran``) so a
re-run is a no-op even on the multi-tier path.

This prevents an exploit where a player could deliberately delay
claiming to "skip" the lower-tier ceremony — the engine *always* mints
every crossed threshold.

## Trigger source

We read ``daimon.arena.ops.my_rank()`` for the canonical wins count.
That call:
  * fetches ``leaderboard.json`` from the arena repo (network)
  * resolves the local pubkey to a wins/losses/draws record
  * tags the resulting tier label via ``tier_of(wins)``

If the arena is unreachable (or the leaderboard doesn't exist yet on a
fresh repo), ``my_rank`` returns either an error envelope or
``rank=None / tier="Rookie"``. ``pending_ceremony`` treats that as
"no pending ceremony" — we never fire a ceremony for a tier we couldn't
actually verify the player crossed. This is a deliberate
robustness/security tradeoff: we'd rather miss a ceremony than mint a
spurious one.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from daimon.arena import ops as arena_ops
from daimon.identity import Identity, load_identity
from daimon.mining.ledger import (
    append_tier_up_reward_entry,
    entry_hash,
    get_balance,
)
from daimon.ceremony import state as ceremony_state


# ---------------------------------------------------------------------------
# Reward schedule + tier ordering
# ---------------------------------------------------------------------------

# Order matters — index = "tier rank" (Rookie=0, Champion=4). Used both
# for monotonicity checks and for computing skipped-tiers on a
# multi-tier jump.
TIER_ORDER: Tuple[str, ...] = ("Rookie", "Novice", "Veteran", "Elite", "Champion")

# Reward for *crossing into* the keyed tier. Rookie has no reward — it's
# the starting state, not an achievement. Frozen via mappingproxy by
# convention; mutating in tests should require a monkeypatch override.
REWARD_SCHEDULE: Dict[str, int] = {
    "Rookie": 0,
    "Novice": 100,
    "Veteran": 250,
    "Elite": 500,
    "Champion": 1000,
}


def tier_index(tier: str) -> int:
    """Return the 0-based ordinal of a tier label, or -1 if unknown.

    Used for monotonicity checks and skipped-tier enumeration.
    Unknown labels return -1 so a corrupt ``claimed_tier`` value can
    never accidentally promote (since ``rank_idx > -1`` is always true
    for any valid current tier).
    """
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PendingCeremony:
    """Snapshot of an unclaimed tier-up event.

    Attributes:
      pending_tier:    Highest tier the player has reached but not claimed.
      prev_tier:       The tier they're crossing FROM (last claimed).
      tiers_to_mint:   Ordered list of *every* tier crossed since the
                       last claim — typically just one element, but a
                       streak can produce ``["Novice", "Veteran"]`` on a
                       Rookie→Veteran jump.
      reward_total:    Sum of REWARD_SCHEDULE for every tier in
                       tiers_to_mint.
      wins_at_check:   Wins count read from ``my_rank`` at detection time
                       (snapshot — recorded in ledger entries for audit).
    """
    pending_tier: str
    prev_tier: str
    tiers_to_mint: Tuple[str, ...]
    reward_total: int
    wins_at_check: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tiers_to_mint"] = list(self.tiers_to_mint)
        return d


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load_or_init_state(
    pubkey_hex: str,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load persisted state or synthesize a fresh-init record.

    A fresh init records ``claimed_tier="Rookie"`` (no ceremonies yet)
    and an empty history. This mirrors the "first-run" path on a clean
    install — the file simply hasn't been written yet.

    On a pubkey mismatch (the user re-bootstrapped identity), we treat
    the existing file as stale and re-init. The old history isn't
    recoverable since the rewards landed under a different signing
    key, but the ledger still holds them under the prior pubkey.
    """
    record = ceremony_state.load_state(path)
    if record is None or record.get("pubkey_hex") != pubkey_hex:
        return {
            "version": ceremony_state.SCHEMA_VERSION,
            "pubkey_hex": pubkey_hex,
            "claimed_tier": "Rookie",
            "claim_history": [],
        }
    return record


def _rank_or_none(rank_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize ``my_rank`` output to either a usable dict or None.

    None signals "we couldn't get a definitive rank". Treated as "no
    pending ceremony" to avoid minting on stale/unreachable data.
    """
    if not isinstance(rank_payload, dict):
        return None
    if rank_payload.get("status") != "ok":
        return None
    # ``my_rank`` returns rank=None for a fresh identity that hasn't
    # played a match yet. Wins is still 0 in that envelope, so we
    # effectively pass through — but we DO want to detect "wins>=3"
    # even with rank=None, since a player who's hit Novice deserves
    # the ceremony regardless of leaderboard ranking position.
    return rank_payload


def pending_ceremony(
    *,
    identity: Optional[Identity] = None,
    path: Optional[Path] = None,
    rank_override: Optional[Dict[str, Any]] = None,
) -> Optional[PendingCeremony]:
    """Return a ``PendingCeremony`` if any tier crossings are unclaimed.

    Args:
      identity:      Optional pre-loaded identity (avoids re-load if the
                     caller already has one). Defaults to ``load_identity()``.
      path:          Override for the state file path (testing).
      rank_override: Inject a synthetic ``my_rank`` payload (testing).
                     Skips the network call.

    Returns:
      ``PendingCeremony`` when one or more tier thresholds have been
      crossed since the last claim. ``None`` when:
        * arena is unreachable / leaderboard missing / wins == 0
        * the identity isn't loaded / no local state
        * ``claimed_tier`` is already at-or-above the achieved tier

    Pure read — does not mutate the state file or the ledger.
    """
    if identity is None:
        try:
            identity = load_identity()
        except FileNotFoundError:
            return None

    # Resolve current achieved tier from arena.
    if rank_override is not None:
        rank = rank_override
    else:
        try:
            rank = arena_ops.my_rank()
        except Exception:  # noqa: BLE001
            return None
    rank = _rank_or_none(rank)
    if rank is None:
        return None
    wins = int(rank.get("wins", 0) or 0)
    achieved_tier = arena_ops.tier_of(wins)
    achieved_idx = tier_index(achieved_tier)
    if achieved_idx <= 0:
        # Still Rookie — no ceremony exists for the starting tier.
        return None

    # Resolve last-claimed tier from local state.
    record = _load_or_init_state(identity.pubkey_hex, path)
    claimed_tier = record.get("claimed_tier", "Rookie")
    claimed_idx = tier_index(claimed_tier)
    if claimed_idx < 0:
        # Corrupt label in state — treat as Rookie to avoid silently
        # double-claiming. Operator can rebuild state from ledger if
        # this ever fires (V1.1 helper TBD).
        claimed_idx = 0

    if achieved_idx <= claimed_idx:
        # Already claimed everything up to and including the current
        # tier. Nothing pending.
        return None

    # Enumerate every tier in (claimed_idx, achieved_idx] — these are
    # the unclaimed crossings. Multi-tier jump = len > 1.
    tiers_to_mint = TIER_ORDER[claimed_idx + 1: achieved_idx + 1]
    reward_total = sum(REWARD_SCHEDULE[t] for t in tiers_to_mint)

    return PendingCeremony(
        pending_tier=achieved_tier,
        prev_tier=claimed_tier,
        tiers_to_mint=tuple(tiers_to_mint),
        reward_total=reward_total,
        wins_at_check=wins,
    )


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def claim_pending(
    *,
    identity: Optional[Identity] = None,
    path: Optional[Path] = None,
    rank_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mint reward(s) for any pending tier crossings + update local state.

    This is the only mutating entry point in the module. It's safe to
    call repeatedly:

      1. If there's no pending ceremony → ``{"status": "noop", ...}``.
      2. If there's a pending ceremony → mints one ``tier_up_reward``
         ledger entry per tier crossed (each idempotent on its own
         key), advances ``claimed_tier`` to the new high-water mark,
         and atomically writes the updated ``claim_history`` + state.

    Re-running after a successful claim is a no-op for *both* the
    ledger (idempotency_key dedup) and the state file (claimed_tier
    has already advanced past the achieved tier). The ledger and
    state never drift — every successful state-file write is preceded
    by the ledger appends, and the idempotency_key contract guarantees
    "at most one ledger entry per tier per identity".

    Args:
      identity:      Pre-loaded identity (defaults to ``load_identity()``).
      path:          Override for the state file path.
      rank_override: Synthetic ``my_rank`` payload for tests.

    Returns:
      ``{"status": "ok", "claimed_tiers": [...], "reward_total": int,
         "balance": int, "claim_history": [...]}``
      OR
      ``{"status": "noop", "claimed_tier": str, "balance": int}``
      OR
      ``{"error": "no_identity" | "no_pending"}`` shapes.

    Never raises on the happy path. A ledger.append failure (rare —
    usually filesystem or signing key trouble) re-raises since silently
    losing a reward would be worse than crashing the tool call.
    """
    if identity is None:
        try:
            identity = load_identity()
        except FileNotFoundError:
            return {"error": "no_identity",
                    "hint": "Call `dm_init` to bootstrap an identity."}

    pending = pending_ceremony(
        identity=identity,
        path=path,
        rank_override=rank_override,
    )
    record = _load_or_init_state(identity.pubkey_hex, path)
    history: List[Dict[str, Any]] = list(record.get("claim_history", []))
    claimed_tier_now = record.get("claimed_tier", "Rookie")

    if pending is None:
        # Nothing pending — but persist the init-shaped state file if
        # it didn't exist yet, so the on-disk state is always present
        # for audit. Use a write-only-if-changed guard to avoid
        # spurious file-system writes on every dm_home call.
        loaded = ceremony_state.load_state(path)
        if loaded is None:
            ceremony_state.save_state(
                pubkey_hex=identity.pubkey_hex,
                claimed_tier=claimed_tier_now,
                claim_history=history,
                path=path,
            )
        return {
            "status": "noop",
            "claimed_tier": claimed_tier_now,
            "balance": get_balance(),
        }

    # Mint each tier individually so per-tier idempotency keys protect
    # the ledger even on a multi-tier jump where a partial-write crash
    # could otherwise re-mint.
    minted_tiers: List[str] = []
    minted_total = 0

    # We snapshot wins_at_claim from the pending payload — even if a
    # subsequent leaderboard fetch shows a different number (rare, but
    # possible if the player wins another match between
    # pending_ceremony() and claim_pending()), the snapshot reflects
    # what was true at decision-time, which is what an audit cares
    # about.
    wins_at_claim = pending.wins_at_check
    prev_tier = pending.prev_tier

    for tier in pending.tiers_to_mint:
        reward = REWARD_SCHEDULE[tier]
        if reward <= 0:
            # Defensive — Rookie has reward=0. But Rookie should never
            # appear in tiers_to_mint (it's excluded by the
            # pending_ceremony enumeration). Skip rather than raise.
            continue
        idem_key = f"tier_up_{tier}"
        entry = append_tier_up_reward_entry(
            tier=tier,
            prev_tier=prev_tier,
            wins_at_claim=wins_at_claim,
            reward=reward,
            idempotency_key=idem_key,
            identity=identity,
        )
        if entry is None:
            # Ledger dedup hit — the entry already existed. State
            # might be lagging; advance it below regardless. We still
            # record a history row so the local file matches the
            # ledger view.
            history.append({
                "tier": tier,
                "claimed_at": _now_iso(),
                "reward": reward,
                "wins_at_claim": wins_at_claim,
                "ledger_entry_hash": None,  # entry pre-existed; hash unknown without re-scan
                "deduped": True,
            })
        else:
            history.append({
                "tier": tier,
                "claimed_at": entry["ts"],
                "reward": reward,
                "wins_at_claim": wins_at_claim,
                "ledger_entry_hash": entry_hash(entry),
            })
            minted_tiers.append(tier)
            minted_total += reward
        # Walk prev_tier forward so each successive ledger entry
        # records the actual tier it was promoted FROM.
        prev_tier = tier

    # Advance state. ``pending.pending_tier`` is the new high-water
    # mark — it's guaranteed > old claimed_tier by construction.
    new_claimed = pending.pending_tier
    ceremony_state.save_state(
        pubkey_hex=identity.pubkey_hex,
        claimed_tier=new_claimed,
        claim_history=history,
        path=path,
    )

    return {
        "status": "ok",
        "prev_tier": pending.prev_tier,
        "claimed_tier": new_claimed,
        "claimed_tiers": minted_tiers,
        "reward_total": minted_total,
        "wins_at_claim": wins_at_claim,
        "balance": get_balance(),
        "claim_history": history,
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_state_against_ledger(
    *,
    identity: Optional[Identity] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Cross-check: every claim_history entry has a matching ledger entry.

    Returns:
      ``{"ok": True,  "claims": N, "ledger_tier_up_entries": N}``
      ``{"ok": False, "missing": [...], "extra": [...]}``

    Used by the test suite + future support tooling. Never raises.
    """
    if identity is None:
        try:
            identity = load_identity()
        except FileNotFoundError:
            return {"ok": True, "claims": 0, "ledger_tier_up_entries": 0,
                    "note": "no identity"}

    from daimon.mining.ledger import _read_entries

    record = ceremony_state.load_state(path)
    history = (record or {}).get("claim_history", [])
    history_tiers = [h.get("tier") for h in history]

    entries = _read_entries()
    ledger_tiers = [e.get("tier") for e in entries
                    if e.get("kind") == "tier_up_reward"
                    and e.get("pubkey_hex") == identity.pubkey_hex]

    # Compare as multisets — same tier should appear at most once on
    # each side, but a mismatch in count is a bug worth surfacing.
    from collections import Counter
    h_counts = Counter(history_tiers)
    l_counts = Counter(ledger_tiers)
    missing = []  # in history but not in ledger
    extra = []    # in ledger but not in history
    for t, c in h_counts.items():
        if c > l_counts.get(t, 0):
            missing.append({"tier": t,
                            "history_count": c,
                            "ledger_count": l_counts.get(t, 0)})
    for t, c in l_counts.items():
        if c > h_counts.get(t, 0):
            extra.append({"tier": t,
                          "ledger_count": c,
                          "history_count": h_counts.get(t, 0)})

    if missing or extra:
        return {
            "ok": False,
            "missing": missing,
            "extra": extra,
            "claims": len(history),
            "ledger_tier_up_entries": len(ledger_tiers),
        }
    return {
        "ok": True,
        "claims": len(history),
        "ledger_tier_up_entries": len(ledger_tiers),
    }
