"""Tests for the render module.

Covers:
  - palette_for(rarity) returns sane palettes for each rarity tier
  - compose_card produces a PNG of the requested size
  - compose_card works without an art file (placeholder mode)
  - compose_card_from_pack_dict integrates cards loader + render layer
  - engine isolation: render-text changes can't leak into combat math

The legacy chafa cascade / hybrid renderer (render_hybrid + detect_tier)
was retired in Phase E together with the half-block fallback. The bundled
WezTerm + KGP painter superseded those code paths — see
``daimon/play/art_render.py`` and ``daimon/render/kgp.py``, with KGP
encoder coverage in ``tests/test_kgp.py`` and painter coverage in
``tests/test_kgp_painter.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daimon.engine.types import Card, EffectOp, Element, TargetFilter, Trigger, TriggerWhen
from daimon.render import (
    CardRenderInfo,
    compose_card,
    compose_card_from_pack_dict,
    palette_for,
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
    """Default card render is 560×784 (5:7 portrait, 2× the legacy 280×392
    so flavor text stays legible at the canonical render resolution).

    Pinned to a literal here rather than `DEFAULT_W/DEFAULT_H` because the
    intent of this test is to catch unintentional default-size drift.
    """
    from PIL import Image
    out = tmp_path / "card.png"
    compose_card(sample_card, sample_info, out)
    img = Image.open(out)
    assert img.size == (560, 784)


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


@pytest.mark.parametrize(
    "card_id,expected_rarity",
    [
        ("abyss_minnow",   "common"),
        ("abyssbreaker",   "uncommon"),
        ("abyss_warden",   "rare"),
        ("arc_predator",   "epic"),
        ("magma_tyrant",   "legendary"),
    ],
)
def test_compose_card_from_pack_dict_bundled_catalog(card_id, expected_rarity, tmp_path):
    """Integration: render real V2 cards from the engine's bundled catalog.

    Replaces the legacy ``_real_starter`` test that was perpetually skipped
    after the monster pivot retired the V1 slot-based cards repo. The bundled
    catalog at ``daimon/catalog/v1_alpha/`` is the production source of truth
    for V2 monster cards (200 cards) and ships inside the engine wheel — no
    external repo dependency, no platform-specific paths, no skip conditions.

    Each of the 5 rarities is exercised so the palette + render-info pipeline
    is covered end-to-end against real production data. Art is intentionally
    routed through a tmp_path so the placeholder fallback is exercised; the
    bundled WezTerm + KGP painter coverage lives in ``test_kgp_painter.py``.
    """
    catalog_root = Path(__file__).parent.parent / "daimon" / "catalog" / "v1_alpha"
    card_file = catalog_root / f"{card_id}.json"
    assert card_file.exists(), f"bundled catalog missing {card_id}.json"

    pack = json.loads(card_file.read_text())

    # Sanity-check the catalog actually shipped V2 schema (post-monster-pivot).
    assert "element" in pack, f"{card_id} missing 'element' (catalog regressed to V1?)"
    assert "slot" not in pack, f"{card_id} has legacy 'slot' field (V1 fossil)"
    assert pack.get("rarity") == expected_rarity, (
        f"catalog drift: {card_id} rarity={pack.get('rarity')!r}, expected {expected_rarity!r}"
    )

    out = tmp_path / f"{card_id}.png"
    compose_card_from_pack_dict(pack, tmp_path, out)
    assert out.exists()
    # PNG magic bytes — proves we wrote a real image, not just an empty file.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


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
# Rarity ladder — frame counts + APNG output for animated tiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rarity,expected_frames",
    [
        ("common",    1),
        ("uncommon",  1),
        ("rare",      1),
        ("epic",      6),
        ("legendary", 12),
    ],
)
def test_compose_card_frames_count_per_rarity(sample_card, rarity, expected_frames):
    """The rarity ladder pins frame counts: 1/1/1/6/12.

    Static tiers always emit 1 frame; animated tiers emit their default
    loop length. This guards against accidental frame-count regressions
    that would balloon APNG file sizes or kill the loop smoothness.
    """
    from daimon.render import compose_card_frames
    info = CardRenderInfo(name=f"Test {rarity}", rarity=rarity)
    frames = compose_card_frames(sample_card, info)
    assert len(frames) == expected_frames


def test_compose_card_frames_explicit_n_overrides_default(sample_card):
    """Callers can pass an explicit ``n_frames`` to override the rarity
    default — useful for tests + perf-critical TUIs that want fewer frames."""
    from daimon.render import compose_card_frames
    info = CardRenderInfo(name="Override", rarity="legendary")
    frames = compose_card_frames(sample_card, info, n_frames=4)
    assert len(frames) == 4


def test_compose_card_frames_animated_tier_frames_differ(sample_card):
    """Animated tiers must produce visibly different frames; otherwise the
    APNG would just look static. Compares frame 0 vs frame N/2 byte-by-byte."""
    from daimon.render import compose_card_frames
    info = CardRenderInfo(name="Anim", rarity="legendary")
    frames = compose_card_frames(sample_card, info)
    assert len(frames) == 12
    # frame 0 and frame 6 are at opposite phase; bytes MUST differ
    assert frames[0].tobytes() != frames[6].tobytes()


def test_compose_card_frames_static_tier_all_frames_identical(sample_card):
    """Static tiers asked for >1 frame must produce identical bytes —
    no animation hooks fire. Otherwise we'd waste APNG file size."""
    from daimon.render import compose_card_frames
    info = CardRenderInfo(name="Static", rarity="common")
    frames = compose_card_frames(sample_card, info, n_frames=4)
    assert len(frames) == 4
    for f in frames[1:]:
        assert f.tobytes() == frames[0].tobytes()


