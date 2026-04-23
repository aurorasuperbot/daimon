"""Deterministic combat resolver.

Algorithm (V2 — monster pivot):
  1. Build UnitState lists for both sides from loadouts.
  2. Fire ON_BATTLE_START triggers (side A first, by position order, then side B).
  3. For each round (max 5):
       a. Tick status conditions (burn dmg, chill countdown, root clears, etc.)
       b. Fire ON_ROUND_START triggers.
       c. Build action queue: every alive unit acts, ordered by effective_spd
          (higher spd first; ties broken by side then position — fully deterministic).
       d. For each acting unit (in order):
            - If dead or rooted, skip (rooted still ticks status).
            - Pick target = lowest-HP enemy alive (ties broken by position index).
            - If no target, side wins early.
            - Fire ON_ATTACK triggers on attacker.
            - Compute damage = max(0, atk - target.def) × element_multiplier.
              Apply shield first.
            - Fire ON_TAKE_DAMAGE triggers on target.
            - If target dies, fire ON_DEATH and ON_ALLY_DEATH triggers.
       e. Record RoundLog.
       f. End early if either side fully wiped.
  4. Determine winner by surviving HP totals.

The engine reads ZERO strings from cards. Only Card.atk/def/hp/spd/element/triggers.
Even card_id is opaque to combat — used only for trace logging.

A.4.a (2026-04-21): a structured `CombatEvent` stream is emitted alongside the
existing string log. Every `log.append(...)` site has a sibling `events.append(...)`
when an `events` list is threaded in. The string log behavior is unchanged —
existing tests require zero edits. Reactive triggers (ON_TAKE_DAMAGE counters,
ON_DEATH effects, ON_ALLY_DEATH cascades) nest under their parent action's
`triggers` list. ON_BATTLE_START / ON_ROUND_START events are top-level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional

from daimon.engine.conditions import compile_condition
from daimon.engine.elements import element_multiplier
from daimon.engine.loadout import Loadout
from daimon.engine.rng import SeededRng
from daimon.engine.types import (
    CombatEvent,
    EffectOp,
    Element,
    MatchResult,
    RoundLog,
    StatusCondition,
    TargetFilter,
    Trigger,
    TriggerWhen,
    UnitState,
)

ROUND_CAP = 5

# Phase-2 status tick magnitudes (locked):
#   BURN = 3 dmg/round
#   POISON = 2 dmg/round (smaller-but-distinct DOT)
_BURN_TICK_DMG = 3
_POISON_TICK_DMG = 2

# ON_LOW_HP threshold — trigger fires once when self.hp drops to ≤ 25% of card.hp.
# Integer floor division keeps the boundary deterministic across all hp_max values.
_LOW_HP_DENOM = 4

# Phase 4f-engine re-entrancy caps (charter §21.5).
# These guard against infinite loops when triggers cascade into same-class triggers.
# Caps are engine-enforced; cards cannot opt out.
_HEAL_CASCADE_CAP = 4    # ON_HEAL_RECEIVED → HEAL → ON_HEAL_RECEIVED nested chain;
                         # max 4 nested heals per single source-heal event.
_THORNS_REFLECT_CAP = 2  # THORNS reflection can trigger attacker's ON_DAMAGE_TAKEN,
                         # which can re-trigger this unit's THORNS; max 2 reflections
                         # per source-attack to prevent infinite loops.
_ALLY_DEATH_CAP = 8      # ON_ALLY_DEATH cascade depth (with L5 doubling on top of
                         # 6-card teams, charter §22.2 L5 sets this at 8).

# Phase 4f-engine extra-action default cap (charter §21.1 + §22.2 L4).
# Default = 1 (one extra action per unit per round). L4 (`tempest_apex`) raises to 2.
_EXTRA_ACTION_DEFAULT_CAP = 1
_EXTRA_ACTION_L4_CAP = 2

# Phase 4f-engine mutation IDs as constants — string literals are typo-prone
# at every check site; promoting to module constants gives one source of truth.
_MUT_L1 = "L1"   # magma_tyrant — every damage instance applies +1 burn stack
_MUT_L2 = "L2"   # worldroot_sentinel — every ally has THORNS 2
_MUT_L3 = "L3"   # tide_empress — every heal trickles +1 to all allies
_MUT_L4 = "L4"   # tempest_apex — extra-action cap raised 1→2
_MUT_L5 = "L5"   # voidking_morr — ON_ALLY_DEATH triggers fire ×2
_MUT_L6 = "L6"   # world_eater — team.distinct_elements +2 for FLUX cards
_MUT_L2_THORNS_BONUS = 2  # bonus thorns added to every ally under L2
_MUT_L3_TRICKLE = 1       # silent +1 heal to every other ally per heal event
_MUT_L6_DISTINCT_BONUS = 2  # +2 to team.distinct_elements read for FLUX cards

# Cached compile of trigger conditions. Conditions are short DSL strings whose
# parse cost is non-trivial relative to fire frequency; cache by string identity
# so repeated triggers across cards/units share one compiled callable.
@lru_cache(maxsize=512)
def _cached_compile(expr: str):
    return compile_condition(expr)


# ---------------------------------------------------------------------------
# Phase 4f-engine: per-effect-call context.
#
# Pre-Phase 4f, _apply_effect was self-contained — it took the target unit, a
# log, and an optional events sink, and ran one effect. Phase 4f's new ops break
# that purity:
#   - HEAL → must fire ON_HEAL_RECEIVED on target (needs allies/enemies)
#   - GRANT_EXTRA_ACTION → must call _resolve_action recursively (needs sides + rng)
#   - SACRIFICE_SELF → must fire ON_DEATH + ON_ALLY_DEATH cascade (needs sides + rng)
#   - Re-entry caps for HEAL chain, THORNS reflection, ON_ALLY_DEATH cascade
#
# Rather than thread 7 kwargs through every call site, we bundle them into one
# _FxCtx struct that flows down the call tree. _apply_effect accepts ctx as an
# optional kwarg with default None — when None, the new follow-up logic is
# skipped (back-compat for any test that builds a unit + calls _apply_effect
# directly without going through _fire_triggers_for_unit).
#
# Cap counters (heal_depth, thorns_depth, ally_death_depth) are passed by VALUE
# (immutable int) so each branch of the cascade tree carries its own depth — a
# trigger that fires HEAL doesn't permanently bump every sibling's heal_depth.
# Use _ctx_with(...) to clone with incremented depth for cascading calls.
# ---------------------------------------------------------------------------

@dataclass
class _FxCtx:
    """Engine context threaded through trigger / effect resolution.

    `side_a` / `side_b` are the live team lists (mutated as units die / heal).
    `rng` is the seeded RNG for RANDOM_* targets. `round_number` is the current
    round (0 during ON_BATTLE_START).

    Cap counters track depth in cascade trees:
      - heal_depth: incremented per nested ON_HEAL_RECEIVED fire
      - thorns_depth: incremented per nested THORNS reflection on the same source-attack
      - ally_death_depth: incremented per nested ON_ALLY_DEATH event in the same cascade
    """
    side_a: List[UnitState]
    side_b: List[UnitState]
    rng: SeededRng
    round_number: int = 0
    heal_depth: int = 0
    thorns_depth: int = 0
    ally_death_depth: int = 0

    def allies_of(self, unit: UnitState) -> List[UnitState]:
        return self.side_a if unit.side == 0 else self.side_b

    def enemies_of(self, unit: UnitState) -> List[UnitState]:
        return self.side_b if unit.side == 0 else self.side_a


def _ctx_with(ctx: _FxCtx, **overrides: int) -> _FxCtx:
    """Clone an FxCtx with one or more depth counters overridden. Sides + rng
    + round_number are shared by reference (intentional — same engine state).
    """
    return _FxCtx(
        side_a=ctx.side_a,
        side_b=ctx.side_b,
        rng=ctx.rng,
        round_number=ctx.round_number,
        heal_depth=overrides.get("heal_depth", ctx.heal_depth),
        thorns_depth=overrides.get("thorns_depth", ctx.thorns_depth),
        ally_death_depth=overrides.get("ally_death_depth", ctx.ally_death_depth),
    )


# ---------------------------------------------------------------------------
# Phase 4f-engine: legendary rule-change mutation lookup.
#
# A team's mutation is "active" iff at least one ALIVE unit on that team carries
# the corresponding `rule_change` tag. When the legendary dies, the mutation
# snaps back to default (charter §22.1).
#
# Mutations are checked at the resolution site of the rule they change, not
# registered globally. This keeps the engine state minimal — no separate
# "active mutations" set to maintain — and naturally handles death/revival.
# ---------------------------------------------------------------------------

def _team_has_mutation(team: List[UnitState], mutation_id: str) -> bool:
    """Return True iff any alive unit on `team` carries `rule_change == mutation_id`."""
    for u in team:
        if u.alive and u.card.rule_change == mutation_id:
            return True
    return False


def _extra_action_cap(team: List[UnitState]) -> int:
    """Cap on extra actions per unit per round for `team`.

    Default = _EXTRA_ACTION_DEFAULT_CAP (1).
    Raised to _EXTRA_ACTION_L4_CAP (2) iff L4 (`tempest_apex`) is alive on team.
    """
    if _team_has_mutation(team, _MUT_L4):
        return _EXTRA_ACTION_L4_CAP
    return _EXTRA_ACTION_DEFAULT_CAP


def _effective_thorns(unit: UnitState) -> int:
    """Effective thorns_value for `unit`, accounting for L2 mutation (worldroot_sentinel).

    L2 grants every alive ally +2 thorns (charter §22.2 L2). This stacks
    additively with explicit THORNS ops (a unit with intrinsic THORNS 3 +
    L2 alive on team = THORNS 5).
    """
    base = unit.thorns_value
    # Sides aren't directly available here; the caller passes in the team via
    # the ctx where this is invoked. Wrap below in a helper that knows the team.
    return base


def _effective_thorns_on_team(unit: UnitState, team: List[UnitState]) -> int:
    """Same as _effective_thorns but with team-level L2 lookup."""
    base = unit.thorns_value
    if _team_has_mutation(team, _MUT_L2):
        base += _MUT_L2_THORNS_BONUS
    return base


# ---------------------------------------------------------------------------
# Internal helpers — small enums-to-strings + hp snapshot.
#
# These bridge engine-native types to the schema vocabulary used in
# CombatEvent (which mirrors play.schema.ActionKind). The adapter
# (play/adapter.py) does the second hop (engine -> schema model objects);
# CombatEvent itself sits at the engine seam.
# ---------------------------------------------------------------------------

# EffectOp -> ActionKind (string). The kinds match play.schema.ActionKind.
_EFFECT_OP_TO_KIND: dict[int, str] = {
    int(EffectOp.BUFF_ATK):     "buff",
    int(EffectOp.DEBUFF_ATK):   "debuff",
    int(EffectOp.BUFF_DEF):     "buff",
    int(EffectOp.DEBUFF_DEF):   "debuff",
    int(EffectOp.HEAL):         "heal",
    int(EffectOp.DAMAGE):       "damage",
    int(EffectOp.ADD_SHIELD):   "shield",
    int(EffectOp.BUFF_SPD):     "buff",
    # Phase-2 ops:
    int(EffectOp.APPLY_BURN):    "status",
    int(EffectOp.APPLY_STUN):    "status",
    int(EffectOp.APPLY_SILENCE): "status",
    int(EffectOp.APPLY_TAUNT):   "status",
    int(EffectOp.APPLY_POISON):  "status",
    # LIFESTEAL is recorded as 'damage' on the target hit; the heal-back is a
    # nested 'heal' event under it. The op-level kind tracks the primary outcome.
    int(EffectOp.LIFESTEAL):     "damage",
    # Phase 4f-engine ops:
    int(EffectOp.APPLY_BURN_STACK):   "status",   # stacks tracked as a status-flavor event
    int(EffectOp.THORNS):             "buff",     # passive self-buff (sets thorns_value)
    int(EffectOp.GRANT_EXTRA_ACTION): "buff",     # tempo grant; recorded as buff
    int(EffectOp.SACRIFICE_SELF):     "death",    # self-destruct; primary outcome IS the death
}

# TriggerWhen -> reason string (matches the engine seam vocabulary).
_TRIGGER_REASON: dict[int, str] = {
    int(TriggerWhen.ON_BATTLE_START):    "ON_BATTLE_START",
    int(TriggerWhen.ON_ROUND_START):     "ON_ROUND_START",
    int(TriggerWhen.ON_ATTACK):          "ON_ATTACK",
    int(TriggerWhen.ON_TAKE_DAMAGE):     "ON_TAKE_DAMAGE",
    int(TriggerWhen.ON_DEATH):           "ON_DEATH",
    int(TriggerWhen.ON_ALLY_DEATH):      "ON_ALLY_DEATH",
    # Phase-2 whens:
    int(TriggerWhen.ON_TURN_END):        "ON_TURN_END",
    int(TriggerWhen.ON_KILL):            "ON_KILL",
    int(TriggerWhen.ON_LOW_HP):          "ON_LOW_HP",
    int(TriggerWhen.ON_OPENING_ATTACK):  "ON_OPENING_ATTACK",
    # Phase 4f-engine whens:
    int(TriggerWhen.ON_HEAL_RECEIVED):        "ON_HEAL_RECEIVED",
    int(TriggerWhen.ON_DAMAGE_TAKEN):         "ON_DAMAGE_TAKEN",
    int(TriggerWhen.ON_EXTRA_ACTION_GRANTED): "ON_EXTRA_ACTION_GRANTED",
}

# Status name strings — used for log line formatting + CombatEvent.status_applied.
_STATUS_NAME: dict[int, str] = {
    int(StatusCondition.BURN):    "BURN",
    int(StatusCondition.CHILL):   "CHILL",
    int(StatusCondition.ROOT):    "ROOT",
    int(StatusCondition.CHARGE):  "CHARGE",
    int(StatusCondition.STUN):    "STUN",
    int(StatusCondition.SILENCE): "SILENCE",
    int(StatusCondition.TAUNT):   "TAUNT",
    int(StatusCondition.POISON):  "POISON",
}


def _hp_snapshot(*units: UnitState) -> dict[tuple[int, int], int]:
    """Snapshot the current HP of one or more units as an hp_after dict.

    Engine HP can go negative briefly during damage application; the schema
    convention is "0 means dead", so we clamp here. Keys are (side, position).
    """
    return {(u.side, u.position): max(0, u.hp) for u in units}


# ---------------------------------------------------------------------------
# Build / lifecycle helpers
# ---------------------------------------------------------------------------

def _build_units(loadout: Loadout, side: int) -> List[UnitState]:
    return [
        UnitState(card=c, position=i, side=side, hp=c.hp)
        for i, c in enumerate(loadout.cards)
    ]


def _alive(units: List[UnitState]) -> List[UnitState]:
    return [u for u in units if u.alive]


def _pick_targets(
    trigger: Trigger,
    actor: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
) -> List[UnitState]:
    alive_allies = _alive(allies)
    alive_enemies = _alive(enemies)
    if trigger.target == TargetFilter.SELF:
        return [actor] if actor.alive else []
    if trigger.target == TargetFilter.ALL_ALLIES:
        return alive_allies
    if trigger.target == TargetFilter.ALL_ENEMIES:
        return alive_enemies
    if trigger.target == TargetFilter.LOWEST_HP_ENEMY:
        if not alive_enemies:
            return []
        return [min(alive_enemies, key=lambda u: (u.hp, u.position))]
    if trigger.target == TargetFilter.HIGHEST_HP_ENEMY:
        if not alive_enemies:
            return []
        return [max(alive_enemies, key=lambda u: (u.hp, -u.position))]
    if trigger.target == TargetFilter.RANDOM_ENEMY:
        return [rng.choice(alive_enemies)] if alive_enemies else []
    if trigger.target == TargetFilter.RANDOM_ALLY:
        return [rng.choice(alive_allies)] if alive_allies else []
    return []


# ---------------------------------------------------------------------------
# Effect application — string log + structured event emission, in lockstep.
#
# The `events` parameter, when not None, is the destination list for the new
# CombatEvent. Pass `None` to skip event emission (useful for tests and call
# sites that don't care about structured events). Pass `parent.triggers` to
# nest a reactive event under its parent action.
# ---------------------------------------------------------------------------

def _apply_effect(
    op: EffectOp,
    value: int,
    target: UnitState,
    log: List[str],
    actor_id: str,
    attacker: Optional[UnitState] = None,
    *,
    events: Optional[List[CombatEvent]] = None,
    reason: Optional[str] = None,
    ctx: Optional[_FxCtx] = None,
) -> None:
    # SACRIFICE_SELF and HEAL can target dead/null states intentionally; everything
    # else short-circuits on dead targets.
    if not target.alive and op not in (EffectOp.HEAL, EffectOp.SACRIFICE_SELF):
        return

    actor_unit = attacker  # actor_unit may be None for status ticks (e.g. burn)

    def _emit(kind: str, amount: Optional[int], log_line: str) -> None:
        if events is None or actor_unit is None:
            return
        events.append(CombatEvent(
            kind=kind,
            actor_side=actor_unit.side,
            actor_position=actor_unit.position,
            actor_card_id=actor_unit.card.card_id,
            target_side=target.side,
            target_position=target.position,
            target_card_id=target.card.card_id,
            amount=amount,
            hp_after=_hp_snapshot(target),
            reason=reason,
            log_line=log_line,
        ))

    if op == EffectOp.BUFF_ATK:
        target.atk_mod += value
        line = f"{actor_id} buffs ATK of {target.card.card_id} by +{value}"
        log.append(line)
        _emit("buff", value, line)
    elif op == EffectOp.DEBUFF_ATK:
        target.atk_mod -= value
        line = f"{actor_id} debuffs ATK of {target.card.card_id} by -{value}"
        log.append(line)
        _emit("debuff", value, line)
    elif op == EffectOp.BUFF_DEF:
        target.def_mod += value
        line = f"{actor_id} buffs DEF of {target.card.card_id} by +{value}"
        log.append(line)
        _emit("buff", value, line)
    elif op == EffectOp.DEBUFF_DEF:
        target.def_mod -= value
        line = f"{actor_id} debuffs DEF of {target.card.card_id} by -{value}"
        log.append(line)
        _emit("debuff", value, line)
    elif op == EffectOp.HEAL:
        if target.alive:
            target.hp += value
            line = f"{actor_id} heals {target.card.card_id} for {value}"
            log.append(line)
            _emit("heal", value, line)
            # Phase 4f-engine: fire ON_HEAL_RECEIVED on target (charter §21.2)
            # AFTER the heal lands. Capped at _HEAL_CASCADE_CAP nested heals
            # per source-heal event (charter §21.5).
            if ctx is not None and ctx.heal_depth < _HEAL_CASCADE_CAP:
                next_ctx = _ctx_with(ctx, heal_depth=ctx.heal_depth + 1)
                _fire_triggers_for_unit(
                    TriggerWhen.ON_HEAL_RECEIVED, target,
                    next_ctx.allies_of(target), next_ctx.enemies_of(target),
                    next_ctx.rng, log,
                    events=events, round_number=next_ctx.round_number,
                    ctx=next_ctx,
                )
            # L3 (`tide_empress`) mutation: every heal silently trickles +1 HP
            # to every OTHER alive ally on the healed unit's team. The trickle
            # is intentionally NOT a HEAL op — it does not fire ON_HEAL_RECEIVED
            # and does not itself trickle (cascade-broken — charter §22.2 L3).
            if ctx is not None:
                team = ctx.allies_of(target)
                if _team_has_mutation(team, _MUT_L3):
                    for ally in team:
                        if ally is target or not ally.alive:
                            continue
                        ally.hp += _MUT_L3_TRICKLE
                        trickle_line = (
                            f"L3 trickle: {ally.card.card_id} heals "
                            f"{_MUT_L3_TRICKLE}"
                        )
                        log.append(trickle_line)
                        if events is not None:
                            events.append(CombatEvent(
                                kind="heal",
                                actor_side=ally.side,
                                actor_position=ally.position,
                                actor_card_id=ally.card.card_id,
                                target_side=ally.side,
                                target_position=ally.position,
                                target_card_id=ally.card.card_id,
                                amount=_MUT_L3_TRICKLE,
                                hp_after=_hp_snapshot(ally),
                                reason="MUTATION_L3",
                                log_line=trickle_line,
                            ))
    elif op == EffectOp.DAMAGE:
        # Non-attack damage (trigger-sourced) still respects element.
        # The element-multiplier line is folded into the damage event's
        # log_line as a prefix rather than emitted as a separate event —
        # one event per outcome, multiplier is flavor on top of that outcome.
        final_value = value
        prefix_line = ""
        if attacker is not None:
            mult = element_multiplier(attacker.card.element, target.card.element)
            if mult != 1.0:
                final_value = max(0, math.ceil(value * mult))
                if final_value != value:
                    prefix_line = (
                        f"{actor_id}'s element vs {target.card.card_id}: "
                        f"{mult}× ({value}→{final_value})"
                    )
                    log.append(prefix_line)
        _take_damage(
            target, final_value, log,
            source=actor_id, source_unit=attacker,
            events=events, reason=reason, log_prefix=prefix_line,
            ctx=ctx,
        )
    elif op == EffectOp.ADD_SHIELD:
        target.shield += value
        line = f"{actor_id} shields {target.card.card_id} for {value}"
        log.append(line)
        _emit("shield", value, line)
    elif op == EffectOp.BUFF_SPD:
        target.spd_mod += value
        line = f"{actor_id} buffs SPD of {target.card.card_id} by +{value}"
        log.append(line)
        _emit("buff", value, line)
    elif op == EffectOp.APPLY_BURN:
        _apply_status(target, StatusCondition.BURN, value, log,
                      actor_id=actor_id, attacker=attacker,
                      events=events, reason=reason)
    elif op == EffectOp.APPLY_STUN:
        _apply_status(target, StatusCondition.STUN, value, log,
                      actor_id=actor_id, attacker=attacker,
                      events=events, reason=reason)
    elif op == EffectOp.APPLY_SILENCE:
        _apply_status(target, StatusCondition.SILENCE, value, log,
                      actor_id=actor_id, attacker=attacker,
                      events=events, reason=reason)
    elif op == EffectOp.APPLY_TAUNT:
        _apply_status(target, StatusCondition.TAUNT, value, log,
                      actor_id=actor_id, attacker=attacker,
                      events=events, reason=reason)
    elif op == EffectOp.APPLY_POISON:
        _apply_status(target, StatusCondition.POISON, value, log,
                      actor_id=actor_id, attacker=attacker,
                      events=events, reason=reason)
    elif op == EffectOp.LIFESTEAL:
        # value semantics: damage dealt to target. Attacker heals ceil(value/2).
        # The damage half respects element multiplier (same path as DAMAGE op);
        # the heal half is computed off the *intended* value (not the post-mult
        # final), so lifesteal is a stable % of the card's design rather than a
        # multiplier-amplified swing. Attacker is required.
        if attacker is None:
            return
        final_value = value
        prefix_line = ""
        mult = element_multiplier(attacker.card.element, target.card.element)
        if mult != 1.0:
            final_value = max(0, math.ceil(value * mult))
            if final_value != value:
                prefix_line = (
                    f"{actor_id}'s element vs {target.card.card_id}: "
                    f"{mult}× ({value}→{final_value})"
                )
                log.append(prefix_line)
        _take_damage(
            target, final_value, log,
            source=actor_id, source_unit=attacker,
            events=events, reason=reason, log_prefix=prefix_line,
            ctx=ctx,
        )
        heal = math.ceil(value / 2)
        if attacker.alive and heal > 0:
            attacker.hp += heal
            line = f"{actor_id} drains {heal} hp from {target.card.card_id}"
            log.append(line)
            if events is not None:
                events.append(CombatEvent(
                    kind="heal",
                    actor_side=attacker.side,
                    actor_position=attacker.position,
                    actor_card_id=attacker.card.card_id,
                    target_side=attacker.side,
                    target_position=attacker.position,
                    target_card_id=attacker.card.card_id,
                    amount=heal,
                    hp_after=_hp_snapshot(attacker),
                    reason=reason,
                    log_line=line,
                ))
            # Phase 4f-engine: LIFESTEAL heal-back fires ON_HEAL_RECEIVED on
            # the attacker (self-heal counts — charter §21.2 "any source").
            # Subject to same heal-cascade cap.
            if ctx is not None and ctx.heal_depth < _HEAL_CASCADE_CAP:
                next_ctx = _ctx_with(ctx, heal_depth=ctx.heal_depth + 1)
                _fire_triggers_for_unit(
                    TriggerWhen.ON_HEAL_RECEIVED, attacker,
                    next_ctx.allies_of(attacker), next_ctx.enemies_of(attacker),
                    next_ctx.rng, log,
                    events=events, round_number=next_ctx.round_number,
                    ctx=next_ctx,
                )
            # L3 trickle on lifesteal heal-back too (any heal counts).
            if ctx is not None:
                team = ctx.allies_of(attacker)
                if _team_has_mutation(team, _MUT_L3):
                    for ally in team:
                        if ally is attacker or not ally.alive:
                            continue
                        ally.hp += _MUT_L3_TRICKLE
                        trickle_line = (
                            f"L3 trickle: {ally.card.card_id} heals "
                            f"{_MUT_L3_TRICKLE}"
                        )
                        log.append(trickle_line)
                        if events is not None:
                            events.append(CombatEvent(
                                kind="heal",
                                actor_side=ally.side,
                                actor_position=ally.position,
                                actor_card_id=ally.card.card_id,
                                target_side=ally.side,
                                target_position=ally.position,
                                target_card_id=ally.card.card_id,
                                amount=_MUT_L3_TRICKLE,
                                hp_after=_hp_snapshot(ally),
                                reason="MUTATION_L3",
                                log_line=trickle_line,
                            ))
    # ----- Phase 4f-engine ops (charter §21.3) -----
    elif op == EffectOp.APPLY_BURN_STACK:
        # Additive: stacks accumulate, never refresh. Tick happens at the
        # holder's ON_TURN_END (in _resolve_action — see _tick_burn_stacks).
        if value <= 0:
            return
        target.burn_stacks += value
        line = (
            f"{actor_id} adds {value} burn stack(s) to {target.card.card_id} "
            f"(now {target.burn_stacks})"
        )
        log.append(line)
        _emit("status", value, line)
    elif op == EffectOp.THORNS:
        # Self-targeting passive. value semantics: thorns_value added to self.
        # Additive — multiple THORNS triggers stack. The reflection itself happens
        # in _take_damage when this unit takes damage (charter §21.3).
        if value <= 0 or target is not attacker:
            # THORNS only makes sense as a self-buff. Reject misauthored cases
            # silently (engine determinism — no exception mid-match) but DO log
            # the no-op so authoring bugs surface in test traces.
            log.append(f"{actor_id} THORNS no-op (target must be self, value > 0)")
            return
        target.thorns_value += value
        line = f"{actor_id} grows THORNS {value} (now {target.thorns_value})"
        log.append(line)
        _emit("buff", value, line)
    elif op == EffectOp.GRANT_EXTRA_ACTION:
        # Grants target one extra action this round if cap permits. Default cap
        # is 1; raised to 2 by L4 mutation. Acts immediately (depth-first) after
        # current trigger resolution unwinds — implemented by recursive
        # _resolve_action call below. The recursive depth-first model matches
        # charter §21.3 ("acts immediately after current resolution unwinds").
        if ctx is None or attacker is None or not target.alive:
            return
        team = ctx.allies_of(target)
        cap = _extra_action_cap(team)
        if target.extra_actions_used_this_round >= cap:
            line = (
                f"{actor_id} cannot grant extra action to {target.card.card_id} "
                f"(cap {cap} reached)"
            )
            log.append(line)
            return
        target.extra_actions_used_this_round += 1
        line = (
            f"{actor_id} grants extra action to {target.card.card_id} "
            f"(used {target.extra_actions_used_this_round}/{cap})"
        )
        log.append(line)
        _emit("buff", 1, line)
        # Fire ON_EXTRA_ACTION_GRANTED on target AFTER the counter is incremented.
        _fire_triggers_for_unit(
            TriggerWhen.ON_EXTRA_ACTION_GRANTED, target,
            ctx.allies_of(target), ctx.enemies_of(target),
            ctx.rng, log,
            events=events, round_number=ctx.round_number,
            ctx=ctx,
        )
        # Resolve the extra action immediately (depth-first). The recursive
        # call uses the same ctx so heal_depth / thorns_depth caps are inherited
        # correctly.
        _resolve_action(
            target, ctx.allies_of(target), ctx.enemies_of(target),
            ctx.rng, log,
            events=events, round_number=ctx.round_number,
            ctx=ctx,
        )
    elif op == EffectOp.SACRIFICE_SELF:
        # Self-destruct. Sets self.hp = 0; fires ON_DEATH for self AND
        # ON_ALLY_DEATH for every alive teammate (Q2 contract — charter §21.3).
        # Cannot be SILENCED (the trigger gating in _fire_triggers_for_unit
        # treats SACRIFICE_SELF as the unit's contribution; suppressing it would
        # deny player agency). Does NOT credit any opponent with ON_KILL.
        if not target.alive:
            return
        if ctx is None:
            # No ctx → can't fire cascade triggers. Still apply the self-kill so
            # state is consistent for any caller that doesn't want the cascade.
            target.hp = 0
            target.alive = False
            log.append(f"{actor_id} sacrifices itself")
            return
        target.hp = 0
        target.alive = False
        line = f"{actor_id} sacrifices itself"
        log.append(line)
        if events is not None:
            events.append(CombatEvent(
                kind="death",
                actor_side=target.side,
                actor_position=target.position,
                actor_card_id=target.card.card_id,
                target_side=target.side,
                target_position=target.position,
                target_card_id=target.card.card_id,
                amount=None,
                hp_after=_hp_snapshot(target),
                reason="SACRIFICE_SELF",
                log_line=line,
            ))
        # Fire ON_DEATH on the sacrificer itself.
        _fire_triggers_for_unit(
            TriggerWhen.ON_DEATH, target,
            ctx.allies_of(target), ctx.enemies_of(target),
            ctx.rng, log,
            events=events, round_number=ctx.round_number,
            ctx=ctx,
        )
        # Fire ON_ALLY_DEATH for each surviving ally on the sacrificer's team.
        _ally_death_triggers(
            target, ctx.allies_of(target), ctx.enemies_of(target),
            ctx.rng, log,
            events=events, round_number=ctx.round_number,
            ctx=ctx,
        )


def _apply_status(
    target: UnitState,
    status: StatusCondition,
    duration: int,
    log: List[str],
    *,
    actor_id: str,
    attacker: Optional[UnitState],
    events: Optional[List[CombatEvent]] = None,
    reason: Optional[str] = None,
) -> None:
    """Apply a duration-based status condition.

    Refresh semantics: existing duration is replaced with max(existing, new).
    This way overlapping applications extend (don't pile up to absurd lengths)
    but a longer subsequent application can refresh.

    Zero or negative durations are no-ops — protects against malformed cards.
    """
    if not target.alive or duration <= 0:
        return
    name = _STATUS_NAME.get(int(status), str(int(status)))
    existing = target.status.get(int(status), 0)
    target.status[int(status)] = max(existing, duration)
    line = f"{actor_id} applies {name} ({duration}r) to {target.card.card_id}"
    log.append(line)
    if events is None or attacker is None:
        return
    events.append(CombatEvent(
        kind="status",
        actor_side=attacker.side,
        actor_position=attacker.position,
        actor_card_id=attacker.card.card_id,
        target_side=target.side,
        target_position=target.position,
        target_card_id=target.card.card_id,
        amount=duration,
        hp_after=_hp_snapshot(target),
        reason=reason,
        status_applied=name,
        log_line=line,
    ))


def _take_damage(
    target: UnitState,
    amount: int,
    log: List[str],
    source: str,
    *,
    source_unit: Optional[UnitState] = None,
    events: Optional[List[CombatEvent]] = None,
    reason: Optional[str] = None,
    log_prefix: str = "",
    ctx: Optional[_FxCtx] = None,
    bypass_thorns: bool = False,
) -> None:
    """Apply damage to a target, logging the absorption + final HP.

    `source` is the human label used in the trace (e.g. card id, "burn").
    `source_unit` is the structured emitter, when available; absent for
    status-tick damage like burn (no attacker unit on record).

    Phase 4f-engine: when `ctx` is provided AND damage > 0 actually lands
    (post-shield), this fires:
      1. ON_DAMAGE_TAKEN on the target (charter §21.2 — distinct from
         ON_TAKE_DAMAGE which fires on the attempt, pre-shield).
      2. THORNS reflection if target.thorns_value > 0 and source_unit alive,
         with re-entry cap _THORNS_REFLECT_CAP per source-attack.
      3. L1 (`magma_tyrant`) burn-stack mutation: if any alive ally on the
         attacker's team carries L1, applies +1 burn_stack to the target after
         damage lands. Stacks even if target dies (the stack is on the corpse,
         doesn't tick — but is consistent with charter §22.2 L1).

    `bypass_thorns=True` is set by THORNS reflections themselves to disable
    the cap-protected re-entry — the cap on ctx.thorns_depth is the actual
    guard; bypass_thorns just lets the engine emit the reflection event.
    """
    if amount <= 0 or not target.alive:
        return
    absorbed = min(target.shield, amount)
    target.shield -= absorbed
    remaining = amount - absorbed
    # Phase 4f-engine: shield_count absorbs one full damage INSTANCE per point
    # (charter §21.1). Distinct from value-based `shield`. If any shield_count
    # remains AND the value-based shield didn't fully absorb, consume one stack
    # and zero the remaining damage. Order: value shield first, then count.
    # (Design choice: value shield is "common-tier shielding"; count is "rare-tier
    # multi-instance protection" — value shield gets first dibs because it's the
    # default mechanic.)
    if remaining > 0 and target.shield_count > 0:
        target.shield_count -= 1
        absorbed += remaining
        remaining = 0
        log.append(
            f"{target.card.card_id} shield_count absorbs full instance "
            f"(now {target.shield_count})"
        )
    target.hp -= remaining

    line = (
        f"{source} hits {target.card.card_id} for {amount} "
        f"(shield absorbed {absorbed}, hp now {max(0, target.hp)})"
    )
    log.append(line)

    died = target.hp <= 0
    if died:
        target.alive = False
        target.hp = 0

    # Structured event emission (additive — stays a no-op when events is None).
    # The string log is NOT appended to here for death — the existing string
    # protocol expresses death via "hp now 0" in the damage line above. Adding
    # an extra "destroyed" string would break the existing combat-test parity.
    damage_event: Optional[CombatEvent] = None
    if events is not None:
        # Combine multiplier prefix (if any) into the event's log_line so the
        # renderer can show both context and outcome on the same beat.
        combined_line = f"{log_prefix} | {line}" if log_prefix else line
        if source_unit is not None:
            actor_side = source_unit.side
            actor_position = source_unit.position
            actor_card_id = source_unit.card.card_id
        else:
            # Status-tick damage (e.g. burn) — actor is the target itself.
            actor_side = target.side
            actor_position = target.position
            actor_card_id = source
        damage_event = CombatEvent(
            kind="damage",
            actor_side=actor_side,
            actor_position=actor_position,
            actor_card_id=actor_card_id,
            target_side=target.side,
            target_position=target.position,
            target_card_id=target.card.card_id,
            amount=amount,
            hp_after=_hp_snapshot(target),
            reason=reason,
            log_line=combined_line,
        )
        events.append(damage_event)
        # Death is its own structured event nested under the killing damage —
        # gives the renderer a clean hook for death-fade primitives. The
        # log_line on the death event is renderer-facing only; not appended
        # to the string log.
        if died:
            damage_event.triggers.append(CombatEvent(
                kind="death",
                actor_side=target.side,
                actor_position=target.position,
                actor_card_id=target.card.card_id,
                target_side=target.side,
                target_position=target.position,
                target_card_id=target.card.card_id,
                amount=None,
                hp_after=_hp_snapshot(target),
                reason="ON_DEATH",
                log_line=f"{target.card.card_id} destroyed",
            ))

    # Phase 4f-engine: ON_DAMAGE_TAKEN, THORNS reflection, L1 mutation.
    # Only fires when ctx is available AND damage > 0 LANDED (i.e. remaining > 0).
    # Shielded-to-zero damage does NOT fire ON_DAMAGE_TAKEN per charter §21.2.
    if ctx is None or remaining <= 0:
        return

    # ON_DAMAGE_TAKEN on target (fires whether target survived or died).
    # Nests under the parent damage event so the renderer can show the chain.
    damage_event_triggers = damage_event.triggers if damage_event is not None else events
    _fire_triggers_for_unit(
        TriggerWhen.ON_DAMAGE_TAKEN, target,
        ctx.allies_of(target), ctx.enemies_of(target),
        ctx.rng, log,
        events=damage_event_triggers,
        round_number=ctx.round_number,
        ctx=ctx,
    )

    # THORNS reflection. Effective thorns = unit.thorns_value + L2 bonus if
    # `worldroot_sentinel` alive on target's team. Reflection is real damage,
    # element-neutral, BYPASSES DEF (charter §21.3). Source_unit must be alive
    # to take the reflection. Cap on cascade depth per source-attack.
    target_team = ctx.allies_of(target)
    eff_thorns = _effective_thorns_on_team(target, target_team)
    if (
        eff_thorns > 0
        and source_unit is not None
        and source_unit.alive
        and source_unit is not target  # no self-thorns from self-damage
        and ctx.thorns_depth < _THORNS_REFLECT_CAP
        # SILENCE on the THORNS-bearer suppresses thorns (charter §21.3).
        and target.status.get(int(StatusCondition.SILENCE), 0) == 0
    ):
        next_ctx = _ctx_with(ctx, thorns_depth=ctx.thorns_depth + 1)
        thorns_line = (
            f"{target.card.card_id} thorns reflect {eff_thorns} → "
            f"{source_unit.card.card_id}"
        )
        log.append(thorns_line)
        _take_damage(
            source_unit, eff_thorns, log,
            source=f"{target.card.card_id}#thorns",
            source_unit=target,
            events=damage_event_triggers,
            reason="THORNS",
            ctx=next_ctx,
            bypass_thorns=True,
        )

    # L1 (`magma_tyrant`) mutation: if any alive ally on attacker's team carries
    # L1, every damage instance dealt by an ally adds +1 burn_stack to target.
    # Skipped for status-tick damage (no source_unit) and for thorns reflections
    # (the attacker would otherwise stack burns onto themselves via thorns).
    if (
        source_unit is not None
        and not bypass_thorns
        and reason != "THORNS"
        and reason != "STATUS_TICK"
    ):
        attacker_team = ctx.allies_of(source_unit)
        if _team_has_mutation(attacker_team, _MUT_L1):
            target.burn_stacks += 1
            mut_line = (
                f"L1 mutation: {target.card.card_id} burn_stacks +1 "
                f"(now {target.burn_stacks})"
            )
            log.append(mut_line)
            if events is not None:
                events.append(CombatEvent(
                    kind="status",
                    actor_side=source_unit.side,
                    actor_position=source_unit.position,
                    actor_card_id=source_unit.card.card_id,
                    target_side=target.side,
                    target_position=target.position,
                    target_card_id=target.card.card_id,
                    amount=1,
                    hp_after=_hp_snapshot(target),
                    reason="MUTATION_L1",
                    log_line=mut_line,
                ))


# ---------------------------------------------------------------------------
# Condition evaluation context
# ---------------------------------------------------------------------------
#
# Triggers may carry a Trigger.condition DSL string. The DSL grammar lives in
# daimon/engine/conditions.py; this builder maps engine state into the dict
# the DSL evaluator expects. Snapshot semantics: the context is rebuilt for
# every trigger evaluation, so a trigger that fires after a teammate dies
# sees the post-death alive_count.
# ---------------------------------------------------------------------------

def _build_condition_ctx(
    unit: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    round_number: int,
) -> Dict[str, Any]:
    # Phase 4f-engine: L6 (`world_eater`) mutation. If `world_eater` is alive on
    # the unit's team AND the unit is a FLUX card, the team.distinct_elements
    # value the DSL sees is bumped by +2 (charter §22.2 L6). Scope is narrow on
    # purpose — only FLUX cards' condition gates see the bonus, so the mutation
    # supercharges FLUX deckbuilding specifically (per §22.2 L6 lock-text).
    distinct = len({int(u.card.element) for u in allies if u.alive})
    if (
        unit.card.archetype == "FLUX"
        and _team_has_mutation(allies, _MUT_L6)
    ):
        distinct += _MUT_L6_DISTINCT_BONUS
    return {
        "self": {
            "hp":      unit.hp,
            "hp_max":  unit.card.hp,
            "shield":  unit.shield,
            "atk":     unit.effective_atk,
            "def":     unit.effective_def,
            "spd":     unit.effective_spd,
            "element": int(unit.card.element),
            # Phase 4f-engine state primitives (charter §21.4 — exposed read-only):
            "burn_stacks":                    unit.burn_stacks,
            "shield_count":                   unit.shield_count,
            "extra_actions_used_this_round":  unit.extra_actions_used_this_round,
        },
        "team": {
            "distinct_elements": distinct,
            "alive_count":       sum(1 for u in allies if u.alive),
            "size":              len(allies),
        },
        "enemies": {
            "distinct_elements": len({int(u.card.element) for u in enemies if u.alive}),
            "alive_count":       sum(1 for u in enemies if u.alive),
            "size":              len(enemies),
        },
        "round": round_number,
    }


# ---------------------------------------------------------------------------
# Trigger orchestration
# ---------------------------------------------------------------------------

def _fire_triggers_for_unit(
    when: TriggerWhen,
    unit: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
    round_number: int = 0,
    ctx: Optional[_FxCtx] = None,
) -> None:
    """Fire all of `unit`'s triggers matching `when`, in declaration order.

    SILENCE gates ALL triggers on the unit (including ON_DEATH) — a silenced
    unit's death-rattle does not pop. Per design: "all triggers on this unit
    suppressed; ticks down at round-start". Phase 4f-engine exception: a
    SACRIFICE_SELF trigger bypasses SILENCE because the op IS the unit's
    contribution; suppressing it would deny player agency (charter §21.5).

    Each trigger may carry a `condition` DSL string; if present, the trigger
    only fires when the condition evaluates truthy against a freshly-snapshot
    context. Parse failures are guaranteed-impossible at fire-time because the
    catalog loader validates conditions at load-time.

    Phase 4f-engine: if `ctx` is provided AND the unit's team has L5
    (`voidking_morr`) alive, ON_ALLY_DEATH triggers fire TWICE in immediate
    sequence (charter §22.2 L5). Cascade depth is capped at _ALLY_DEATH_CAP
    via ctx.ally_death_depth (the ctx passed in must already reflect the
    current depth).
    """
    if not unit.alive and when != TriggerWhen.ON_DEATH:
        return
    silenced = unit.status.get(int(StatusCondition.SILENCE), 0) > 0
    reason = _TRIGGER_REASON.get(int(when))
    # L5 doubling: only ON_ALLY_DEATH gets doubled, only when L5 active on team.
    fire_count = 1
    if (
        when == TriggerWhen.ON_ALLY_DEATH
        and ctx is not None
        and _team_has_mutation(allies, _MUT_L5)
    ):
        fire_count = 2
    # SILENCE gating: all triggers suppressed EXCEPT SACRIFICE_SELF (charter
    # §21.5 exception). We check per-trigger because `unit.card.triggers` may
    # contain a mix of SACRIFICE_SELF and other ops; one SILENCE check on the
    # unit can't represent that.
    for trig in unit.card.triggers:
        if trig.when != when:
            continue
        if silenced and trig.op != EffectOp.SACRIFICE_SELF:
            continue
        if trig.condition is not None:
            cond_ctx = _build_condition_ctx(unit, allies, enemies, round_number)
            if not _cached_compile(trig.condition)(cond_ctx):
                continue
        for _ in range(fire_count):
            for tgt in _pick_targets(trig, unit, allies, enemies, rng):
                _apply_effect(
                    trig.op, trig.value, tgt, log, unit.card.card_id,
                    attacker=unit, events=events, reason=reason,
                    ctx=ctx,
                )
            # Re-check SILENCE between L5 fires — if the first fire's effect
            # silenced this unit, the second fire is suppressed (charter §22.2 L5).
            if fire_count == 2 and unit.status.get(int(StatusCondition.SILENCE), 0) > 0:
                if trig.op != EffectOp.SACRIFICE_SELF:
                    break


def _fire_triggers_all_units(
    when: TriggerWhen,
    side_a: List[UnitState],
    side_b: List[UnitState],
    rng: SeededRng,
    log: List[str],
    first_player: int = 0,
    *,
    events: Optional[List[CombatEvent]] = None,
    round_number: int = 0,
    ctx: Optional[_FxCtx] = None,
) -> None:
    """Deterministic order: `first_player` side by position, then other side by position.

    `first_player` is 0 (side A first) or 1 (side B first). Round-alternating
    per locked rule #30 so that trigger-resolution order varies across rounds
    and neither side gets a permanent tie-breaker advantage.
    """
    if first_player == 0:
        first, second = side_a, side_b
    else:
        first, second = side_b, side_a
    for u in sorted(first, key=lambda x: x.position):
        _fire_triggers_for_unit(
            when, u, first, second, rng, log,
            events=events, round_number=round_number, ctx=ctx,
        )
    for u in sorted(second, key=lambda x: x.position):
        _fire_triggers_for_unit(
            when, u, second, first, rng, log,
            events=events, round_number=round_number, ctx=ctx,
        )


def _ally_death_triggers(
    dying: UnitState,
    side: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
    round_number: int = 0,
    ctx: Optional[_FxCtx] = None,
) -> None:
    """Fire ON_ALLY_DEATH on every alive ally on `side` other than `dying`.

    Phase 4f-engine cascade cap: `ctx.ally_death_depth` is incremented per
    nested ON_ALLY_DEATH event (an ally death triggered by another ally's
    ON_ALLY_DEATH cascade). Cap at _ALLY_DEATH_CAP — beyond that, further
    cascades short-circuit. With L5 (`voidking_morr`) doubling on top of a
    6-card team, this cap is what keeps the round from deadlocking on a
    team-wipe cascade (charter §22.2 L5).
    """
    if ctx is not None and ctx.ally_death_depth >= _ALLY_DEATH_CAP:
        log.append(
            f"ON_ALLY_DEATH cascade cap reached ({_ALLY_DEATH_CAP}) — "
            f"suppressing further fires"
        )
        return
    next_ctx = ctx
    if ctx is not None:
        next_ctx = _ctx_with(ctx, ally_death_depth=ctx.ally_death_depth + 1)
    for u in sorted(side, key=lambda x: x.position):
        if u is dying or not u.alive:
            continue
        _fire_triggers_for_unit(
            TriggerWhen.ON_ALLY_DEATH, u, side, enemies, rng, log,
            events=events, round_number=round_number, ctx=next_ctx,
        )


def _sweep_low_hp_triggers(
    side_a: List[UnitState],
    side_b: List[UnitState],
    rng: SeededRng,
    log: List[str],
    round_number: int,
    *,
    events: Optional[List[CombatEvent]] = None,
    ctx: Optional[_FxCtx] = None,
) -> None:
    """Fire ON_LOW_HP for any alive unit that has crossed the 25% HP threshold
    and not yet fired its one-shot ON_LOW_HP trigger this match.

    Centralized as a sweep (not inline in _take_damage) because damage flows
    from many sources — basic attacks, trigger-sourced DAMAGE/LIFESTEAL, BURN
    + POISON ticks. A single sweep after every damage boundary is simpler and
    deterministic: side then position iteration order.

    Per-match one-shot is gated by UnitState.low_hp_fired; a unit at 0% HP is
    obviously dead and skipped. Threshold uses card.hp (max), not effective_hp,
    so buffs/debuffs to current HP don't shift the trigger boundary.
    """
    for unit in sorted(side_a + side_b, key=lambda x: (x.side, x.position)):
        if not unit.alive or unit.low_hp_fired:
            continue
        if unit.hp > unit.card.hp // _LOW_HP_DENOM:
            continue
        unit.low_hp_fired = True
        allies = side_a if unit.side == 0 else side_b
        enemies = side_b if unit.side == 0 else side_a
        _fire_triggers_for_unit(
            TriggerWhen.ON_LOW_HP, unit, allies, enemies, rng, log,
            events=events, round_number=round_number, ctx=ctx,
        )


def _tick_status_start_of_round(
    side_a: List[UnitState],
    side_b: List[UnitState],
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
    ctx: Optional[_FxCtx] = None,
) -> None:
    """Apply status condition ticks at round start.

    Tick semantics:
      - BURN:    deal 3 dmg, decrement.
      - POISON:  deal 2 dmg, decrement (smaller-but-distinct DOT vs BURN).
      - CHILL:   no damage; effect lives in effective_spd; decrement here.
      - ROOT:    no damage; effect lives in _resolve_action; decrement here.
      - SILENCE: no damage; trigger gating lives in _fire_triggers_for_unit;
                 decrement here per design ("ticks down at round-start").
      - TAUNT:   no damage; target-priority override lives in _resolve_action;
                 decrement here.
      - STUN:    NOT ticked here. Per design "1 = next action only" — STUN
                 ticks on action-skip in _resolve_action, not at round-start.
                 Otherwise a 1-round STUN applied mid-round would expire before
                 doing anything.
      - CHARGE:  consumed on attack in _resolve_action; never ticks here.
    """
    for u in sorted(side_a + side_b, key=lambda x: (x.side, x.position)):
        if not u.alive:
            continue
        burn = u.status.get(int(StatusCondition.BURN), 0)
        if burn > 0:
            _take_damage(
                u, _BURN_TICK_DMG, log, source="burn",
                source_unit=None, events=events, reason="STATUS_TICK",
                ctx=ctx,
            )
            u.status[int(StatusCondition.BURN)] = burn - 1
        poison = u.status.get(int(StatusCondition.POISON), 0)
        if poison > 0 and u.alive:
            _take_damage(
                u, _POISON_TICK_DMG, log, source="poison",
                source_unit=None, events=events, reason="STATUS_TICK",
                ctx=ctx,
            )
            u.status[int(StatusCondition.POISON)] = poison - 1
        chill = u.status.get(int(StatusCondition.CHILL), 0)
        if chill > 0:
            u.status[int(StatusCondition.CHILL)] = chill - 1
        root = u.status.get(int(StatusCondition.ROOT), 0)
        if root > 0:
            u.status[int(StatusCondition.ROOT)] = root - 1
        silence = u.status.get(int(StatusCondition.SILENCE), 0)
        if silence > 0:
            u.status[int(StatusCondition.SILENCE)] = silence - 1
        taunt = u.status.get(int(StatusCondition.TAUNT), 0)
        if taunt > 0:
            u.status[int(StatusCondition.TAUNT)] = taunt - 1


# ---------------------------------------------------------------------------
# Action resolution
# ---------------------------------------------------------------------------

def _resolve_action(
    actor: UnitState,
    allies: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
    round_number: int = 0,
    ctx: Optional[_FxCtx] = None,
) -> None:
    if not actor.alive:
        return
    # Rooted units skip their attack this round
    if actor.status.get(int(StatusCondition.ROOT), 0) > 0:
        line = f"{actor.card.card_id} is rooted, skips attack"
        log.append(line)
        if events is not None:
            events.append(CombatEvent(
                kind="status",
                actor_side=actor.side,
                actor_position=actor.position,
                actor_card_id=actor.card.card_id,
                target_side=actor.side,
                target_position=actor.position,
                target_card_id=actor.card.card_id,
                amount=None,
                hp_after=_hp_snapshot(actor),
                reason="ROOT",
                status_applied="ROOT",
                log_line=line,
            ))
        return
    # STUN: skip this action and consume one stack. Per design "1 = next action
    # only" — STUN ticks on consumption (here), NOT in _tick_status_start_of_round.
    stun = actor.status.get(int(StatusCondition.STUN), 0)
    if stun > 0:
        actor.status[int(StatusCondition.STUN)] = stun - 1
        line = f"{actor.card.card_id} is stunned, skips action"
        log.append(line)
        if events is not None:
            events.append(CombatEvent(
                kind="status",
                actor_side=actor.side,
                actor_position=actor.position,
                actor_card_id=actor.card.card_id,
                target_side=actor.side,
                target_position=actor.position,
                target_card_id=actor.card.card_id,
                amount=None,
                hp_after=_hp_snapshot(actor),
                reason="STUN",
                status_applied="STUN",
                log_line=line,
            ))
        return
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return

    # ON_OPENING_ATTACK: fires once per match on the unit's first attack of
    # the match. Fires BEFORE ON_ATTACK and BEFORE the actual attack (it's
    # proactive on the attacker, not reactive to damage). The has_attacked
    # flag is set after _take_damage below so an early-return (no enemies)
    # doesn't burn the one-shot.
    if not actor.has_attacked:
        _fire_triggers_for_unit(
            TriggerWhen.ON_OPENING_ATTACK, actor, allies, enemies, rng, log,
            events=events, round_number=round_number, ctx=ctx,
        )

    # ON_ATTACK triggers fire BEFORE the actual attack. We don't know the
    # target yet (pick happens after triggers), so ON_ATTACK events go
    # top-level — they're proactive on the attacker, not reactive
    # to the damage.
    _fire_triggers_for_unit(
        TriggerWhen.ON_ATTACK, actor, allies, enemies, rng, log,
        events=events, round_number=round_number, ctx=ctx,
    )

    # Re-resolve target after triggers (target may have died or been added to)
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return

    # TAUNT target override: if any enemy has TAUNT active, the basic attack
    # MUST hit a taunting enemy first. Within the taunting subset we still
    # use the lowest-HP / lowest-position rule for determinism. TAUNT only
    # affects the basic attack here; trigger-driven targeting (LOWEST_HP_ENEMY
    # etc.) keeps its declared filter — TAUNT models physical aggression draw,
    # not magical compulsion.
    taunting = [u for u in alive_enemies
                if u.status.get(int(StatusCondition.TAUNT), 0) > 0]
    target_pool = taunting if taunting else alive_enemies
    target = min(target_pool, key=lambda u: (u.hp, u.position))

    # Base damage, with Charge consumption and element multiplier
    charge_bonus = 0
    if actor.status.get(int(StatusCondition.CHARGE), 0) > 0:
        charge_bonus = 6
        actor.status[int(StatusCondition.CHARGE)] = 0  # consumed

    raw_atk = actor.effective_atk + charge_bonus
    base_dmg = max(0, raw_atk - target.effective_def)
    mult = element_multiplier(actor.card.element, target.card.element)
    final_dmg = max(0, math.ceil(base_dmg * mult)) if base_dmg > 0 else 0
    prefix_line = ""
    if mult != 1.0 and base_dmg > 0:
        prefix_line = (
            f"{actor.card.card_id} vs {target.card.card_id}: "
            f"{mult}× ({base_dmg}→{final_dmg})"
        )
        log.append(prefix_line)

    pre_alive = target.alive

    # Set up the parent event sink BEFORE _take_damage so the damage event
    # itself is the parent of subsequent ON_TAKE_DAMAGE / ON_DEATH triggers.
    parent_event: Optional[CombatEvent] = None
    parent_triggers: Optional[List[CombatEvent]] = None
    events_pre_len = len(events) if events is not None else 0

    _take_damage(
        target, final_dmg, log,
        source=actor.card.card_id, source_unit=actor,
        events=events, reason=None, log_prefix=prefix_line,
        ctx=ctx,
    )

    # Mark first-attack consumed AFTER damage application — the unit
    # "attempted an attack" at this point, even if final_dmg was 0.
    actor.has_attacked = True

    if events is not None:
        # If _take_damage emitted (i.e. damage > 0 and target was alive), the
        # last item appended is the parent damage event for cascading triggers.
        if len(events) > events_pre_len:
            parent_event = events[-1]
            parent_triggers = parent_event.triggers

    # ON_TAKE_DAMAGE on target — nest under parent damage event.
    if pre_alive and final_dmg > 0:
        _fire_triggers_for_unit(
            TriggerWhen.ON_TAKE_DAMAGE, target, enemies, allies, rng, log,
            events=parent_triggers if parent_triggers is not None else events,
            round_number=round_number, ctx=ctx,
        )

    # Death triggers — also nest under parent damage event.
    target_died = pre_alive and not target.alive
    if target_died:
        _fire_triggers_for_unit(
            TriggerWhen.ON_DEATH, target, enemies, allies, rng, log,
            events=parent_triggers if parent_triggers is not None else events,
            round_number=round_number, ctx=ctx,
        )
        _ally_death_triggers(
            target, enemies, allies, rng, log,
            events=parent_triggers if parent_triggers is not None else events,
            round_number=round_number, ctx=ctx,
        )
        # ON_KILL fires on the attacker right after target dies — also nests
        # under the parent damage event so the render shows the kill chain
        # cleanly. Skipped if the attacker was killed by a counter (ON_TAKE_DAMAGE
        # damage cascade) before reaching here.
        if actor.alive:
            _fire_triggers_for_unit(
                TriggerWhen.ON_KILL, actor, allies, enemies, rng, log,
                events=parent_triggers if parent_triggers is not None else events,
                round_number=round_number, ctx=ctx,
            )

    # ON_TURN_END fires on the actor at the end of its action regardless of
    # whether it killed anything. Only fires if the actor is still alive
    # (counter-killed attackers don't get a turn-end pop). Top-level event:
    # logically it's a "post-action" beat, not a sub-action of the attack.
    if actor.alive:
        _fire_triggers_for_unit(
            TriggerWhen.ON_TURN_END, actor, allies, enemies, rng, log,
            events=events, round_number=round_number, ctx=ctx,
        )

        # Phase 4f-engine: burn_stacks tick at ON_TURN_END (charter §21.1).
        # Holder takes `burn_stacks × 1` real damage (element-neutral, post-DEF),
        # then stacks zero. Distinct from APPLY_BURN (status-tick at round start);
        # this lives at ON_TURN_END so the holder's own action resolves before
        # the stacks bite (gameplay clarity — "I attacked, then my burns tick").
        # The damage IS routed through _take_damage so it can fire ON_DAMAGE_TAKEN
        # and respect THORNS reflection like any other damage source.
        if actor.burn_stacks > 0:
            burn_dmg = actor.burn_stacks
            stacks_consumed = actor.burn_stacks
            actor.burn_stacks = 0
            burn_line = (
                f"burn_stacks tick: {actor.card.card_id} loses {burn_dmg} hp "
                f"({stacks_consumed} stack(s) consumed)"
            )
            log.append(burn_line)
            _take_damage(
                actor, burn_dmg, log, source="burn_stacks",
                source_unit=None, events=events, reason="STATUS_TICK",
                ctx=ctx,
            )


def _hp_total(units: List[UnitState]) -> int:
    return sum(max(0, u.hp) for u in units if u.alive)


def resolve_match(
    loadout_a: Loadout,
    loadout_b: Loadout,
    seed: bytes,
) -> MatchResult:
    """Deterministically resolve a match.

    Same (loadout_a, loadout_b, seed) ALWAYS produces the same MatchResult.
    """
    if not isinstance(seed, bytes) or len(seed) != 32:
        raise ValueError("seed must be exactly 32 bytes")

    rng = SeededRng(seed)
    side_a = _build_units(loadout_a, side=0)
    side_b = _build_units(loadout_b, side=1)

    # Phase 4f-engine: single _FxCtx for the entire match. Sides + rng are
    # shared by reference (intentional — engine mutates them in place). The
    # `round_number` field is mutated at the top of each round; cap counters
    # (heal_depth, thorns_depth, ally_death_depth) start at 0 here and are
    # cloned-with-bump via `_ctx_with` whenever a cascading call descends a
    # level. Pre-Phase-4f helpers that don't need ctx still work — they accept
    # ctx as an Optional kwarg.
    ctx = _FxCtx(side_a=side_a, side_b=side_b, rng=rng, round_number=0)

    # Loadout-asymmetry fix (2026-04-22): derive the FIRST first-player from
    # the seed rather than always handing the opening tempo to side A.
    #
    # Previously the engine always opened with side A acting/triggering first
    # for ON_BATTLE_START + round 1, then alternated. That gave side A a
    # permanent first-strike advantage on every match — same loadout vs same
    # opponent vs same seed produced different stats depending on which side
    # the player landed on.
    #
    # Now `start_player = seed[0] & 1`. For the all-zero "replay-safe" seed
    # (used by tests + deterministic-by-default mode) this stays 0, so all
    # existing test assertions hold. Real matches use a random seed → ~50/50
    # opening tempo across the population; rounds still alternate from the
    # opener (start_player, 1 - start_player, start_player, ...) per locked
    # round-alternation rule #30.
    start_player = seed[0] & 1

    rounds: List[RoundLog] = []

    # Battle start triggers (one-shot) — accumulate into a separate buffer so
    # they can be merged into round 1's logs deterministically. Use the
    # seed-derived start_player so battle-start trigger order is also fair.
    # round_number=0 here so any trigger condition that gates on `round`
    # treats battle-start as pre-round (round 1 begins on the next sweep).
    start_log: List[str] = []
    start_events: List[CombatEvent] = []
    _fire_triggers_all_units(
        TriggerWhen.ON_BATTLE_START, side_a, side_b, rng, start_log,
        first_player=start_player, events=start_events, round_number=0,
        ctx=ctx,
    )
    _sweep_low_hp_triggers(
        side_a, side_b, rng, start_log, 0, events=start_events, ctx=ctx,
    )

    winner: Optional[int] = None
    reason = "round_cap"

    for r in range(1, ROUND_CAP + 1):
        # Round-alternating first-player (locked rule #30, 2026-04-21):
        # opener is `start_player` (seed-derived); subsequent rounds flip.
        # Round 1 -> start_player; round 2 -> 1 - start_player; round 3 ->
        # start_player; ... — preserves alternation while removing the
        # permanent side-A bias.
        first_player = (start_player + (r - 1)) % 2

        # Phase 4f-engine: ctx.round_number tracks the current round so DSL
        # conditions reading `round` see the right value mid-round.
        ctx.round_number = r

        # Phase 4f-engine: reset extra_actions_used_this_round at round start
        # (charter §21.1). The cap is per-unit-per-round, not per-match; absent
        # a reset, a unit that received 1 extra action in round 1 could never
        # receive another. Reset for ALL units (alive or dead — dead units
        # might revive later via off-spec mechanics; cheap to reset all).
        for u in side_a + side_b:
            u.extra_actions_used_this_round = 0

        round_log = RoundLog(round_number=r, first_player=first_player)
        if r == 1:
            round_log.actions.extend(start_log)
            round_log.events.extend(start_events)

        # Tick status conditions first (burn/poison dmg, chill/silence/taunt
        # countdown, etc.) — then sweep ON_LOW_HP because tick damage may have
        # crossed the threshold.
        _tick_status_start_of_round(
            side_a, side_b, round_log.actions, events=round_log.events, ctx=ctx,
        )
        _sweep_low_hp_triggers(
            side_a, side_b, rng, round_log.actions, r, events=round_log.events,
            ctx=ctx,
        )

        # Check for wipes caused by burn damage before doing more work
        a_alive = bool(_alive(side_a))
        b_alive = bool(_alive(side_b))
        if not a_alive or not b_alive:
            round_log.side_a_hp_total = _hp_total(side_a)
            round_log.side_b_hp_total = _hp_total(side_b)
            rounds.append(round_log)
            if not a_alive and not b_alive:
                winner = None
                reason = "double_wipe"
            elif not a_alive:
                winner = 1
                reason = "wipe"
            else:
                winner = 0
                reason = "wipe"
            break

        # Round start triggers (first_player side fires first)
        _fire_triggers_all_units(
            TriggerWhen.ON_ROUND_START, side_a, side_b, rng, round_log.actions,
            first_player=first_player, events=round_log.events, round_number=r,
            ctx=ctx,
        )
        _sweep_low_hp_triggers(
            side_a, side_b, rng, round_log.actions, r, events=round_log.events,
            ctx=ctx,
        )

        # Build action queue: all alive units, sorted by spd desc.
        # Tie-break: first_player side acts first, then other side, then position.
        actors = [u for u in side_a + side_b if u.alive]
        actors.sort(key=lambda u: (
            -u.effective_spd,
            0 if u.side == first_player else 1,
            u.position,
        ))

        for actor in actors:
            if not actor.alive:
                continue
            allies = side_a if actor.side == 0 else side_b
            enemies = side_b if actor.side == 0 else side_a
            _resolve_action(
                actor, allies, enemies, rng, round_log.actions,
                events=round_log.events, round_number=r, ctx=ctx,
            )
            # Sweep after every action — the action may have damaged units
            # on either side (counters, lifesteal, ON_TAKE_DAMAGE cascades).
            _sweep_low_hp_triggers(
                side_a, side_b, rng, round_log.actions, r, events=round_log.events,
                ctx=ctx,
            )

        round_log.side_a_hp_total = _hp_total(side_a)
        round_log.side_b_hp_total = _hp_total(side_b)
        rounds.append(round_log)

        a_alive = bool(_alive(side_a))
        b_alive = bool(_alive(side_b))
        if not a_alive and not b_alive:
            winner = None
            reason = "double_wipe"
            break
        if not a_alive:
            winner = 1
            reason = "wipe"
            break
        if not b_alive:
            winner = 0
            reason = "wipe"
            break

    final_a = _hp_total(side_a)
    final_b = _hp_total(side_b)

    if winner is None and reason == "round_cap":
        if final_a > final_b:
            winner = 0
        elif final_b > final_a:
            winner = 1
        else:
            winner = None
            reason = "draw"

    return MatchResult(
        seed=seed,
        rounds=rounds,
        winner=winner,
        side_a_final_hp=final_a,
        side_b_final_hp=final_b,
        reason=reason,
    )
