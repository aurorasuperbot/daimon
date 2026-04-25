"""Phase 4g — showcase loadouts for V1 legendary mutations (L1-L6).

These tests verify three independent things for each of the 6 showcase
loadouts under ``daimon/loadouts/showcase/``:

  1. Manifest + JSON load cleanly via the loader API
     (`list_showcase_loadouts`, `get_showcase_loadout`, `resolve_showcase_loadout`).
  2. The legendary at position 0 carries the expected `rule_change` tag,
     and engine helpers (`_team_has_mutation`, `_extra_action_cap`,
     `_effective_thorns`, `_build_condition_ctx`) recognise the mutation
     when the legendary is alive on the team.
  3. Each loadout can resolve a real match end-to-end against a control
     opponent (mono-NORMAL chassis) without crashing, and the engine
     determines a winner or a draw.

The mutation-engine assertions are deliberately layered at the helper
level rather than asserting on log strings from a full match \u2014 this
keeps the tests robust to opponent-design changes and to the inherent
non-determinism of which exact log lines fire in a given round, while
still proving the L1-L6 dispatch path is wired through to the loadout.

There is no assertion that the loadouts WIN against an arbitrary
opponent \u2014 balancing is Phase 5 work; this is structural+plumbing
coverage only.

CATALOG GAP CLOSED in Phase 4h (charter \u00a724): the V1 alpha catalog now
binds GRANT_EXTRA_ACTION (boltrunner, shock_runner) and ON_EXTRA_ACTION_GRANTED
(arc_lancer), so the L4 mutation (extra-action cap 1 \u2192 2) is exercised
end-to-end in `tests/test_phase4h_new_ops_synergies.py`. This file's L4
test still asserts only the helper-level cap-raise (`_extra_action_cap`
returns 2) \u2014 the in-combat exercise of L4 lives in the Phase 4h test file.
"""

from __future__ import annotations

import pytest

from daimon.engine import resolve_match
from daimon.engine.combat import (
    _build_units,
    _build_condition_ctx,
    _effective_thorns_on_team,
    _extra_action_cap,
    _team_has_mutation,
)
from daimon.loadouts import (
    ShowcaseLoadout,
    get_showcase_loadout,
    list_showcase_loadouts,
    resolve_showcase_loadout,
)
from daimon.npcs import get_npc, npc_loadout

# Test seed: all-zero is the engine's "replay-safe" seed, used widely across
# the existing test suite. Keeping the same seed here means every showcase
# match is deterministic and reproducible.
ZERO_SEED = b"\x00" * 32

# A control opponent: the rookie NPC `sparring_sam` is the lowest-tier
# practice opponent in the bundled roster, mono-NORMAL chassis. Using a
# named NPC instead of an ad-hoc loadout keeps the test grounded in real
# game content and stable across catalog edits.
CONTROL_OPPONENT_NPC = "sparring_sam"


# Expected legendary card_id and rule_change per L1-L6 mutation showcase loadout_id.
# Locked here so a renamed showcase or swapped legendary fails loudly.
# L7-L10 archetype showcases (rainbow / death-rattle / killchain / regen) are
# additional non-mutation loadouts and intentionally NOT in this dict — the
# legendary-binding tests below are scoped to mutation showcases only.
EXPECTED_BY_ID = {
    "showcase_l1_inferno_burnstack":   ("magma_tyrant",       "L1", "INFERNO"),
    "showcase_l2_bulwark_thorns":      ("worldroot_sentinel", "L2", "BULWARK"),
    "showcase_l3_tidal_trickle":       ("tide_empress",       "L3", "TIDAL"),
    "showcase_l4_stormchain_tempo":    ("tempest_apex",       "L4", "STORMCHAIN"),
    "showcase_l5_revenant_cascade":    ("voidking_morr",      "L5", "REVENANT"),
    "showcase_l6_syncretic_mono_void":      ("world_eater",        "L6", "SYNCRETIC"),
}

# Non-mutation archetype showcases shipped after the L1-L6 set. These don't
# bind to a rule_change and don't carry a legendary at slot 0 — they exist to
# prove the engine's combinatorial depth without requiring legendary pulls.
EXPECTED_ARCHETYPE_IDS = {
    "showcase_l7_prism_pantheon":      "ARCHETYPE_SYNCRETIC_RAINBOW",
    "showcase_l8_funeral_pyre":        "ARCHETYPE_DEATH_RATTLE",
    "showcase_l9_apex_predator":       "ARCHETYPE_LIFESTEAL_KILLCHAIN",
    "showcase_l10_worldroot_garden":   "ARCHETYPE_NATURE_REGEN",
}


# ---------------------------------------------------------------------------
# Manifest + loader-level structural tests
# ---------------------------------------------------------------------------