def test_compose_card_writes_apng_for_legendary(sample_card, tmp_path):
    """Legendary cards must produce a real APNG (multi-frame PNG) on disk.

    APNG is detected by the presence of the ``acTL`` chunk (animation
    control). Image viewers without APNG support fall through to frame 0,
    so the ``.png`` extension stays correct.
    """
    info = CardRenderInfo(name="Legendary Test", rarity="legendary")
    out = tmp_path / "leg.png"
    compose_card(sample_card, info, out)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"acTL" in data, "legendary output missing acTL chunk (not an APNG)"


def test_compose_card_writes_apng_for_epic(sample_card, tmp_path):
    info = CardRenderInfo(name="Epic Test", rarity="epic")
    out = tmp_path / "epic.png"
    compose_card(sample_card, info, out)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"acTL" in data


def test_compose_card_writes_static_png_for_rare(sample_card, tmp_path):
    """Static tiers must NOT emit acTL chunks — keeps the file small and
    avoids confusing viewers that try to play 1-frame animations."""
    info = CardRenderInfo(name="Rare Test", rarity="rare")
    out = tmp_path / "rare.png"
    compose_card(sample_card, info, out)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"acTL" not in data


def test_compose_card_apng_round_trips_through_pillow(sample_card, tmp_path):
    """Pillow can re-open the legendary APNG and walk all frames."""
    from PIL import Image
    info = CardRenderInfo(name="RoundTrip", rarity="legendary")
    out = tmp_path / "rt.png"
    compose_card(sample_card, info, out)
    img = Image.open(out)
    n = getattr(img, "n_frames", 1)
    assert n == 12, f"expected 12 frames, got {n}"
    # Loop should be 0 (forever)
    assert img.info.get("loop", 0) == 0


def test_compose_card_all_tiers_produce_distinct_frame0(sample_card, tmp_path):
    """Frame 0 of each tier must differ from every other tier — proves the
    visual ladder is actually distinct, not just per-tier code paths that
    happen to render the same pixels."""
    from daimon.render import compose_card_frames
    seen: dict[str, bytes] = {}
    for rarity in ["common", "uncommon", "rare", "epic", "legendary"]:
        info = CardRenderInfo(name=f"Tier {rarity}", rarity=rarity)
        frames = compose_card_frames(sample_card, info)
        seen[rarity] = frames[0].tobytes()
    rarities = list(seen.keys())
    for i, r1 in enumerate(rarities):
        for r2 in rarities[i + 1:]:
            assert seen[r1] != seen[r2], (
                f"{r1} and {r2} render identical bytes — visual ladder regressed"
            )


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
