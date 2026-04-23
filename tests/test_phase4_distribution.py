"""Phase 4d/4f distribution-lock tests (V1, 2026-04-23).

Locks the V1 200-card pool composition. Every assertion here is a *gate*:
adding/removing a card without updating the design doc + this file must
make the test suite fail.

Locked invariants (per `docs/card_design_v1.md` §3 + §4 + §23.2):

  total cards          == 200
  rarity histogram     == {common: 98, uncommon: 60, rare: 28, epic: 8, legendary: 6}
                          (Phase 4f-pool 2026-04-23: epic 12→8, legendary 2→6;
                           4 epics promoted in-place, see §23.6)
  vanilla commons      <= 30% of common pool (29 of 98)
  per-element minimums (each of the 5 elements gets enough representation
                        that mono-element teams of 6 are buildable at every
                        rarity tier — i.e. ≥6 cards per element overall, and
                        ≥1 card per element per rarity tier where that tier
                        is element-bearing)
  per-archetype × rarity matrix (§23.2) — locks the soft-cluster sizing so
                        any future authoring drift requires explicit doc
                        update before tests pass.

The legendary/epic exact-set tests already live in
`tests/test_phase3_anchors.py::TestCatalogLoad`. This file complements those
with the bulk-tier (common/uncommon/rare) shape gates Phase 4 is responsible
for, plus the total-count + vanilla-cap + per-archetype matrix rules.

Why a separate file? `test_phase3_anchors.py` is *integration* — it
exercises individual anchor cards through the engine. This file is
*structural* — it asserts the catalog SHAPE without running combat.
Different failure modes deserve different test files.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest


PACK_DIR = Path(__file__).resolve().parent.parent / "daimon" / "catalog" / "v1_alpha"
MANIFEST_PATH = PACK_DIR / "manifest.json"

# All 6 elements in the V1 pool. The first five form a closed
# rock-paper-scissors-plus-void ring; NORMAL stands deliberately outside
# the ring (see daimon/engine/elements.py + docs/card_design_v1.md §18).
# Tests that care about the affinity ring iterate `RING_ELEMENTS` instead.
ELEMENTS = ("FIRE", "WATER", "NATURE", "VOLT", "VOID", "NORMAL")
RING_ELEMENTS = ("FIRE", "WATER", "NATURE", "VOLT", "VOID")
TIERS = ("common", "uncommon", "rare", "epic", "legendary")

# V1 lock — these are THE numbers. Changing them requires a doc update.
V1_TOTAL = 200
V1_RARITY = {
    "common":    98,
    "uncommon":  60,
    "rare":      28,
    "epic":       8,   # Phase 4f-pool: 12→8 (4 promoted to legendary, §23.6)
    "legendary":  6,   # Phase 4f-pool: 2→6  (one rule-changer per archetype, §22.2)
}
# Per-rarity vanilla cap (commons only — see card_design_v1.md §4)
VANILLA_COMMON_CAP_PCT = 30
# NORMAL element pool minimum — see §18. Locks the design intent that
# NORMAL is a non-trivial splash-support element, not a token presence.
# Currently 15 NORMAL cards (8C/4U/2R/1E); floor allows minor future
# trimming without breaking the gate.
NORMAL_MIN_TOTAL = 10
NORMAL_MIN_PER_BULK_TIER = {
    "common":   6,   # NORMAL must be a real splash option in pulls
    "uncommon": 3,
    "rare":     2,
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


@pytest.fixture(scope="module")
def cards_on_disk() -> list[dict]:
    """Read every card JSON the manifest references — single source of truth
    for trigger counts (manifest doesn't carry triggers)."""
    m = json.loads(MANIFEST_PATH.read_text())
    out: list[dict] = []
    for entry in m["cards"]:
        out.append(json.loads((PACK_DIR / entry["file"]).read_text()))
    return out


# ---------------------------------------------------------------------------
# 1. Total count + rarity histogram (the headline invariants)
# ---------------------------------------------------------------------------


class TestPoolShape:
    def test_total_card_count_locked_at_200(self, manifest):
        assert len(manifest["cards"]) == V1_TOTAL, (
            f"V1 pool size drifted: expected {V1_TOTAL}, got "
            f"{len(manifest['cards'])}. Update card_design_v1.md §3 if intentional."
        )

    def test_rarity_distribution_locked(self, manifest):
        actual = Counter(e["rarity"] for e in manifest["cards"])
        for rarity, expected_count in V1_RARITY.items():
            assert actual[rarity] == expected_count, (
                f"{rarity} count drifted: expected {expected_count}, got "
                f"{actual[rarity]}. Full distribution: {dict(actual)}. "
                f"Update card_design_v1.md §3 if intentional."
            )
        # Catch any unexpected rarity tier appearing in the pool.
        unknown = set(actual) - set(V1_RARITY)
        assert not unknown, f"Unknown rarity tier(s) in pool: {unknown}"


# ---------------------------------------------------------------------------
# 2. Vanilla-common cap (≤30% of commons may have zero triggers)
# ---------------------------------------------------------------------------


class TestVanillaCap:
    def test_vanilla_commons_under_cap(self, cards_on_disk):
        commons = [c for c in cards_on_disk if c.get("rarity") == "common"]
        vanilla = [c for c in commons if not c.get("triggers")]
        cap = (len(commons) * VANILLA_COMMON_CAP_PCT) // 100
        pct = (len(vanilla) * 100) // max(len(commons), 1)
        assert len(vanilla) <= cap, (
            f"Vanilla commons {len(vanilla)}/{len(commons)} ({pct}%) exceeds "
            f"the {VANILLA_COMMON_CAP_PCT}% cap (max {cap}). "
            f"Vanilla cards: {sorted(c['card_id'] for c in vanilla)}"
        )


# ---------------------------------------------------------------------------
# 3. Per-element representation — every element must support mono-element
#    teams of 6 across all bulk tiers, and FLUX-host elements must appear.
# ---------------------------------------------------------------------------


class TestElementCoverage:
    """Catches 'we accidentally authored 50 FIRE cards and 5 WATER' drift.

    Concretely: every element needs enough cards that a mono-element 6-card
    loadout is buildable, AND each bulk tier (common/uncommon/rare) carries
    each element at least once so element-pure pulls aren't impossible.

    NORMAL gets bespoke gates (it's intentionally smaller than the ring
    elements — splashable support, not a primary archetype home). The
    common-tier balance check explicitly excludes NORMAL since NORMAL is
    *meant* to be ~half the size of any ring element at common.
    """

    def test_each_element_has_at_least_six_cards(self, manifest):
        """A mono-element loadout requires 6 distinct cards of that element."""
        per_elem = Counter(e["element"] for e in manifest["cards"])
        for elem in ELEMENTS:
            assert per_elem[elem] >= 6, (
                f"Element {elem} has only {per_elem[elem]} cards — "
                f"mono-element loadout unbuildable. Need ≥6."
            )

    def test_every_bulk_tier_covers_every_element(self, manifest):
        """common/uncommon/rare must each include all 6 elements (so pulls
        at any of these tiers can return any element, including NORMAL)."""
        by_tier: dict[str, set[str]] = defaultdict(set)
        for e in manifest["cards"]:
            by_tier[e["rarity"]].add(e["element"])
        for tier in ("common", "uncommon", "rare"):
            missing = set(ELEMENTS) - by_tier[tier]
            assert not missing, (
                f"Tier {tier!r} missing elements {sorted(missing)}. "
                f"Present: {sorted(by_tier[tier])}"
            )

    def test_element_balance_within_common_tier(self, manifest):
        """The 5 RING elements at common tier should be roughly even (we
        expect 18 each). Allow ±25% from the ring-mean before flagging.
        Catches 'half the commons ended up FIRE' authoring bugs.

        NORMAL is excluded — its intentional under-allocation (8 commons,
        ~half of a ring element) would falsely fail a 6-element mean
        comparison. NORMAL has its own minimum gate in
        `TestNormalElementPool::test_normal_meets_minimum_per_bulk_tier`."""
        commons = [e for e in manifest["cards"] if e["rarity"] == "common"]
        ring_commons = [e for e in commons if e["element"] in RING_ELEMENTS]
        per_elem = Counter(e["element"] for e in ring_commons)
        mean = len(ring_commons) / len(RING_ELEMENTS)
        lower, upper = mean * 0.75, mean * 1.25
        for elem in RING_ELEMENTS:
            n = per_elem[elem]
            assert lower <= n <= upper, (
                f"Common tier ring element {elem} has {n} cards; expected "
                f"within [{lower:.1f}, {upper:.1f}] (ring-mean {mean:.1f}). "
                f"Ring distribution: {dict(per_elem)}"
            )


# ---------------------------------------------------------------------------
# 3b. NORMAL element gates — Phase 4e (see card_design_v1.md §18)
# ---------------------------------------------------------------------------


class TestNormalElementPool:
    """NORMAL is the splashable utility element added in Phase 4e. It stands
    outside the type-effectiveness ring (always 1.0×) and exists as the
    home for archetype-less support cards. These gates lock the design
    intent: NORMAL must be a real splash option (not a token), and every
    NORMAL card must carry `archetype: null`."""

    def test_normal_meets_minimum_total(self, manifest):
        """NORMAL must be a non-trivial splash element, not a token tier."""
        per_elem = Counter(e["element"] for e in manifest["cards"])
        n = per_elem.get("NORMAL", 0)
        assert n >= NORMAL_MIN_TOTAL, (
            f"NORMAL pool size {n} below minimum {NORMAL_MIN_TOTAL} — "
            f"NORMAL is meant to be a real splash option, not a token. "
            f"Update card_design_v1.md §18 if shrinking is intentional."
        )

    def test_normal_meets_minimum_per_bulk_tier(self, manifest):
        """NORMAL must appear in C/U/R at meaningful counts so pulls at any
        bulk tier can plausibly return a NORMAL card."""
        by_tier: dict[str, int] = defaultdict(int)
        for e in manifest["cards"]:
            if e["element"] == "NORMAL":
                by_tier[e["rarity"]] += 1
        for tier, floor in NORMAL_MIN_PER_BULK_TIER.items():
            n = by_tier[tier]
            assert n >= floor, (
                f"NORMAL has {n} {tier}s; minimum is {floor}. "
                f"NORMAL distribution: {dict(by_tier)}"
            )

    def test_normal_cards_have_null_archetype(self, cards_on_disk):
        """NORMAL cards must carry `archetype: null` — they're intentionally
        archetype-less so they splash into any deck without distorting
        archetype identity."""
        violators: list[str] = []
        for c in cards_on_disk:
            if c.get("element") != "NORMAL":
                continue
            arch = c.get("archetype")
            # JSON null deserializes to Python None; allow missing key as
            # equivalent (caller treated null vs absent as same intent).
            if arch is not None:
                violators.append(f"{c['card_id']}: archetype={arch!r}")
        assert not violators, (
            "NORMAL cards must have archetype: null — see §18. "
            "Violators:\n  " + "\n  ".join(violators)
        )

    def test_no_elemental_card_carries_normal_archetype_label(self, cards_on_disk):
        """Inverse direction — no FIRE/WATER/NATURE/VOLT/VOID card should
        get `archetype: "NORMAL"` either. NORMAL is an *element*, not an
        archetype label."""
        violators: list[str] = []
        for c in cards_on_disk:
            if str(c.get("archetype", "")).upper() == "NORMAL":
                violators.append(f"{c['card_id']}: archetype=NORMAL")
        assert not violators, (
            "NORMAL is an element, not an archetype label. Violators:\n  "
            + "\n  ".join(violators)
        )


# ---------------------------------------------------------------------------
# 4. Trigger budget per rarity — design doc §4 power budgets must hold
# ---------------------------------------------------------------------------


# (rarity, max_triggers) per §4. Legendaries can carry 3, epics 2, lower
# tiers progressively fewer; commons capped at 1 (vanilla allowed = 0).
TIER_TRIGGER_CAP = {
    "common":    1,
    "uncommon":  1,
    "rare":      2,
    "epic":      2,
    "legendary": 3,
}

# Known scheduled debt from Phase 4a (rarity reconciliation). These five
# cards were demoted from `legendary` → `rare` with their original 3-trigger
# legendary scaffolding INTACT — Phase 4a's docstring explicitly states:
# "Stats + triggers are intentionally UNTOUCHED here; Phase 5 (sim balance)
# will identify individual cards needing stat tuning after the full pool is
# authored." Allowlisted here so the test still catches NEW drift while
# Phase 5 carries the responsibility of collapsing this set to 0.
PHASE5_TRIGGER_DEBT = {
    "pyrotyrant",
    "leviathan_prime",
    "worldroot_colossus",
    "storm_celestial",
    "echo_lich",
}


class TestTriggerBudget:
    def test_no_card_exceeds_its_tier_trigger_cap(self, cards_on_disk):
        """No card carries more triggers than its rarity tier permits.

        Excludes `PHASE5_TRIGGER_DEBT` — five rares carrying legendary-tier
        trigger counts as scheduled debt from Phase-4a reconciliation. Phase
        5 must normalize them; until then they're allowlisted explicitly.
        """
        violators: list[str] = []
        for c in cards_on_disk:
            cid = c.get("card_id")
            if cid in PHASE5_TRIGGER_DEBT:
                continue
            tier = c.get("rarity")
            cap = TIER_TRIGGER_CAP.get(tier)
            if cap is None:
                continue
            n = len(c.get("triggers", []))
            if n > cap:
                violators.append(f"{cid} ({tier}): {n} triggers > cap {cap}")
        assert not violators, (
            "Trigger-budget violations:\n  " + "\n  ".join(violators)
        )

    def test_phase5_debt_set_is_real(self, cards_on_disk):
        """Sanity gate on the allowlist — every card we excused must (a) still
        exist and (b) actually be over the cap. Stops the allowlist from
        going stale and silently masking new violators."""
        by_id = {c["card_id"]: c for c in cards_on_disk}
        stale: list[str] = []
        for cid in PHASE5_TRIGGER_DEBT:
            c = by_id.get(cid)
            assert c is not None, (
                f"PHASE5_TRIGGER_DEBT references nonexistent card {cid!r}"
            )
            cap = TIER_TRIGGER_CAP.get(c.get("rarity"))
            n = len(c.get("triggers", []))
            if cap is not None and n <= cap:
                # Card has been normalized — remove it from the allowlist.
                stale.append(cid)
        assert not stale, (
            f"PHASE5_TRIGGER_DEBT contains cards already within budget: "
            f"{stale}. Remove them from the allowlist."
        )


# ---------------------------------------------------------------------------
# 5. card_id uniqueness — manifest entries vs JSON files vs each other
# ---------------------------------------------------------------------------


class TestUniqueness:
    def test_manifest_card_ids_are_unique(self, manifest):
        ids = [e["card_id"] for e in manifest["cards"]]
        dupes = [cid for cid, n in Counter(ids).items() if n > 1]
        assert not dupes, f"Duplicate card_ids in manifest: {dupes}"

    def test_each_manifest_entry_has_a_disk_file(self, manifest):
        missing = [
            e["card_id"] for e in manifest["cards"]
            if not (PACK_DIR / e["file"]).exists()
        ]
        assert not missing, f"Manifest references missing files: {missing}"

    def test_no_orphan_json_files(self, manifest):
        """Every card-shaped JSON in the pack must be referenced by manifest.
        Catches 'authored a card, forgot to add it to manifest' drift."""
        manifest_files = {e["file"] for e in manifest["cards"]}
        manifest_files.add("manifest.json")  # the manifest itself is allowed
        on_disk = {p.name for p in PACK_DIR.glob("*.json")}
        orphans = on_disk - manifest_files
        assert not orphans, (
            f"Card JSONs on disk but missing from manifest: {sorted(orphans)}"
        )


# ---------------------------------------------------------------------------
# 6. Per-archetype × rarity matrix lock — Phase 4f-pool extension (§23.2)
#
# The soft-cluster cards-per-archetype counts are LOCKED post-promotion. Any
# future card add/remove/retag must update both this table and §23.2 in the
# design doc — the assertion failure is the prompt to remember.
#
# The matrix below is the POST-PROMOTION steady state (§23.2 measured table).
# `null` archetype covers NORMAL element + the 28 element-flavored utility
# rares + the 1 NORMAL epic. See §23.2 lock-text for why rares are null by
# convention (anti-pattern guard against archetype-as-engine-gate).
# ---------------------------------------------------------------------------


# Locked per-archetype × rarity counts. Tuple form because dicts of dicts get
# noisy in failure output; we serialize to a (rarity, archetype, count) sort
# for reporting.
ARCHETYPE_MATRIX = {
    # archetype:    {rarity: count}
    "INFERNO":    {"common": 13, "uncommon":  6, "rare": 0, "epic": 1, "legendary": 1},
    "BULWARK":    {"common": 13, "uncommon":  7, "rare": 0, "epic": 1, "legendary": 1},
    "TIDAL":      {"common": 13, "uncommon":  7, "rare": 0, "epic": 1, "legendary": 1},
    "STORMCHAIN": {"common": 13, "uncommon":  7, "rare": 0, "epic": 1, "legendary": 1},
    "REVENANT":   {"common": 13, "uncommon":  7, "rare": 0, "epic": 1, "legendary": 1},
    "FLUX":       {"common": 10, "uncommon": 10, "rare": 0, "epic": 2, "legendary": 1},
    # `null` = NORMAL element (15) + element-flavored utility (53). All 28
    # rares are archetype:null by §23.2 convention — anti-pattern guard.
    None:         {"common": 23, "uncommon": 16, "rare": 28, "epic": 1, "legendary": 0},
}


class TestArchetypeMatrix:
    """Locks the §23.2 per-archetype × rarity counts post-Phase-4f promotion.

    This is the AUTHORING-CONTRACT level test: any soft-cluster reshuffle
    (card retag, card add/remove with archetype effect) shows up here as a
    matrix-cell drift. Failure prompts the design-doc update.
    """

    def test_matrix_matches_disk(self, cards_on_disk):
        """Measure each (archetype, rarity) cell from disk and compare to the
        locked table. Reports cell-level diffs on failure."""
        measured: dict[tuple, int] = Counter()
        for c in cards_on_disk:
            arch = c.get("archetype")  # None for null/missing
            rarity = c.get("rarity")
            measured[(arch, rarity)] += 1

        expected: dict[tuple, int] = {}
        for arch, by_rarity in ARCHETYPE_MATRIX.items():
            for rarity, n in by_rarity.items():
                expected[(arch, rarity)] = n

        # Every expected cell must match.
        diffs: list[str] = []
        for cell, n in expected.items():
            arch, rarity = cell
            actual_n = measured.get(cell, 0)
            if actual_n != n:
                diffs.append(
                    f"  archetype={arch!r:12s} rarity={rarity:11s}: "
                    f"expected {n}, got {actual_n}"
                )
        # Catch any unexpected (archetype, rarity) pair.
        unexpected = sorted(
            (cell, n) for cell, n in measured.items() if cell not in expected
        )
        if unexpected:
            for cell, n in unexpected:
                arch, rarity = cell
                diffs.append(
                    f"  archetype={arch!r:12s} rarity={rarity:11s}: "
                    f"unexpected cell with {n} card(s)"
                )
        assert not diffs, (
            "Per-archetype × rarity matrix drifted from §23.2 lock:\n"
            + "\n".join(diffs)
            + "\n\nIf this drift is intentional, update both:\n"
            + "  1. ARCHETYPE_MATRIX in this file\n"
            + "  2. §23.2 in docs/card_design_v1.md"
        )

    def test_matrix_totals_match_rarity_histogram(self):
        """Sanity gate: matrix row+column sums must equal V1_RARITY totals.
        Catches authoring drift in the matrix itself (not on disk)."""
        # Column sums = per-rarity totals.
        per_rarity: Counter = Counter()
        for arch, by_rarity in ARCHETYPE_MATRIX.items():
            for rarity, n in by_rarity.items():
                per_rarity[rarity] += n
        for rarity, expected_n in V1_RARITY.items():
            assert per_rarity[rarity] == expected_n, (
                f"ARCHETYPE_MATRIX column sum for {rarity}={per_rarity[rarity]} "
                f"diverges from V1_RARITY[{rarity}]={expected_n}. "
                f"Either ARCHETYPE_MATRIX or V1_RARITY is wrong — fix both."
            )
        # Total grand sum must equal V1_TOTAL.
        grand = sum(per_rarity.values())
        assert grand == V1_TOTAL, (
            f"ARCHETYPE_MATRIX grand total {grand} != V1_TOTAL {V1_TOTAL}"
        )

    def test_only_legendaries_carry_rule_change_tag(self, cards_on_disk):
        """rule_change is the legendary mutation marker; nothing else should
        carry it. Catches accidental tagging of non-legendaries — the engine
        wouldn't refuse it (rule_change is engine-active regardless of rarity)
        but it'd be a balance-explosion landmine. Lock at distribution layer."""
        violators = [
            f"{c['card_id']} (rarity={c.get('rarity')}, "
            f"rule_change={c.get('rule_change')})"
            for c in cards_on_disk
            if c.get("rule_change") is not None
            and c.get("rarity") != "legendary"
        ]
        assert not violators, (
            "Non-legendary cards carry rule_change tag (balance landmine):\n  "
            + "\n  ".join(violators)
        )

    def test_all_six_legendaries_carry_expected_rule_change(self, cards_on_disk):
        """Each of the 6 V1 legendaries must carry its locked mutation ID.
        The engine dispatches off rule_change; a missing tag silently strips
        the card's identity."""
        expected = {
            "magma_tyrant":       "L1",
            "worldroot_sentinel": "L2",
            "tide_empress":       "L3",
            "tempest_apex":       "L4",
            "voidking_morr":      "L5",
            "world_eater":        "L6",
        }
        by_id = {c["card_id"]: c for c in cards_on_disk}
        diffs: list[str] = []
        for cid, want in expected.items():
            c = by_id.get(cid)
            if c is None:
                diffs.append(f"  {cid}: missing from catalog")
                continue
            got = c.get("rule_change")
            if got != want:
                diffs.append(f"  {cid}: expected rule_change={want!r}, got {got!r}")
        assert not diffs, (
            "Legendary rule_change tags drifted from §22.2 lock:\n"
            + "\n".join(diffs)
        )