class TestManifest:
    """Manifest-side invariants: 6 loadouts, distinct demonstrates, clean load."""

    def test_full_loadout_count(self):
        """Manifest carries 6 mutation showcases (L1-L6) + 4 archetype
        showcases (L7-L10) = 10 total."""
        sls = list_showcase_loadouts()
        expected_total = len(EXPECTED_BY_ID) + len(EXPECTED_ARCHETYPE_IDS)
        assert len(sls) == expected_total, (
            f"expected {expected_total} showcase loadouts, got {len(sls)}"
        )

    def test_one_per_rule_change(self):
        """Manifest covers L1..L6 mutations exactly once each, alongside the
        L7-L10 archetype demonstrations."""
        sls = list_showcase_loadouts()
        seen = {sl.demonstrates for sl in sls}
        expected = (
            {expected[1] for expected in EXPECTED_BY_ID.values()}  # L1..L6
            | set(EXPECTED_ARCHETYPE_IDS.values())                 # ARCHETYPE_*
        )
        assert seen == expected, seen

    def test_loadout_ids_unique_and_match_expected(self):
        sls = list_showcase_loadouts()
        actual = {sl.loadout_id for sl in sls}
        expected = set(EXPECTED_BY_ID) | set(EXPECTED_ARCHETYPE_IDS)
        assert actual == expected, (
            f"showcase ids drifted: expected {expected}, got {actual}"
        )

    def test_each_loadout_has_six_card_ids(self):
        for sl in list_showcase_loadouts():
            assert len(sl.card_ids) == 6, (
                f"{sl.loadout_id}: expected 6 cards, got {len(sl.card_ids)}"
            )

    def test_each_loadout_has_flavor_and_description(self):
        """Showcase loadouts are documentation \u2014 every one needs prose."""
        for sl in list_showcase_loadouts():
            assert sl.flavor.strip(), f"{sl.loadout_id}: missing flavor"
            assert sl.description.strip(), f"{sl.loadout_id}: missing description"

    def test_get_by_id_round_trips(self):
        for sl in list_showcase_loadouts():
            got = get_showcase_loadout(sl.loadout_id)
            assert got == sl

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            get_showcase_loadout("not_a_real_showcase_id")


# ---------------------------------------------------------------------------
# Per-loadout legendary-binding tests
# ---------------------------------------------------------------------------

class TestLegendaryBinding:
    """Each showcase loadout's slot 0 is the expected legendary, with the
    expected rule_change + archetype tags. A swapped legendary or a
    rule_change drift on the catalog card surfaces here."""

    @pytest.mark.parametrize("loadout_id,expected", list(EXPECTED_BY_ID.items()))
    def test_slot_zero_is_expected_legendary(self, loadout_id, expected):
        expected_card_id, expected_rule_change, expected_archetype = expected
        sl = get_showcase_loadout(loadout_id)
        assert sl.demonstrates == expected_rule_change, (
            f"{loadout_id}: manifest demonstrates={sl.demonstrates!r} "
            f"but expected {expected_rule_change!r}"
        )
        assert sl.card_ids[0] == expected_card_id, (
            f"{loadout_id}: slot 0 = {sl.card_ids[0]!r}, expected {expected_card_id!r}"
        )

        ld = resolve_showcase_loadout(sl)
        leg = ld.cards[0]
        assert leg.card_id == expected_card_id
        assert leg.rule_change == expected_rule_change, (
            f"{loadout_id}: catalog card {leg.card_id!r} rule_change="
            f"{leg.rule_change!r}, expected {expected_rule_change!r}"
        )
        assert leg.archetype == expected_archetype, (
            f"{loadout_id}: catalog card {leg.card_id!r} archetype="
            f"{leg.archetype!r}, expected {expected_archetype!r}"
        )
        # Note: rarity is a catalog-side concept (lives in the card JSON
        # payload), not on the runtime engine.Card. We rely on rule_change
        # being in {L1..L6} as the legendary discriminator since only
        # legendaries carry rule_change tags (locked in test_phase4_distribution.py).


# ---------------------------------------------------------------------------
# Mutation-plumbing tests \u2014 engine helpers see each L1..L6 mutation as
# active when the showcase legendary is alive on the team.
# ---------------------------------------------------------------------------

