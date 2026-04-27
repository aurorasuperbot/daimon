"""Onboarding stage detection — pure read of local filesystem state.

DAIMON's product onboarding is a 5-stage state machine layered on top of the
existing engine primitives. Each stage corresponds to a concrete user action
that's the next forward step from where they currently are. The home card
and the agent both consult :func:`detect_stage` to decide what UX to surface.

## Stages

The walk is **strictly ordered** — :func:`detect_stage` returns the FIRST
gate still open. A new player passes through them in order; an
existing player typically lands on ``GRADUATED`` immediately and the
home card hides the onboarding banner.

  1. ``BOOTSTRAP``    — No identity exists. Next: ``dm_onboard``.
  2. ``ASSET_LOAD``   — Identity exists but the card-art manifest is missing
                        (e.g. ``dm_onboard`` ran but failed at the manifest
                        step, or the user is mid-prefetch on a slow link).
                        Next: re-run ``dm_onboard`` (idempotent).
  3. ``FIRST_PULL``   — Identity + manifest, but collection is empty.
                        Next: ``dm_pull``.
  4. ``FIRST_MATCH``  — Owns ≥1 card, but no match has been recorded yet.
                        Next: ``dm_match_npc("Sparring Sam")`` (rank-1
                        Rookie). The recommended NPC is resolved
                        dynamically — if Sparring Sam disappears from the
                        roster we fall back to the first NPC in the
                        Rookie tier.
  5. ``MINING_HOOK``  — Played + has cards, but the Claude Code
                        ``PostToolUse`` hook isn't installed. Without the
                        hook the player only earns currency from explicit
                        actions (pulls, quests, tier-ups) — the
                        agentic-mining loop never fires. Next:
                        ``daimon mine install-hook``.
  6. ``GRADUATED``    — All five gates cleared. The home card hides the
                        onboarding banner and the agent stops nudging.

## Read-only contract

This module never writes anything. ``detect_stage()`` is pure and idempotent.
``dm_home`` calls it on every render — that path MUST remain a read-only
snapshot, so the detector can't have side effects (no marker files, no
counters, no telemetry writes from inside the read).

## Why no GRADUATED marker file?

It would simplify things — write ``~/.config/daimon/onboarding_complete.json``
once we hit GRADUATED and short-circuit on subsequent calls. We deliberately
DON'T do that:

  * Each gate is independently meaningful. If a user uninstalls the mining
    hook, they SHOULD see "install mining hook" again in their home card —
    that's not a regression, it's the system surfacing real state.
  * A marker file is a separate source of truth that can drift from the
    underlying gates. The current design has exactly one source of truth
    per gate (the file/dir it inspects).
  * Skipping the gates only saves five ``Path.exists()`` calls. Not worth
    a parallel state file.

The only stage that's truly one-shot is FIRST_PULL/FIRST_MATCH (you can't
"un-play" your first match), but those gates clear naturally once the
buffer or collection has the relevant entries — and the buffer is
append-only in normal operation.

## Test surface

See ``tests/test_onboarding_stages.py`` for the per-stage unit tests +
the full end-to-end progression test that walks a fake home directory
through all six states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OnboardingStage(str, Enum):
    """The six possible states of a player's onboarding journey.

    Inherits from ``str`` so the enum value is JSON-serialisable verbatim
    (no custom encoder needed in the MCP layer).
    """
    BOOTSTRAP = "bootstrap"
    ASSET_LOAD = "asset_load"
    FIRST_PULL = "first_pull"
    FIRST_MATCH = "first_match"
    MINING_HOOK = "mining_hook"
    GRADUATED = "graduated"


# Display ordering — same as enum declaration order, but explicit so callers
# don't depend on ``Enum.__members__`` insertion-order which is a CPython
# implementation detail (PEP 435 doesn't formally guarantee it).
STAGE_ORDER = (
    OnboardingStage.BOOTSTRAP,
    OnboardingStage.ASSET_LOAD,
    OnboardingStage.FIRST_PULL,
    OnboardingStage.FIRST_MATCH,
    OnboardingStage.MINING_HOOK,
    OnboardingStage.GRADUATED,
)

# Total non-graduated steps — what the home card uses to render
# "Step 2 of 5" style progress indicators. GRADUATED is excluded because
# it's the terminal state, not a step.
TOTAL_STAGES = len(STAGE_ORDER) - 1  # 5


def stage_index(stage: OnboardingStage) -> int:
    """Zero-indexed position of ``stage`` in :data:`STAGE_ORDER`. GRADUATED == 5."""
    return STAGE_ORDER.index(stage)


@dataclass(frozen=True)
class OnboardingState:
    """Snapshot of where a player is in the onboarding journey.

    Attributes:
      stage: which gate is currently open (or GRADUATED).
      step: 1-based position in the journey (1..5; GRADUATED is 6).
      total: ``TOTAL_STAGES`` (always 5) — convenience for callers
        rendering "Step N of M" progress.
      title: short display title for the home card banner.
      blurb: one-sentence description of what the player should do next.
      cta_label: button text for the home card CTA. Empty string when
        the stage doesn't have an actionable button (e.g. GRADUATED, or
        ASSET_LOAD where the right action is to retry the whole
        ``dm_onboard`` from outside the chat).
      cta_message: what the button posts into chat. The user's local
        Claude Code mention-watcher reacts to it. Empty string when
        ``cta_label`` is empty.
      signals: per-stage diagnostic dict — exposes the underlying signal
        the detector used (e.g. ``{"identity_present": False}``) for
        agent telemetry and debugging. NOT for end-user display.
    """
    stage: OnboardingStage
    step: int
    total: int
    title: str
    blurb: str
    cta_label: str
    cta_message: str
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "step": self.step,
            "total": self.total,
            "title": self.title,
            "blurb": self.blurb,
            "cta_label": self.cta_label,
            "cta_message": self.cta_message,
            "signals": dict(self.signals),
        }


# ---------------------------------------------------------------------------
# Per-stage signal probes — each is best-effort and never raises.
# ---------------------------------------------------------------------------

def _identity_present() -> bool:
    """True iff a private key file exists at the canonical path.

    We deliberately don't ``load_identity()`` here — loading would
    decrypt + parse the PEM, which is overkill for a yes/no probe and
    would surface CryptographyExceptions on a corrupt key (we want a
    corrupt key to LOOK like "no identity" so the user gets a fresh
    onboard prompt, which is what they actually need).
    """
    try:
        from daimon.identity.keys import PRIVATE_KEY_PATH
        return PRIVATE_KEY_PATH.exists()
    except Exception:
        return False


def _manifest_installed() -> bool:
    """True iff the active art-pack manifest has been fetched + persisted.

    The pack name is the engine default; we don't know which pack the
    user might have customized to, so we accept "any manifest under
    art/<pack>/.manifest.json". Realistically there's only one active
    pack at a time (v1_alpha as of 2026-04).
    """
    try:
        from daimon.update.paths import manifest_path
        return manifest_path().exists()
    except Exception:
        return False


def _collection_count() -> int:
    """Number of card serials in the local collection. Returns 0 on any error."""
    try:
        from daimon import collection
        return collection.count()
    except Exception:
        return 0


def _has_played_match() -> bool:
    """True iff at least one match-kind entry exists in the mining buffer.

    The buffer is append-only and capped (~250-500 entries) — we don't
    care about historical match count, only the boolean "have they ever
    played one?". An over-rotated buffer where the only match was 6
    months ago would re-fire FIRST_MATCH, which is fine: the player has
    been away long enough that a refresher prompt is helpful.
    """
    try:
        from daimon.mining import buffer as _buffer
        # Tail a generous window then filter — this is the same pattern
        # ``_recent_from_buffer`` uses in mcp/server.py.
        window = _buffer.tail(2000)
        return bool(_buffer.by_kind(window, "match"))
    except Exception:
        return False


def _mining_hook_installed() -> bool:
    """True iff the Claude Code settings.json has the daimon PostToolUse hook.

    The detection mirrors ``mining/installer.hook_status`` but doesn't
    invoke that helper directly because it returns a richer dict — for a
    yes/no gate we only need the boolean. Conservative: if the settings
    file is missing or unparseable we report False (the user needs to
    install) rather than crash.
    """
    try:
        from daimon.mining.installer import (
            DEFAULT_SETTINGS_PATH,
            _has_daimon_hook,
            _read_settings,
        )
    except Exception:
        return False
    if not DEFAULT_SETTINGS_PATH.exists():
        return False
    try:
        data = _read_settings(DEFAULT_SETTINGS_PATH)
    except Exception:
        return False
    hooks = data.get("hooks") or {}
    if not isinstance(hooks, dict):
        return False
    post_tool_use = hooks.get("PostToolUse")
    if not isinstance(post_tool_use, list):
        return False
    return bool(_has_daimon_hook(post_tool_use))


def _first_match_npc_name() -> str:
    """Resolve the canonical first-match opponent.

    Strategy:
      1. Prefer "Sparring Sam" if she's in the Rookie tier (the
         design-intent first opponent).
      2. Otherwise the rank-1 NPC in the Rookie tier.
      3. Otherwise any Rookie NPC (sorted by id for stability).
      4. Otherwise the literal string "Sparring Sam" as a fallback —
         the agent will report the recommendation didn't resolve and
         the player can pick manually.
    """
    try:
        from daimon.npcs import list_npcs
        rookies = [n for n in list_npcs("rookie")]
        if not rookies:
            return "Sparring Sam"
        for n in rookies:
            if n.name == "Sparring Sam":
                return n.name
        # Fall back to lowest rank, then alphabetical id.
        rookies.sort(key=lambda x: (getattr(x, "rank", 999), x.npc_id))
        return rookies[0].name
    except Exception:
        return "Sparring Sam"


# ---------------------------------------------------------------------------
# Stage builders — small private helpers so ``detect_stage`` reads as a
# decision tree rather than a wall of dataclass kwargs.
# ---------------------------------------------------------------------------

def _state(
    stage: OnboardingStage,
    *,
    title: str,
    blurb: str,
    cta_label: str = "",
    cta_message: str = "",
    signals: Optional[Dict[str, Any]] = None,
) -> OnboardingState:
    return OnboardingState(
        stage=stage,
        step=stage_index(stage) + 1,
        total=TOTAL_STAGES,
        title=title,
        blurb=blurb,
        cta_label=cta_label,
        cta_message=cta_message,
        signals=dict(signals or {}),
    )


def _bootstrap() -> OnboardingState:
    return _state(
        OnboardingStage.BOOTSTRAP,
        title="Bootstrap your DAIMON identity",
        blurb=(
            "Generate your ed25519 keypair + 24-word recovery phrase. "
            "Mining, pulls, and battles all sign through this identity."
        ),
        cta_label="Initialize DAIMON",
        cta_message="@daimon onboard",
        signals={"identity_present": False},
    )


def _asset_load() -> OnboardingState:
    return _state(
        OnboardingStage.ASSET_LOAD,
        title="Finish loading your starter pack",
        blurb=(
            "Identity is set, but the card-art manifest didn't fully "
            "land. Re-running onboard is idempotent — it'll resume the "
            "manifest fetch + starter prefetch."
        ),
        cta_label="Resume onboarding",
        cta_message="@daimon onboard",
        signals={"identity_present": True, "manifest_installed": False},
    )


def _first_pull() -> OnboardingState:
    return _state(
        OnboardingStage.FIRST_PULL,
        title="Pull your first card",
        blurb=(
            "Your collection is empty. Spend your starter currency on a "
            "gacha pull — every pull mints a brand-new serial owned by you."
        ),
        cta_label="Pull a card",
        cta_message="@daimon pull",
        signals={"collection_count": 0},
    )


def _first_match() -> OnboardingState:
    npc_name = _first_match_npc_name()
    return _state(
        OnboardingStage.FIRST_MATCH,
        title="Fight your first match",
        blurb=(
            f"You have cards. Time to use them — challenge {npc_name} "
            "(rank-1 Rookie). The match runs deterministically against a "
            "fixed loadout, so your first battle is a clean baseline."
        ),
        cta_label=f"Battle {npc_name}",
        cta_message=f"@daimon battle {npc_name}",
        signals={"matches_played": 0, "first_match_opponent": npc_name},
    )


def _mining_hook(collection_count: int) -> OnboardingState:
    return _state(
        OnboardingStage.MINING_HOOK,
        title="Wire up agentic mining",
        blurb=(
            "You're playing — now make Claude Code earn currency for you. "
            "The PostToolUse hook awards a tiny amount of currency on "
            "every tool call your agent makes. Without it, you only earn "
            "from explicit pulls / quests / tier-ups."
        ),
        cta_label="Install mining hook",
        # The hook installer is a CLI command; the agent can shell it out
        # via Bash on the user's behalf, OR they can run it themselves.
        cta_message="@daimon install mining hook",
        signals={
            "hook_installed": False,
            "collection_count": collection_count,
        },
    )


def _graduated(*, signals: Dict[str, Any]) -> OnboardingState:
    return _state(
        OnboardingStage.GRADUATED,
        title="",
        blurb="",
        cta_label="",
        cta_message="",
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_stage() -> OnboardingState:
    """Walk the gates in order; return the first one that's still open.

    Side-effect free. Calls only into read-only filesystem probes. Safe
    to call from ``dm_home`` (which is itself a read-only snapshot tool)
    on every render.

    Returns an :class:`OnboardingState` describing the current stage.
    Never raises — every probe is wrapped in ``try/except`` and returns
    the most-conservative interpretation on error (treat-as-not-cleared,
    so the player gets the prompt rather than being silently advanced).
    """
    # --- Stage 1: Bootstrap ------------------------------------------------
    if not _identity_present():
        return _bootstrap()

    # --- Stage 2: Asset load ----------------------------------------------
    if not _manifest_installed():
        return _asset_load()

    # --- Stage 3: First pull ----------------------------------------------
    coll_count = _collection_count()
    if coll_count == 0:
        return _first_pull()

    # --- Stage 4: First match ---------------------------------------------
    if not _has_played_match():
        return _first_match()

    # --- Stage 5: Mining hook ---------------------------------------------
    if not _mining_hook_installed():
        return _mining_hook(coll_count)

    # --- Graduated --------------------------------------------------------
    return _graduated(signals={
        "identity_present": True,
        "manifest_installed": True,
        "collection_count": coll_count,
        "matches_played": True,
        "hook_installed": True,
    })


__all__ = [
    "OnboardingStage",
    "OnboardingState",
    "STAGE_ORDER",
    "TOTAL_STAGES",
    "detect_stage",
    "stage_index",
]
