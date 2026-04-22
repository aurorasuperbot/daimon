"""Tests for daimon.play.animator + primitives.

Coverage matrix:
  - Default registry ships the 10 V1 primitives in paint order
  - `minimal()` returns the original 4 (for legacy snapshot tests)
  - Each primitive's applies_to / window / emit is correct
  - Animator.tick() collects emissions in registry order
  - Animator hoists hit_pause emissions into snapshot.pause_ms
  - Animator emits cues at t_ms==0 with HIT→KO promotion
  - Custom registry is honored
  - Pluggability — adding a custom primitive adds its emissions
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.play.animator import Animator, AnimationSnapshot
from daimon.play.primitives import (
    CardEmission,
    ColorFlashPrimitive,
    ConnectionEmission,
    ConnectionLinePrimitive,
    Cue,
    GlowPrimitive,
    HitPausePrimitive,
    HpTickPrimitive,
    IntentPrimitive,
    OverlayIconPrimitive,
    Primitive,
    PrimitiveRegistry,
    PulsePrimitive,
    ShakePrimitive,
    ZapPrimitive,
)
from daimon.play.schema import (
    Action,
    ActionKind,
    CardRef,
    Match,
    Side,
    VisOverrides,
)


FIXTURE_PATH = Path(__file__).parent.parent / "daimon" / "play" / "fixtures" / "match_sample.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_match() -> Match:
    return Match.model_validate(json.loads(FIXTURE_PATH.read_text()))


@pytest.fixture
def damage_action(sample_match) -> Action:
    # R1A1: Voltcat Apex (opp/pos 0) → Blade Foxling (player/pos 2), 7 damage
    return sample_match.rounds[0].actions[0]


@pytest.fixture
def buff_action(sample_match) -> Action:
    # R1A2: Iron Boar buff (player/pos 1), no target
    return sample_match.rounds[0].actions[1]


@pytest.fixture
def heal_action(sample_match) -> Action:
    # R1A3: Mindroot heals Voltcat Apex (same-side target)
    return sample_match.rounds[0].actions[2]


# ---------------------------------------------------------------------------
# Registry basics
# ---------------------------------------------------------------------------

EXPECTED_DEFAULT_NAMES = [
    "intent",
    "color_flash",
    "connection_line",
    "overlay_icon",
    "hp_tick",
    "zap",
    "shake",
    "pulse",
    "hit_pause",
    "glow",
]


class TestRegistry:
    def test_default_registry_has_ten_primitives(self):
        reg = PrimitiveRegistry.default()
        assert reg.names() == EXPECTED_DEFAULT_NAMES
        assert len(reg) == 10

    def test_minimal_returns_original_four(self):
        reg = PrimitiveRegistry.minimal()
        assert reg.names() == [
            "color_flash", "connection_line", "overlay_icon", "hp_tick",
        ]
        assert len(reg) == 4

    def test_add_and_remove(self):
        reg = PrimitiveRegistry.default()
        baseline = len(reg)

        class _Sentinel(Primitive):
            name = "sentinel"

            def applies_to(self, action):
                return False

            def window(self, action):
                return (0, 1)

            def emit(self, action, t_ms):
                return [], None

        reg.add(_Sentinel())
        assert "sentinel" in reg.names()
        assert len(reg) == baseline + 1
        reg.remove("sentinel")
        assert "sentinel" not in reg.names()
        assert len(reg) == baseline


# ---------------------------------------------------------------------------
# ColorFlashPrimitive
# ---------------------------------------------------------------------------

class TestColorFlash:
    def test_actor_flashes_at_beat_start(self, damage_action):
        prim = ColorFlashPrimitive()
        cards, conn = prim.emit(damage_action, t_ms=50)
        assert conn is None
        # Only actor should be flashing (target window starts at 100)
        assert len(cards) == 1
        assert cards[0].side == Side.OPPONENT
        assert cards[0].position == 0
        assert cards[0].kind == "color_flash"
        assert cards[0].color == "red"

    def test_both_flash_at_peak(self, damage_action):
        prim = ColorFlashPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=200)
        assert len(cards) == 2
        sides_positions = {(c.side, c.position) for c in cards}
        assert (Side.OPPONENT, 0) in sides_positions
        assert (Side.PLAYER, 2) in sides_positions

    def test_after_window_no_flash(self, damage_action):
        prim = ColorFlashPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=500)
        assert cards == []

    def test_untargeted_action_only_flashes_actor(self, buff_action):
        prim = ColorFlashPrimitive()
        cards, _ = prim.emit(buff_action, t_ms=200)
        # Only actor — target is None so no target flash even in its window
        assert len(cards) == 1
        assert cards[0].side == Side.PLAYER
        assert cards[0].kind == "color_flash"
        assert cards[0].color == "blue"  # buff

    def test_color_resolution_uses_vis_override(self, damage_action):
        # Clone + inject a vis override
        damage_action_override = damage_action.model_copy(
            update={"vis_overrides": VisOverrides(color="purple")}
        )
        prim = ColorFlashPrimitive()
        cards, _ = prim.emit(damage_action_override, t_ms=50)
        assert cards[0].color == "purple"


# ---------------------------------------------------------------------------
# ConnectionLinePrimitive
# ---------------------------------------------------------------------------

class TestConnectionLine:
    def test_draws_line_during_window(self, damage_action):
        prim = ConnectionLinePrimitive()
        cards, conn = prim.emit(damage_action, t_ms=100)
        assert cards == []
        assert conn is not None
        assert conn.actor_side == Side.OPPONENT
        assert conn.actor_position == 0
        assert conn.target_side == Side.PLAYER
        assert conn.target_position == 2
        assert conn.color == "red"

    def test_suppressed_by_vis_override(self, damage_action):
        suppressed = damage_action.model_copy(
            update={"vis_overrides": VisOverrides(suppress_line=True)}
        )
        prim = ConnectionLinePrimitive()
        assert not prim.applies_to(suppressed)

    def test_no_line_for_untargeted_action(self, buff_action):
        prim = ConnectionLinePrimitive()
        assert not prim.applies_to(buff_action)

    def test_no_line_outside_window(self, damage_action):
        prim = ConnectionLinePrimitive()
        _, conn = prim.emit(damage_action, t_ms=500)
        assert conn is None


# ---------------------------------------------------------------------------
# OverlayIconPrimitive
# ---------------------------------------------------------------------------

class TestOverlayIcon:
    def test_attaches_to_actor_and_target(self, damage_action):
        prim = OverlayIconPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=100)
        assert len(cards) == 2
        for c in cards:
            assert c.kind == "overlay_icon"
            assert c.icon == "💥"

    def test_untargeted_only_actor(self, buff_action):
        prim = OverlayIconPrimitive()
        cards, _ = prim.emit(buff_action, t_ms=100)
        assert len(cards) == 1
        assert cards[0].icon == "⚡"  # buff

    def test_heal_uses_sparkle_icon(self, heal_action):
        prim = OverlayIconPrimitive()
        cards, _ = prim.emit(heal_action, t_ms=100)
        for c in cards:
            assert c.icon == "✨"

    def test_vis_override_icon(self, damage_action):
        override = damage_action.model_copy(
            update={"vis_overrides": VisOverrides(icon="❄")}
        )
        prim = OverlayIconPrimitive()
        cards, _ = prim.emit(override, t_ms=100)
        for c in cards:
            assert c.icon == "❄"


# ---------------------------------------------------------------------------
# HpTickPrimitive
# ---------------------------------------------------------------------------

class TestHpTick:
    def test_applies_only_when_hp_after_nonempty(self, damage_action, buff_action):
        prim = HpTickPrimitive()
        assert prim.applies_to(damage_action)        # has hp_after
        assert not prim.applies_to(buff_action)      # no hp change

    def test_ticks_target_during_window(self, damage_action):
        prim = HpTickPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=300)
        assert len(cards) == 1
        assert cards[0].side == Side.PLAYER
        assert cards[0].position == 2
        assert cards[0].kind == "hp_tick"

    def test_no_tick_before_window(self, damage_action):
        prim = HpTickPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=100)
        assert cards == []


# ---------------------------------------------------------------------------
# Animator.tick() end-to-end
# ---------------------------------------------------------------------------

class TestAnimator:
    def test_default_animator_emits_expected_kinds_mid_beat(self, damage_action):
        # damage_action has amount=7 (below shake/hit_pause thresholds), no reason
        # (so no zap), kind=DAMAGE (not buff/shield/heal so no pulse). At t=250
        # only the original 4 primitives fire — intent capped at 200, glow trail
        # capped at 200.
        animator = Animator()
        snap = animator.tick(damage_action, t_ms=250)
        kinds = [e.kind for e in snap.card_emissions]
        assert kinds.count("color_flash") == 2
        assert kinds.count("overlay_icon") == 2
        assert kinds.count("hp_tick") == 1
        assert snap.connection is not None
        # No hit_pause emission at t=250; pause_ms must remain 0
        assert snap.pause_ms == 0
        # Cues only fire at t=0
        assert snap.cues == []

    def test_animator_no_emissions_after_beat(self, damage_action):
        animator = Animator()
        snap = animator.tick(damage_action, t_ms=580)
        assert snap.card_emissions == []
        assert snap.connection is None

    def test_animator_snapshot_for_card_filters(self, damage_action):
        animator = Animator()
        snap = animator.tick(damage_action, t_ms=250)
        target_emits = snap.for_card(Side.PLAYER, 2)
        # Target has: color_flash + overlay + hp_tick = 3
        assert len(target_emits) == 3
        kinds = {e.kind for e in target_emits}
        assert kinds == {"color_flash", "overlay_icon", "hp_tick"}

    def test_custom_registry_honored(self, damage_action):
        # Only color_flash
        custom = PrimitiveRegistry([ColorFlashPrimitive()])
        animator = Animator(registry=custom)
        snap = animator.tick(damage_action, t_ms=250)
        assert all(e.kind == "color_flash" for e in snap.card_emissions)
        assert snap.connection is None  # no connection primitive registered

    def test_pluggability_with_custom_primitive(self, damage_action):
        """Adding a new primitive should emit its effects without touching other code."""

        class RedRingPrimitive(Primitive):
            name = "red_ring"

            def applies_to(self, action: Action) -> bool:
                return action.kind == ActionKind.DAMAGE and action.target is not None

            def window(self, action: Action) -> tuple[int, int]:
                return (150, 350)

            def emit(self, action, t_ms):
                if not self.is_active_at(action, t_ms):
                    return [], None
                return [CardEmission(
                    side=action.target.side,
                    position=action.target.position,
                    kind="red_ring",
                    color="red",
                    extra={"ring_radius": (t_ms - 150) / 200.0},
                )], None

        reg = PrimitiveRegistry.default()
        reg.add(RedRingPrimitive())
        animator = Animator(registry=reg)
        snap = animator.tick(damage_action, t_ms=250)
        ring_emits = [e for e in snap.card_emissions if e.kind == "red_ring"]
        assert len(ring_emits) == 1
        assert ring_emits[0].extra["ring_radius"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Promoted V1 primitives — shake, pulse, glow (now default-on)
# ---------------------------------------------------------------------------

class TestShake:
    def test_shake_fires_only_on_big_damage(self, damage_action):
        # damage_action has amount=7, below threshold 8
        prim = ShakePrimitive()
        assert not prim.applies_to(damage_action)

        big_hit = damage_action.model_copy(update={"amount": 11})
        assert prim.applies_to(big_hit)

    def test_shake_emits_offset_cells(self):
        action = Action(
            action_id="test",
            actor=CardRef(side=Side.OPPONENT, position=0, card="Foo"),
            target=CardRef(side=Side.PLAYER, position=2, card="Bar"),
            kind=ActionKind.DAMAGE,
            amount=10,
        )
        prim = ShakePrimitive()
        cards, _ = prim.emit(action, t_ms=200)
        assert len(cards) == 1
        assert cards[0].kind == "shake"
        assert "offset_cells" in cards[0].extra
        assert cards[0].extra["offset_cells"] in (-1, 0, 1)

    def test_shake_inactive_outside_window(self):
        action = Action(
            action_id="test",
            actor=CardRef(side=Side.OPPONENT, position=0, card="Foo"),
            target=CardRef(side=Side.PLAYER, position=2, card="Bar"),
            kind=ActionKind.DAMAGE,
            amount=10,
        )
        prim = ShakePrimitive()
        cards, _ = prim.emit(action, t_ms=50)   # before window starts (100)
        assert cards == []
        cards, _ = prim.emit(action, t_ms=400)  # after window ends (350)
        assert cards == []


class TestPulse:
    def test_pulse_fires_on_buff(self, buff_action):
        prim = PulsePrimitive()
        assert prim.applies_to(buff_action)
        cards, _ = prim.emit(buff_action, t_ms=100)
        assert len(cards) == 1
        assert cards[0].kind == "pulse"
        assert 0.0 <= cards[0].extra["radius"] <= 1.0

    def test_pulse_fires_on_heal(self, heal_action):
        prim = PulsePrimitive()
        assert prim.applies_to(heal_action)

    def test_pulse_does_not_fire_on_damage(self, damage_action):
        prim = PulsePrimitive()
        assert not prim.applies_to(damage_action)


class TestGlow:
    def test_glow_emits_action_trail_early_in_beat(self, damage_action):
        prim = GlowPrimitive()
        assert prim.applies_to(damage_action)
        cards, _ = prim.emit(damage_action, t_ms=50)
        assert len(cards) == 1
        assert cards[0].kind == "glow"
        assert cards[0].side == Side.OPPONENT  # actor side
        assert cards[0].position == 0          # actor pos

    def test_glow_action_trail_caps_at_200ms(self, damage_action):
        prim = GlowPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=250)
        assert cards == []

    def test_glow_emit_persistent_helper(self):
        emit = GlowPrimitive.emit_persistent(Side.PLAYER, 3)
        assert emit.kind == "glow"
        assert emit.side == Side.PLAYER
        assert emit.position == 3
        assert emit.extra.get("persistent") is True


# ---------------------------------------------------------------------------
# New V1 primitives — intent, zap, hit_pause
# ---------------------------------------------------------------------------

class TestIntent:
    def test_intent_fires_on_targeted_action(self, damage_action):
        prim = IntentPrimitive()
        assert prim.applies_to(damage_action)

    def test_intent_skips_untargeted(self, buff_action):
        prim = IntentPrimitive()
        assert not prim.applies_to(buff_action)

    def test_intent_emits_on_actor_in_first_window(self, damage_action):
        prim = IntentPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=50)
        assert len(cards) == 1
        assert cards[0].kind == "intent"
        assert cards[0].side == damage_action.actor.side
        assert cards[0].position == damage_action.actor.position

    def test_intent_intensity_fades(self, damage_action):
        prim = IntentPrimitive()
        early, _ = prim.emit(damage_action, t_ms=0)
        late, _ = prim.emit(damage_action, t_ms=180)
        assert early[0].intensity > late[0].intensity

    def test_intent_silent_outside_window(self, damage_action):
        prim = IntentPrimitive()
        cards, _ = prim.emit(damage_action, t_ms=300)
        assert cards == []


class TestZap:
    def test_zap_fires_only_on_cascade(self, damage_action):
        # damage_action has reason=None — top-level action
        prim = ZapPrimitive()
        assert not prim.applies_to(damage_action)
        cascade = damage_action.model_copy(update={"reason": "trigger:ON_HIT"})
        assert prim.applies_to(cascade)

    def test_zap_emits_on_target_during_window(self, damage_action):
        cascade = damage_action.model_copy(update={"reason": "trigger:ON_HIT"})
        prim = ZapPrimitive()
        cards, _ = prim.emit(cascade, t_ms=150)
        assert len(cards) == 1
        assert cards[0].kind == "zap"
        assert cards[0].side == cascade.target.side
        assert cards[0].position == cascade.target.position

    def test_zap_silent_outside_window(self, damage_action):
        cascade = damage_action.model_copy(update={"reason": "trigger:ON_HIT"})
        prim = ZapPrimitive()
        cards, _ = prim.emit(cascade, t_ms=400)
        assert cards == []


class TestHitPause:
    def test_hit_pause_fires_on_big_damage(self, damage_action):
        prim = HitPausePrimitive()
        assert not prim.applies_to(damage_action)  # amount=7 below threshold
        big = damage_action.model_copy(update={"amount": 15})
        assert prim.applies_to(big)

    def test_hit_pause_skips_non_damage(self, buff_action):
        prim = HitPausePrimitive()
        assert not prim.applies_to(buff_action)

    def test_hit_pause_emits_pause_ms_at_t0(self, damage_action):
        big = damage_action.model_copy(update={"amount": 15})
        prim = HitPausePrimitive()
        cards, _ = prim.emit(big, t_ms=0)
        assert len(cards) == 1
        assert cards[0].kind == "hit_pause"
        assert cards[0].extra["pause_ms"] >= 60
        assert cards[0].extra["pause_ms"] <= 180

    def test_hit_pause_silent_after_t0(self, damage_action):
        big = damage_action.model_copy(update={"amount": 15})
        prim = HitPausePrimitive()
        cards, _ = prim.emit(big, t_ms=10)
        assert cards == []

    def test_hit_pause_scales_with_damage(self, damage_action):
        small = damage_action.model_copy(update={"amount": 10})
        big = damage_action.model_copy(update={"amount": 30})
        prim = HitPausePrimitive()
        small_cards, _ = prim.emit(small, t_ms=0)
        big_cards, _ = prim.emit(big, t_ms=0)
        assert big_cards[0].extra["pause_ms"] > small_cards[0].extra["pause_ms"]


# ---------------------------------------------------------------------------
# Animator hoisting + cues — the snapshot-level integration
# ---------------------------------------------------------------------------

class TestSnapshotPauseAndCues:
    def test_pause_ms_hoisted_into_snapshot(self, damage_action):
        big = damage_action.model_copy(update={"amount": 20})
        animator = Animator()
        snap = animator.tick(big, t_ms=0)
        assert snap.pause_ms > 0
        # Hit-pause emissions should NOT be in card_emissions (they're hoisted)
        assert all(e.kind != "hit_pause" for e in snap.card_emissions)

    def test_no_pause_when_action_below_threshold(self, damage_action):
        # damage_action has amount=7, below hit_pause threshold of 10
        animator = Animator()
        snap = animator.tick(damage_action, t_ms=0)
        assert snap.pause_ms == 0

    def test_cues_emitted_at_t0_only(self, damage_action):
        animator = Animator()
        at_zero = animator.tick(damage_action, t_ms=0)
        later = animator.tick(damage_action, t_ms=200)
        assert Cue.HIT in at_zero.cues
        assert later.cues == []

    def test_cue_promoted_to_ko_when_target_dies(self, damage_action):
        target_key = f"{damage_action.target.side.value}/{damage_action.target.position}"
        ko_action = damage_action.model_copy(update={
            "amount": 99,
            "hp_after": {target_key: 0},
        })
        animator = Animator()
        snap = animator.tick(ko_action, t_ms=0)
        assert Cue.KO in snap.cues
        assert Cue.HIT not in snap.cues

    def test_buff_cue_for_buff_action(self, buff_action):
        animator = Animator()
        snap = animator.tick(buff_action, t_ms=0)
        assert Cue.BUFF in snap.cues