class TestMutationPlumbing:
    """Verify each loadout's mutation is recognised by the engine helpers
    that actually gate behaviour. These are the same helpers the combat
    loop calls each round, so a failure here means the mutation is dead
    no matter how clever the legendary's flavor text is."""

    def _build(self, loadout_id: str):
        sl = get_showcase_loadout(loadout_id)
        ld = resolve_showcase_loadout(sl)
        return _build_units(ld, side=0)

    def test_l1_mutation_recognized(self):
        team = self._build("showcase_l1_inferno_burnstack")
        assert _team_has_mutation(team, "L1") is True

    def test_l2_grants_thorns_to_every_ally(self):
        """L2 mutation: every ally on team has effective thorns_value >= 2,
        even allies whose intrinsic thorns_value is 0 (charter \u00a722.2 L2)."""
        team = self._build("showcase_l2_bulwark_thorns")
        assert _team_has_mutation(team, "L2") is True
        for u in team:
            eff = _effective_thorns_on_team(u, team)
            assert eff >= 2, (
                f"{u.card.card_id}: effective thorns = {eff}, "
                f"expected >= 2 (L2 grants +2)"
            )

    def test_l3_mutation_recognized(self):
        team = self._build("showcase_l3_tidal_trickle")
        assert _team_has_mutation(team, "L3") is True

    def test_l4_raises_extra_action_cap_to_two(self):
        """L4 mutation: extra-action cap raised from 1 to 2 (charter \u00a722.2 L4).
        Without L4, _extra_action_cap returns 1; with L4 alive, returns 2.

        Phase 4h note: GRANT_EXTRA_ACTION is now catalog-bound (boltrunner,
        shock_runner), and the in-combat consumption of the raised cap is
        exercised in `tests/test_phase4h_new_ops_synergies.py`."""
        team = self._build("showcase_l4_stormchain_tempo")
        assert _team_has_mutation(team, "L4") is True
        assert _extra_action_cap(team) == 2

    def test_l5_mutation_recognized(self):
        team = self._build("showcase_l5_revenant_cascade")
        assert _team_has_mutation(team, "L5") is True

    def test_l6_grants_two_distinct_elements_to_syncretic_cards(self):
        """L6 mutation: SYNCRETIC cards on world_eater's team see effective
        team.distinct_elements = real_distinct + 2 (charter \u00a722.2 L6).
        The L6 showcase is a mono-VOID team \u2014 real distinct = 1, so SYNCRETIC
        cards must see 3 (the \u22652 and \u22653 gates open; the \u22654 stays gated)."""
        team = self._build("showcase_l6_syncretic_mono_void")
        assert _team_has_mutation(team, "L6") is True

        # Build a condition context as if a SYNCRETIC card on this team were
        # checking its trigger condition. _build_condition_ctx applies the
        # L6 distinct bump for SYNCRETIC cards.
        syncretic_unit = next(u for u in team if u.card.archetype == "SYNCRETIC")
        enemies = []  # mutation maths don't depend on enemies for this gate
        ctx = _build_condition_ctx(syncretic_unit, team, enemies, round_number=1)

        # Mono-VOID team: real distinct = 1; with L6, SYNCRETIC cards see 1 + 2 = 3.
        assert ctx["team"]["distinct_elements"] == 3, (
            f"L6 SYNCRETIC card sees distinct_elements="
            f"{ctx['team']['distinct_elements']}, expected 3 (mono + L6 bump)"
        )

        # Sanity: a non-SYNCRETIC card on the same team sees the real distinct
        # count (1), proving the L6 bump is SYNCRETIC-scoped, not team-scoped.
        non_syncretic_unit = next(u for u in team if u.card.archetype != "SYNCRETIC")
        ctx2 = _build_condition_ctx(non_syncretic_unit, team, enemies, round_number=1)
        assert ctx2["team"]["distinct_elements"] == 1, (
            f"L6 non-SYNCRETIC card sees distinct_elements="
            f"{ctx2['team']['distinct_elements']}, expected 1 (no L6 bump for non-SYNCRETIC)"
        )


# ---------------------------------------------------------------------------
# End-to-end smoke: every showcase loadout can resolve a match against a
# real, named opponent without crashing.
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Each showcase loadout vs the rookie sparring opponent must resolve
    cleanly. We don't assert the showcase WINS \u2014 balance is Phase 5 \u2014
    only that the engine produces a determined MatchResult with a winner
    or a draw and at least one round of combat."""

    @pytest.mark.parametrize(
        "loadout_id",
        list(EXPECTED_BY_ID) + list(EXPECTED_ARCHETYPE_IDS),
    )
    def test_match_resolves_cleanly(self, loadout_id):
        sl = get_showcase_loadout(loadout_id)
        showcase = resolve_showcase_loadout(sl)

        opponent_npc = get_npc(CONTROL_OPPONENT_NPC)
        opponent = npc_loadout(opponent_npc)

        result = resolve_match(showcase, opponent, ZERO_SEED)

        assert result.seed == ZERO_SEED
        assert len(result.rounds) >= 1, (
            f"{loadout_id}: match produced 0 rounds"
        )
        assert result.reason in ("wipe", "round_cap", "draw"), result.reason
        assert result.winner in (0, 1, None)
        assert result.side_a_final_hp >= 0
        assert result.side_b_final_hp >= 0
