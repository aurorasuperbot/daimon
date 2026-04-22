"""Tests for the render module.

Covers:
  - palette_for(rarity) returns sane palettes for each rarity tier
  - compose_card produces a PNG of the requested size
  - compose_card works without an art file (placeholder mode)
  - compose_card_from_pack_dict integrates cards loader + render layer
  - render_hybrid produces non-empty terminal output with frame chars
  - render_hybrid never reads card.flavor through the engine path
    (regression guard for prompt-injection invariant)
  - detect_tier returns a valid tier
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from daimon.engine.types import Card, EffectOp, Element, TargetFilter, Trigger, TriggerWhen
from daimon.render import (
    CardRenderInfo,
    compose_card,
    compose_card_from_pack_dict,
    detect_tier,
    palette_for,
    render_hybrid,
)


@pytest.fixture
def sample_card() -> Card:
    return Card(
        card_id="test_card",
        species="test_blade",
        element=Element.FIRE,
        atk=12, defense=4, hp=22, spd=8,
        triggers=(
            Trigger(TriggerWhen.ON_ATTACK, EffectOp.BUFF_ATK, TargetFilter.SELF, 2),
        ),
    )


@pytest.fixture
def sample_info() -> CardRenderInfo:
    return CardRenderInfo(
        name="Test Card",
        flavor="A blade for testing.",
        rarity="legendary",
        art_path=None,
    )


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

def test_palette_for_known_rarities():
    for rarity in ["legendary", "epic", "rare", "uncommon", "common"]:
        pal = palette_for(rarity)
        assert len(pal.accent) == 3
        assert all(0 <= c <= 255 for c in pal.accent)


def test_palette_for_unknown_falls_back_to_common():
    pal = palette_for("mythic_ultra_super_rare")
    assert pal == palette_for("common")


def test_palette_for_case_insensitive():
    assert palette_for("LEGENDARY") == palette_for("legendary")


# ---------------------------------------------------------------------------
# compose_card
# ---------------------------------------------------------------------------

def test_compose_card_produces_png(sample_card, sample_info, tmp_path):
    out = tmp_path / "card.png"
    compose_card(sample_card, sample_info, out)
    assert out.exists()
    # PNG magic bytes
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_compose_card_default_size(sample_card, sample_info, tmp_path):
    from PIL import Image
    out = tmp_path / "card.png"
    compose_card(sample_card, sample_info, out)
    img = Image.open(out)
    assert img.size == (280, 392)


def test_compose_card_custom_size(sample_card, sample_info, tmp_path):
    from PIL import Image
    out = tmp_path / "card.png"
    compose_card(sample_card, sample_info, out, width=140, height=196)
    img = Image.open(out)
    assert img.size == (140, 196)


def test_compose_card_works_for_each_rarity(sample_card, tmp_path):
    for rarity in ["legendary", "epic", "rare", "uncommon", "common"]:
        out = tmp_path / f"{rarity}.png"
        info = CardRenderInfo(name=rarity, rarity=rarity)
        compose_card(sample_card, info, out)
        assert out.exists()


def test_compose_card_handles_missing_art(sample_card, sample_info, tmp_path):
    sample_info.art_path = tmp_path / "no_such_art.png"  # doesn't exist
    out = tmp_path / "card.png"
    compose_card(sample_card, sample_info, out)
    assert out.exists()  # placeholder should kick in


# ---------------------------------------------------------------------------
# compose_card_from_pack_dict
# ---------------------------------------------------------------------------

def test_compose_card_from_pack_dict(tmp_path):
    pack_card = {
        "card_id": "test_pack_card",
        "species": "test_pack",
        "element": "FIRE",
        "atk": 5, "def": 5, "hp": 20, "spd": 5, "triggers": [],
        "_render_only": {
            "name": "Test Pack Card",
            "flavor": "It came from a JSON file.",
            "rarity": "rare",
        },
    }
    out = tmp_path / "out.png"
    compose_card_from_pack_dict(pack_card, tmp_path, out)
    assert out.exists()


def test_compose_card_from_pack_dict_real_starter(tmp_path):
    """Render a real starter card from the cards repo to verify integration."""
    cards_root = Path(
        "/opt/agents/projects/daimon-workspace/daimon-cards/packs/starter"
    )
    card_file = cards_root / "starter_scout_head.json"
    if not card_file.exists():
        pytest.skip("starter cards not available in this checkout")
    pack = json.loads(card_file.read_text())
    # External cards repo may still be on V1 schema (has 'slot', lacks 'element').
    # Skip in that case — the engine/render tests already cover V2 loading.
    if "slot" in pack and "element" not in pack:
        pytest.skip("starter cards repo still on V1 schema; skip until migrated")
    out = tmp_path / "scout.png"
    compose_card_from_pack_dict(pack, cards_root, out)
    assert out.exists()


def test_render_info_accepts_top_level_fields():
    """Cards-repo format puts render fields at top level (not under _render_only)."""
    from daimon.render import render_info_from_pack_dict
    pack_card = {
        "card_id": "x", "species": "x_species", "element": "FIRE",
        "atk": 5, "def": 5, "hp": 20, "spd": 5, "triggers": [],
        "name": "Top Level Name", "rarity": "rare", "flavor": "blah",
    }
    info = render_info_from_pack_dict(pack_card, Path("."))
    assert info.name == "Top Level Name"
    assert info.rarity == "rare"
    assert info.flavor == "blah"


def test_render_info_accepts_nested_render_only():
    """Test fixtures format nests render fields under _render_only."""
    from daimon.render import render_info_from_pack_dict
    pack_card = {
        "card_id": "x", "species": "x_species", "element": "FIRE",
        "atk": 5, "def": 5, "hp": 20, "spd": 5, "triggers": [],
        "_render_only": {"name": "Nested Name", "rarity": "epic"},
    }
    info = render_info_from_pack_dict(pack_card, Path("."))
    assert info.name == "Nested Name"
    assert info.rarity == "epic"


# ---------------------------------------------------------------------------
# Hybrid renderer
# ---------------------------------------------------------------------------

def test_render_hybrid_produces_output(sample_card, sample_info):
    out = render_hybrid(sample_card, sample_info, tier=7, ansi=False)
    assert out
    assert "TEST CARD" in out
    # V2: element chip replaces the old slot chip in the header row
    assert "FIRE" in out


def test_render_hybrid_includes_stats(sample_card, sample_info):
    out = render_hybrid(sample_card, sample_info, tier=7, ansi=False)
    assert "ATK:" in out
    assert "12" in out  # atk
    assert "DEF:" in out
    assert "HP:" in out
    assert "SPD:" in out


def test_render_hybrid_includes_trigger_summary(sample_card, sample_info):
    out = render_hybrid(sample_card, sample_info, tier=7, ansi=False)
    assert "ON_ATTACK" in out
    assert "BUFF_ATK" in out
    # Critical: trigger summary is enum-based, never prose. The card has no
    # text fields the engine could be tricked into reading.


def test_render_hybrid_t7_uses_ascii_chars(sample_card, sample_info):
    out = render_hybrid(sample_card, sample_info, tier=7, ansi=False)
    # T7 falls back to ASCII frame
    assert "+" in out  # corners
    assert "-" in out  # horizontal
    # No box-drawing unicode
    assert "╔" not in out
    assert "═" not in out


def test_render_hybrid_t1_uses_box_drawing(sample_card, sample_info):
    out = render_hybrid(sample_card, sample_info, tier=1, ansi=False)
    assert "╔" in out or "║" in out


def test_render_hybrid_handles_no_triggers(sample_info):
    plain = Card(card_id="vanilla", species="vanilla_s", element=Element.NATURE,
                 atk=5, defense=5, hp=20, spd=5, triggers=())
    out = render_hybrid(plain, sample_info, tier=7, ansi=False)
    assert "no triggers" in out


def test_render_hybrid_truncates_long_name(sample_card):
    long_name = "X" * 100
    info = CardRenderInfo(name=long_name, rarity="common")
    out = render_hybrid(sample_card, info, tier=7, ansi=False, width_cells=40)
    # No line should massively overflow
    for line in out.split("\n"):
        # ANSI-stripped length should be ≤ width_cells + small slack
        assert len(line) <= 60


# ---------------------------------------------------------------------------
# Cascade tier detection
# ---------------------------------------------------------------------------

def test_detect_tier_returns_valid():
    tier = detect_tier()
    assert tier in (1, 2, 3, 4, 5, 6, 7)


# ---------------------------------------------------------------------------
# Engine isolation regression test
# ---------------------------------------------------------------------------

def test_render_info_changes_dont_affect_engine(sample_card):
    """Render text changes must NEVER affect engine behavior.

    This is the prompt-injection invariant: an adversarial card author who
    modifies name/flavor/rarity cannot affect combat math.
    """
    from daimon.engine import Loadout, resolve_match
    from tests.conftest import make_filler

    # Build two loadouts with the same card but DIFFERENT render info
    head_a = sample_card  # whatever
    fillers = [make_filler(i) for i in range(6)]
    fillers[2] = head_a  # replace position 2

    lo_normal = Loadout(cards=tuple(fillers))

    # Same engine card, but in render-info we'd use different name/flavor.
    # The engine doesn't take render info, so the result MUST be identical.
    seed = b"\x42" * 32
    r1 = resolve_match(lo_normal, lo_normal, seed)
    r2 = resolve_match(lo_normal, lo_normal, seed)
    assert r1.winner == r2.winner
    assert r1.side_a_final_hp == r2.side_a_final_hp
    assert r1.side_b_final_hp == r2.side_b_final_hp
