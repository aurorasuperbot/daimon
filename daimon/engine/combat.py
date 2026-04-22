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
from typing import List, Optional

from daimon.engine.elements import element_multiplier
from daimon.engine.loadout import Loadout
from daimon.engine.rng import SeededRng
from daimon.engine.types import (
    CombatEvent,
    EffectOp,
    MatchResult,
    RoundLog,
    StatusCondition,
    TargetFilter,
    Trigger,
    TriggerWhen,
    UnitState,
)

ROUND_CAP = 5


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
    int(EffectOp.BUFF_ATK):    "buff",
    int(EffectOp.DEBUFF_ATK):  "debuff",
    int(EffectOp.BUFF_DEF):    "buff",
    int(EffectOp.DEBUFF_DEF):  "debuff",
    int(EffectOp.HEAL):        "heal",
    int(EffectOp.DAMAGE):      "damage",
    int(EffectOp.ADD_SHIELD):  "shield",
    int(EffectOp.BUFF_SPD):    "buff",
}

# TriggerWhen -> reason string (matches the engine seam vocabulary).
_TRIGGER_REASON: dict[int, str] = {
    int(TriggerWhen.ON_BATTLE_START): "ON_BATTLE_START",
    int(TriggerWhen.ON_ROUND_START):  "ON_ROUND_START",
    int(TriggerWhen.ON_ATTACK):       "ON_ATTACK",
    int(TriggerWhen.ON_TAKE_DAMAGE):  "ON_TAKE_DAMAGE",
    int(TriggerWhen.ON_DEATH):        "ON_DEATH",
    int(TriggerWhen.ON_ALLY_DEATH):   "ON_ALLY_DEATH",
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
) -> None:
    if not target.alive and op != EffectOp.HEAL:
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
) -> None:
    """Apply damage to a target, logging the absorption + final HP.

    `source` is the human label used in the trace (e.g. card id, "burn").
    `source_unit` is the structured emitter, when available; absent for
    status-tick damage like burn (no attacker unit on record).
    """
    if amount <= 0 or not target.alive:
        return
    absorbed = min(target.shield, amount)
    target.shield -= absorbed
    remaining = amount - absorbed
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
) -> None:
    """Fire all of `unit`'s triggers matching `when`, in declaration order."""
    if not unit.alive and when != TriggerWhen.ON_DEATH:
        return
    reason = _TRIGGER_REASON.get(int(when))
    for trig in unit.card.triggers:
        if trig.when != when:
            continue
        for tgt in _pick_targets(trig, unit, allies, enemies, rng):
            _apply_effect(
                trig.op, trig.value, tgt, log, unit.card.card_id,
                attacker=unit, events=events, reason=reason,
            )


def _fire_triggers_all_units(
    when: TriggerWhen,
    side_a: List[UnitState],
    side_b: List[UnitState],
    rng: SeededRng,
    log: List[str],
    first_player: int = 0,
    *,
    events: Optional[List[CombatEvent]] = None,
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
        _fire_triggers_for_unit(when, u, first, second, rng, log, events=events)
    for u in sorted(second, key=lambda x: x.position):
        _fire_triggers_for_unit(when, u, second, first, rng, log, events=events)


def _ally_death_triggers(
    dying: UnitState,
    side: List[UnitState],
    enemies: List[UnitState],
    rng: SeededRng,
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
) -> None:
    for u in sorted(side, key=lambda x: x.position):
        if u is dying or not u.alive:
            continue
        _fire_triggers_for_unit(
            TriggerWhen.ON_ALLY_DEATH, u, side, enemies, rng, log, events=events,
        )


def _tick_status_start_of_round(
    side_a: List[UnitState],
    side_b: List[UnitState],
    log: List[str],
    *,
    events: Optional[List[CombatEvent]] = None,
) -> None:
    """Apply status condition ticks at round start. Plumbing-only in V2 —
    no catalog cards emit status yet, so this is a no-op in practice until
    Phase 6 content lands."""
    for u in sorted(side_a + side_b, key=lambda x: (x.side, x.position)):
        if not u.alive:
            continue
        burn = u.status.get(int(StatusCondition.BURN), 0)
        if burn > 0:
            _take_damage(
                u, 3, log, source="burn",
                source_unit=None, events=events, reason="STATUS_TICK",
            )
            u.status[int(StatusCondition.BURN)] = burn - 1
        chill = u.status.get(int(StatusCondition.CHILL), 0)
        if chill > 0:
            u.status[int(StatusCondition.CHILL)] = chill - 1
        root = u.status.get(int(StatusCondition.ROOT), 0)
        if root > 0:
            u.status[int(StatusCondition.ROOT)] = root - 1


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
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return

    # The parent attack event — built lazily after we have a target. Reactive
    # ON_ATTACK / ON_TAKE_DAMAGE / ON_DEATH triggers nest into its `triggers`
    # list so the renderer can visualize them as one cause-effect chain.
    parent_event: Optional[CombatEvent] = None
    parent_triggers: Optional[List[CombatEvent]] = None

    # ON_ATTACK triggers fire BEFORE the actual attack. We don't know the
    # target yet (pick happens after triggers), so ON_ATTACK events go
    # top-level for now — they're proactive on the attacker, not reactive
    # to the damage.
    _fire_triggers_for_unit(
        TriggerWhen.ON_ATTACK, actor, allies, enemies, rng, log, events=events,
    )

    # Re-resolve target after triggers (target may have died or been added to)
    alive_enemies = _alive(enemies)
    if not alive_enemies:
        return
    target = min(alive_enemies, key=lambda u: (u.hp, u.position))

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
    if events is not None:
        # We capture the index of the about-to-be-emitted damage event so
        # we can reach back into the events list and grab it as parent.
        events_pre_len = len(events)

    _take_damage(
        target, final_dmg, log,
        source=actor.card.card_id, source_unit=actor,
        events=events, reason=None, log_prefix=prefix_line,
    )

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
        )

    # Death triggers — also nest under parent damage event.
    if pre_alive and not target.alive:
        _fire_triggers_for_unit(
            TriggerWhen.ON_DEATH, target, enemies, allies, rng, log,
            events=parent_triggers if parent_triggers is not None else events,
        )
        _ally_death_triggers(
            target, enemies, allies, rng, log,
            events=parent_triggers if parent_triggers is not None else events,
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
    start_log: List[str] = []
    start_events: List[CombatEvent] = []
    _fire_triggers_all_units(
        TriggerWhen.ON_BATTLE_START, side_a, side_b, rng, start_log,
        first_player=start_player, events=start_events,
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

        round_log = RoundLog(round_number=r, first_player=first_player)
        if r == 1:
            round_log.actions.extend(start_log)
            round_log.events.extend(start_events)

        # Tick status conditions first (burn dmg, etc.)
        _tick_status_start_of_round(
            side_a, side_b, round_log.actions, events=round_log.events,
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
            first_player=first_player, events=round_log.events,
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
                events=round_log.events,
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
