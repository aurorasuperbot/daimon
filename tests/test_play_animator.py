"""Tests for nullpoint.play.animator + primitives.

Coverage matrix:
  - Default registry has exactly the 4 V1 ship primitives
  - Each primitive's applies_to / window / emit is correct
  - Animator.tick() collects emissions in registry order
  - Custom registry is honored (opt-in V1.x primitives)
  - Pluggability — adding a custom primitive adds its emissions
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nullpoint.play.animator import Animator, AnimationSnapshot
from nullpoint.play.primitives import (
    CardEmission,
    ColorFlashPrimitive,
    ConnectionEmission,
    ConnectionLinePrimitive,
    GlowPrimitive,
    HpTickPrimitive,
    OverlayIconPrimitive,
    Primitive,
    PrimitiveRegistry,
    PulsePrimitive,
    ShakePrimitive,
)
from nullpoint.play.schema import (
    Action,
    ActionKind,
    CardRef,
    Match,
    Side,
    VisOverrides,
)


FIXTURE_PATH = Path(__file__).parent.parent / "nullpoint" / "play" / "fixtures" / "match_sample.json"


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

class TestRegistry:
    def test_default_registry_has_four_primitives(self):
        reg = PrimitiveRegistry.default()
        assert reg.names() == ["color_flash", "connection_line", "overlay_icon", "hp_tick"]
        assert len(reg) == 4

    def test_v1x_preview_adds_three_more(self):
        reg = PrimitiveRegistry.with_v1x_preview()
        assert reg.names() == [
            "color_flash", "connection_line", "overlay_icon", "hp_tick",
            "shake", "pulse", "glow",
        ]
        assert len(reg) == 7

    def test_add_and_remove(self):
        reg = PrimitiveRegistry.default()
        reg.add(ShakePrimitive())
        assert "shake" in reg.names()
        reg.remove("shake")
        assert "shake" not in reg.names()
        assert len(reg) == 4


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
    def test_default_animator_emits_from_four_primitives(self, damage_action):
        animator = Animator()
        snap = animator.tick(damage_action, t_ms=250)
        # At t=250 inside damage window: actor_flash, target_flash, connection, overlay_actor, overlay_target, hp_tick
        kinds = [e.kind for e in snap.card_emissions]
        assert kinds.count("color_flash") == 2
        assert kinds.count("overlay_icon") == 2
        assert kinds.count("hp_tick") == 1
        assert snap.connection is not None

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
# V1.x stubs — shape only, since they're not wired into default
# ---------------------------------------------------------------------------

class TestV1xStubs:
    def test_shake_fires_only_on_big_damage(self, damage_action):
        # damage_action has amount=7, below threshold 8
        prim = ShakePrimitive()
        assert not prim.applies_to(damage_action)

        big_hit = damage_action.model_copy(update={"amount": 11})
        assert prim.applies_to(big_hit)

    def test_shake_emits_offset(self):
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
        assert "offset_px" in cards[0].extra

    def test_pulse_fires_on_buff(self, buff_action):
        prim = PulsePrimitive()
        assert prim.applies_to(buff_action)
        cards, _ = prim.emit(buff_action, t_ms=100)
        assert len(cards) == 1
        assert cards[0].kind == "pulse"
        assert 0.0 <= cards[0].extra["radius"] <= 1.0

    def test_glow_currently_inactive(self, damage_action):
        # Stub — always False for now
        prim = GlowPrimitive()
        assert not prim.applies_to(damage_action)
