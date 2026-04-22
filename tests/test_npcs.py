"""Tests for the DAIMON NPC tier roster.

Coverage:
  - manifest + per-NPC files load cleanly
  - every NPC's loadout resolves through the catalog into an engine.Loadout
    (catches bad card_ids, wrong count, dupes, species-cap violations)
  - tier difficulty curve: average loadout "power" climbs across tiers
  - determinism: same NPC + same seed -> identical match result
  - sanity: roster cache, lookup-by-id, tier filter
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

import pytest

from daimon.cards import load_card_dict
from daimon.catalog import load_catalog
from daimon.engine import Loadout, resolve_match
from daimon.npcs import (
    NPC,
    Roster,
    clear_roster_cache,
    get_npc,
    get_roster,
    list_npcs,
    list_tiers,
    load_roster,
    npc_card_dicts,
    npc_loadout,
)
from daimon.npcs.loader import _resolve_loadout_cards


# ---------------------------------------------------------------------------
# Manifest + file integrity
# ---------------------------------------------------------------------------

EXPECTED_TIERS = ["rookie", "novice", "veteran", "elite", "champion"]


def test_roster_loads_with_expected_tiers():
    roster = get_roster(force_reload=True)
    assert isinstance(roster, Roster)
    assert roster.tier_ids() == EXPECTED_TIERS


def test_roster_has_25_npcs():
    """V1 alpha ships exactly 5 NPCs per tier x 5 tiers = 25."""
    roster = get_roster(force_reload=True)
    assert len(roster.npcs) == 25
    for tier_id in EXPECTED_TIERS:
        assert len(roster.by_tier[tier_id]) == 5, (
            f"tier {tier_id!r} expected 5 NPCs, got {len(roster.by_tier[tier_id])}"
        )


def test_every_npc_has_unique_id():
    roster = get_roster(force_reload=True)
    ids = [n.npc_id for n in roster.npcs]
    assert len(ids) == len(set(ids)), "duplicate npc_id in roster"


def test_every_npc_has_required_fields():
    """Catches stray empty fields in the JSON without manifest changes."""
    for n in list_npcs():
        assert n.npc_id, f"npc {n} has empty npc_id"
        assert n.name, f"npc {n.npc_id} has empty name"
        assert n.tier in EXPECTED_TIERS, f"npc {n.npc_id} has bad tier {n.tier!r}"
        assert n.rank in (1, 2, 3, 4, 5), f"npc {n.npc_id} has bad rank {n.rank}"
        assert n.flavor, f"npc {n.npc_id} has empty flavor"
        assert n.bio, f"npc {n.npc_id} has empty bio"
        assert len(n.loadout) == 6, (
            f"npc {n.npc_id} loadout has {len(n.loadout)} cards, expected 6"
        )


# ---------------------------------------------------------------------------
# Loadout resolution -- catches bad card_ids, dupes, species cap
# ---------------------------------------------------------------------------

def test_every_npc_loadout_resolves_to_engine_loadout():
    """The strictest test: every NPC's loadout passes engine validation.

    This catches:
      - card_id missing from v1_alpha catalog
      - duplicate card_id within a team (rejected by Loadout.__post_init__)
      - species cap (>2 of same species, rejected by Loadout.__post_init__)
      - any schema-loader rejection of the resolved card
    """
    failures = []
    for n in list_npcs():
        try:
            lo = npc_loadout(n)
            assert isinstance(lo, Loadout)
            assert len(lo.cards) == 6
        except Exception as e:  # noqa: BLE001 - we want to surface ALL failures
            failures.append(f"{n.npc_id}: {type(e).__name__}: {e}")
    assert not failures, "NPC loadouts failed to resolve:\n  " + "\n  ".join(failures)


def test_npc_card_dicts_returns_full_payloads():
    """npc_card_dicts gives raw catalog payloads suitable for MCP / render."""
    sam = get_npc("sparring_sam")
    cards = npc_card_dicts(sam)
    assert len(cards) == 6
    # Should carry display fields (name, rarity, art) -- those are catalog-bundled
    for c in cards:
        assert "card_id" in c
        assert "species" in c
        assert "name" in c, f"NPC card payload missing display name: {c.get('card_id')}"
        assert "rarity" in c


# ---------------------------------------------------------------------------
# Tier curve: difficulty should climb
# ---------------------------------------------------------------------------

def _team_power(npc: NPC) -> float:
    """Crude power proxy: sum(stat) + 4 per trigger.

    Triggers are weighted heavily because the game's combat depth comes
    from triggers, not stat blocks. (Voltcat Apex's two triggers give it
    the same effective power as Anvilram's stat wall.)
    """
    cat = load_catalog()
    raw = _resolve_loadout_cards(npc, cat)
    total = 0.0
    for c in raw:
        total += c.get("atk", 0) + c.get("def", 0) + c.get("hp", 0) + c.get("spd", 0)
        total += 4 * len(c.get("triggers", []))
    return total


def test_tier_curve_is_monotonically_climbing():
    """Average power within each tier should never DROP as you climb."""
    by_tier = {}
    for tier_id in EXPECTED_TIERS:
        npcs = list_npcs(tier_id)
        avg = mean(_team_power(n) for n in npcs)
        by_tier[tier_id] = avg

    # Print for diagnostic visibility (only on test failure does pytest show it)
    print("\nTier power averages:")
    for t in EXPECTED_TIERS:
        print(f"  {t:10}  {by_tier[t]:7.1f}")

    for prev, curr in zip(EXPECTED_TIERS, EXPECTED_TIERS[1:]):
        assert by_tier[curr] >= by_tier[prev], (
            f"tier {curr!r} ({by_tier[curr]:.1f}) is weaker than "
            f"{prev!r} ({by_tier[prev]:.1f}) -- difficulty curve broken"
        )


def test_champion_tier_significantly_stronger_than_rookie():
    """Sanity: top tier should be at least 1.15x rookie average power.

    Without this, the tier separation is purely cosmetic -- the actual
    fights would feel identical. 1.15x is conservative; in practice
    the catalog enables ~1.3x via legendary + epic triggers.
    """
    rookie_avg = mean(_team_power(n) for n in list_npcs("rookie"))
    champion_avg = mean(_team_power(n) for n in list_npcs("champion"))
    ratio = champion_avg / rookie_avg
    assert ratio >= 1.15, (
        f"champion/rookie ratio is only {ratio:.2f}x -- "
        f"tiers don't feel different enough"
    )


def test_legendary_appears_only_in_champion_tier():
    """voltcat_apex (the only legendary in v1_alpha) must be Champion-only."""
    for n in list_npcs():
        if "voltcat_apex" in n.loadout:
            assert n.tier == "champion", (
                f"NPC {n.npc_id!r} (tier {n.tier!r}) has the legendary "
                f"voltcat_apex -- legendary should be Champion-tier only"
            )


def test_rookie_tier_has_no_rares_or_higher():
    """Rookie should be common-only or common+1 uncommon."""
    cat = load_catalog()
    rare_or_higher = {
        c.card_id for c in cat.cards
        if c.rarity in ("rare", "epic", "legendary")
    }
    for n in list_npcs("rookie"):
        bad = set(n.loadout) & rare_or_higher
        assert not bad, (
            f"Rookie NPC {n.npc_id!r} contains {bad} -- "
            f"rookie tier should be commons + uncommons only"
        )


# ---------------------------------------------------------------------------
# Determinism + match plumbing
# ---------------------------------------------------------------------------

SEED = b"\x00" * 32


def _filler_loadout() -> Loadout:
    """6 vanilla 5/5/20/5 monsters for use as a player team in determinism tests."""
    cards = []
    for i in range(6):
        cards.append(load_card_dict({
            "card_id": f"filler_{i}",
            "species": f"filler_{i}",
            "element": ["FIRE", "WATER", "NATURE", "VOLT", "VOID", "FIRE"][i],
            "atk": 5, "def": 5, "hp": 20, "spd": 5,
            "triggers": [],
        }))
    return Loadout(cards=tuple(cards))


def test_match_against_npc_is_deterministic():
    """Same NPC + same player loadout + same seed -> identical result."""
    npc = get_npc("doom_paw_doppia")
    player = _filler_loadout()
    npc_lo = npc_loadout(npc)

    r1 = resolve_match(player, npc_lo, SEED)
    r2 = resolve_match(player, npc_lo, SEED)
    assert r1.winner == r2.winner
    assert r1.reason == r2.reason
    assert r1.side_a_final_hp == r2.side_a_final_hp
    assert r1.side_b_final_hp == r2.side_b_final_hp
    assert len(r1.rounds) == len(r2.rounds)


def test_filler_loses_to_doom_paw_doppia():
    """Smoke test on the difficulty curve: filler player team should LOSE
    to the final boss. If this ever passes the other way, either the player
    fixture got too strong or Doppia got nerfed."""
    npc = get_npc("doom_paw_doppia")
    player = _filler_loadout()
    npc_lo = npc_loadout(npc)
    r = resolve_match(player, npc_lo, SEED)
    # Either Doppia wins, or it's a draw -- never the filler.
    assert r.winner != 0, (
        f"vanilla filler beat Doom-paw Doppia (winner={r.winner}) -- "
        f"either Doppia is too weak or filler is too strong"
    )


# ---------------------------------------------------------------------------
# Public API conveniences
# ---------------------------------------------------------------------------

def test_list_tiers_returns_rank_order():
    assert list_tiers() == EXPECTED_TIERS


def test_list_npcs_filtered_by_tier():
    rookie = list_npcs("rookie")
    assert all(n.tier == "rookie" for n in rookie)
    assert len(rookie) == 5


def test_list_npcs_unknown_tier_raises():
    with pytest.raises(ValueError, match="unknown tier"):
        list_npcs("grandmaster")


def test_get_npc_unknown_id_raises():
    with pytest.raises(KeyError, match="unknown npc_id"):
        get_npc("santiago_the_eternal")


def test_get_roster_caches():
    """Two calls return the same Roster object (cache hit)."""
    clear_roster_cache()
    r1 = get_roster()
    r2 = get_roster()
    assert r1 is r2


def test_clear_roster_cache_forces_reload():
    r1 = get_roster()
    clear_roster_cache()
    r2 = get_roster()
    assert r1 is not r2  # fresh object after cache clear
    # Roster contents should still be identical
    assert r1.tier_ids() == r2.tier_ids()
    assert {n.npc_id for n in r1.npcs} == {n.npc_id for n in r2.npcs}


# ---------------------------------------------------------------------------
# Error path: malformed roster directory
# ---------------------------------------------------------------------------

def test_load_roster_from_alternate_root(tmp_path: Path):
    """Loader supports a custom root for tests / forks."""
    # Build a tiny one-tier, one-NPC roster on disk
    (tmp_path / "rookie").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "roster_version": "test_alpha",
        "description": "test roster",
        "tiers": [{
            "tier_id": "rookie", "rank": 1, "label": "Rookie",
            "rule": "test", "npcs": ["test_dummy"],
        }],
    }))
    (tmp_path / "rookie" / "test_dummy.json").write_text(json.dumps({
        "npc_id": "test_dummy", "name": "Test Dummy", "tier": "rookie",
        "rank": 1, "flavor": "test", "bio": "test",
        "loadout": ["scoutling", "iron_boar", "blade_foxling",
                    "shellpup", "dashmouse", "geodeling"],
    }))
    r = load_roster(root=tmp_path)
    assert r.roster_version == "test_alpha"
    assert len(r.npcs) == 1
    assert r.npcs[0].npc_id == "test_dummy"
    # And the loadout still resolves through the real catalog
    lo = npc_loadout(r.npcs[0])
    assert len(lo.cards) == 6


def test_load_roster_rejects_mismatched_npc_id(tmp_path: Path):
    (tmp_path / "rookie").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "roster_version": "test", "description": "",
        "tiers": [{"tier_id": "rookie", "rank": 1, "label": "Rookie",
                   "rule": "", "npcs": ["alice"]}],
    }))
    (tmp_path / "rookie" / "alice.json").write_text(json.dumps({
        "npc_id": "bob",  # WRONG -- doesn't match manifest reference
        "name": "Bob", "tier": "rookie", "rank": 1,
        "flavor": "", "bio": "",
        "loadout": ["scoutling", "iron_boar", "blade_foxling",
                    "shellpup", "dashmouse", "geodeling"],
    }))
    with pytest.raises(ValueError, match="does not match manifest"):
        load_roster(root=tmp_path)


def test_load_roster_rejects_wrong_loadout_size(tmp_path: Path):
    (tmp_path / "rookie").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "roster_version": "test", "description": "",
        "tiers": [{"tier_id": "rookie", "rank": 1, "label": "Rookie",
                   "rule": "", "npcs": ["short"]}],
    }))
    (tmp_path / "rookie" / "short.json").write_text(json.dumps({
        "npc_id": "short", "name": "Short", "tier": "rookie", "rank": 1,
        "flavor": "", "bio": "",
        "loadout": ["scoutling", "iron_boar", "blade_foxling"],  # only 3
    }))
    with pytest.raises(ValueError, match="loadout must be list of 6"):
        load_roster(root=tmp_path)
