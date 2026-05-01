"""Tests for the Imprint system — per-serial stats, match history, trophies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pytest

from daimon import collection as collection_mod
from daimon import imprint as imprint_mod
from daimon import match_history as match_history_mod
from daimon.collection import Serial, new_serial, load_collection, append_serial
from daimon.identity import keys as identity_keys
from daimon.imprint import (
    compute_trophies,
    extract_per_card_stats,
    load_imprint_stats,
    record_match,
    resolve_serials_for_loadout,
    get_serial_stats,
    save_imprint_stats,
)
from daimon.match_history import (
    append_match,
    matches_for_serial,
    recent_matches,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def iso(monkeypatch, tmp_path):
    """Redirect all imprint + collection paths to tmp_path."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH", cfg / "coll.json")
    monkeypatch.setattr(imprint_mod, "IMPRINT_STATS_PATH",
                        cfg / "imprint_stats.json")
    monkeypatch.setattr(match_history_mod, "MATCH_HISTORY_PATH",
                        cfg / "match_history.jsonl")
    return cfg


# ---------------------------------------------------------------------------
# Fake engine types for extract_per_card_stats
# ---------------------------------------------------------------------------

@dataclass
class FakeCombatEvent:
    kind: str
    actor_side: int
    actor_position: int
    actor_card_id: str
    target_side: Optional[int] = None
    target_position: Optional[int] = None
    target_card_id: Optional[str] = None
    amount: Optional[int] = None
    hp_after: Dict[Tuple[int, int], int] = field(default_factory=dict)
    reason: Optional[str] = None
    status_applied: Optional[str] = None
    log_line: str = ""
    triggers: List["FakeCombatEvent"] = field(default_factory=list)


@dataclass
class FakeRoundLog:
    round_number: int
    first_player: int = 0
    actions: List[str] = field(default_factory=list)
    events: List[FakeCombatEvent] = field(default_factory=list)
    side_a_hp_total: int = 0
    side_b_hp_total: int = 0


@dataclass
class FakeMatchResult:
    seed: bytes
    rounds: List[FakeRoundLog]
    winner: Optional[int]
    side_a_final_hp: int
    side_b_final_hp: int
    reason: str


@dataclass(frozen=True)
class FakeCard:
    card_id: str
    species: str = ""
    element: int = 1
    atk: int = 5
    defense: int = 5
    hp: int = 20
    spd: int = 5
    triggers: tuple = ()
    rule_change: Optional[str] = None
    archetype: Optional[str] = None
    canon: Optional[str] = None


@dataclass(frozen=True)
class FakeLoadout:
    cards: tuple


# ---------------------------------------------------------------------------
# Serial dataclass backward compat
# ---------------------------------------------------------------------------

class TestSerialBackwardCompat:
    def test_old_collection_without_imprint_fields(self, iso):
        old_doc = {
            "pubkey_hex": "deadbeef",
            "serials": [{
                "serial": "aaa", "card_id": "iron_boar",
                "pack": "v1_alpha", "rarity": "common",
                "minted_at": "2026-04-30T00:00:00+00:00",
                "minted_via": "pull", "ledger_entry_hash": "abc",
            }],
        }
        path = iso / "coll.json"
        path.write_text(json.dumps(old_doc), encoding="utf-8")
        data = load_collection(path)
        s = data["serials"][0]
        assert s["card_id"] == "iron_boar"
        assert s.get("mint_number") is None
        assert s.get("edition") is None
        assert s.get("original_owner_pubkey") is None


class TestNewSerial:
    def test_edition_and_owner(self, iso):
        s = new_serial("iron_boar", "v1_alpha", "common",
                        edition="1st", original_owner_pubkey="abc123")
        assert s.edition == "1st"
        assert s.original_owner_pubkey == "abc123"
        assert s.mint_number is None

    def test_defaults_none(self, iso):
        s = new_serial("iron_boar", "v1_alpha", "common")
        assert s.edition is None
        assert s.original_owner_pubkey is None


# ---------------------------------------------------------------------------
# record_match + stats
# ---------------------------------------------------------------------------

class TestRecordMatch:
    def test_first_match_creates_entry(self, iso):
        entry = record_match("uuid1", "iron_boar", won=True,
                             kills=2, damage_dealt=100, damage_taken=50)
        assert entry["wins"] == 1
        assert entry["losses"] == 0
        assert entry["kills"] == 2
        assert entry["damage_dealt"] == 100
        assert entry["damage_taken"] == 50
        assert entry["matches_played"] == 1
        assert entry["streak"] == 1
        assert entry["best_streak"] == 1

    def test_incremental_updates(self, iso):
        record_match("uuid1", "iron_boar", won=True, kills=1, damage_dealt=50)
        record_match("uuid1", "iron_boar", won=True, kills=2, damage_dealt=80)
        entry = record_match("uuid1", "iron_boar", won=False, kills=0,
                             damage_dealt=20, damage_taken=100)
        assert entry["wins"] == 2
        assert entry["losses"] == 1
        assert entry["kills"] == 3
        assert entry["damage_dealt"] == 150
        assert entry["matches_played"] == 3
        assert entry["streak"] == 0
        assert entry["best_streak"] == 2

    def test_get_serial_stats(self, iso):
        record_match("uuid1", "iron_boar", won=True)
        stats = get_serial_stats("uuid1")
        assert stats is not None
        assert stats["wins"] == 1
        assert get_serial_stats("nonexistent") is None

    def test_persistence(self, iso):
        record_match("uuid1", "iron_boar", won=True)
        data = load_imprint_stats()
        assert "uuid1" in data["serials"]
        assert data["version"] == 1


# ---------------------------------------------------------------------------
# Trophies
# ---------------------------------------------------------------------------

class TestComputeTrophies:
    def test_no_trophies_for_new_card(self):
        assert compute_trophies({"wins": 0, "kills": 0, "best_streak": 0}) == []

    def test_veteran(self):
        t = compute_trophies({"wins": 10, "kills": 5, "best_streak": 3})
        assert "veteran" in t
        assert "centurion" not in t

    def test_centurion(self):
        t = compute_trophies({"wins": 100, "kills": 50, "best_streak": 8})
        assert "centurion" in t
        assert "veteran" in t

    def test_slayer(self):
        t = compute_trophies({"wins": 5, "kills": 100, "best_streak": 2})
        assert "slayer" in t

    def test_streak_tiers(self):
        assert "undefeated_5" in compute_trophies(
            {"wins": 5, "kills": 0, "best_streak": 5})
        assert "undefeated_10" in compute_trophies(
            {"wins": 10, "kills": 0, "best_streak": 10})
        assert "undefeated_25" in compute_trophies(
            {"wins": 25, "kills": 0, "best_streak": 25})

    def test_streak_tiers_are_exclusive(self):
        t = compute_trophies({"wins": 10, "kills": 0, "best_streak": 10})
        assert "undefeated_10" in t
        assert "undefeated_5" not in t
        assert "undefeated_25" not in t


# ---------------------------------------------------------------------------
# resolve_serials_for_loadout
# ---------------------------------------------------------------------------

class TestResolveSerials:
    def test_picks_oldest_serial(self):
        serials = [
            {"serial": "newer", "card_id": "iron_boar",
             "minted_at": "2026-04-30T12:00:00+00:00"},
            {"serial": "older", "card_id": "iron_boar",
             "minted_at": "2026-04-29T12:00:00+00:00"},
        ]
        result = resolve_serials_for_loadout(["iron_boar"], serials)
        assert result["iron_boar"] == "older"

    def test_no_matching_serial(self):
        result = resolve_serials_for_loadout(["voltcat"], [])
        assert result == {}

    def test_multiple_cards(self):
        serials = [
            {"serial": "s1", "card_id": "a", "minted_at": "2026-01-01"},
            {"serial": "s2", "card_id": "b", "minted_at": "2026-01-02"},
        ]
        result = resolve_serials_for_loadout(["a", "b"], serials)
        assert result == {"a": "s1", "b": "s2"}

    def test_multiple_serials_same_card(self):
        """When owning multiple copies, the oldest serial is picked."""
        serials = [
            {"serial": "s1", "card_id": "a", "minted_at": "2026-01-01"},
            {"serial": "s2", "card_id": "a", "minted_at": "2026-01-02"},
        ]
        result = resolve_serials_for_loadout(["a"], serials)
        assert result["a"] == "s1"


# ---------------------------------------------------------------------------
# extract_per_card_stats
# ---------------------------------------------------------------------------

class TestExtractPerCardStats:
    def _make_loadout(self, n=2):
        cards = tuple(FakeCard(card_id=f"card_{i}") for i in range(n))
        return FakeLoadout(cards=cards)

    def test_damage_dealt_and_taken(self):
        lo = self._make_loadout(2)
        events = [
            FakeCombatEvent(
                kind="damage", actor_side=0, actor_position=0,
                actor_card_id="card_0", target_side=1, target_position=0,
                target_card_id="enemy_0", amount=30, triggers=[],
            ),
            FakeCombatEvent(
                kind="damage", actor_side=1, actor_position=0,
                actor_card_id="enemy_0", target_side=0, target_position=1,
                target_card_id="card_1", amount=15, triggers=[],
            ),
        ]
        result = FakeMatchResult(
            seed=b"\x00" * 32,
            rounds=[FakeRoundLog(round_number=1, events=events)],
            winner=0, side_a_final_hp=100, side_b_final_hp=0, reason="wipe",
        )
        stats = extract_per_card_stats(result, lo, side=0)
        assert stats[0]["damage_dealt"] == 30
        assert stats[0]["damage_taken"] == 0
        assert stats[1]["damage_dealt"] == 0
        assert stats[1]["damage_taken"] == 15

    def test_kills_attributed_to_attacker(self):
        lo = self._make_loadout(2)
        death = FakeCombatEvent(
            kind="death", actor_side=1, actor_position=0,
            actor_card_id="enemy_0",
        )
        events = [
            FakeCombatEvent(
                kind="damage", actor_side=0, actor_position=0,
                actor_card_id="card_0", target_side=1, target_position=0,
                target_card_id="enemy_0", amount=20,
                triggers=[death],
            ),
        ]
        result = FakeMatchResult(
            seed=b"\x00" * 32,
            rounds=[FakeRoundLog(round_number=1, events=events)],
            winner=0, side_a_final_hp=100, side_b_final_hp=0, reason="wipe",
        )
        stats = extract_per_card_stats(result, lo, side=0)
        assert stats[0]["kills"] == 1
        assert stats[1]["kills"] == 0

    def test_survived_flag(self):
        lo = self._make_loadout(2)
        events = [
            FakeCombatEvent(
                kind="death", actor_side=0, actor_position=1,
                actor_card_id="card_1",
            ),
        ]
        result = FakeMatchResult(
            seed=b"\x00" * 32,
            rounds=[FakeRoundLog(round_number=1, events=events)],
            winner=1, side_a_final_hp=0, side_b_final_hp=50, reason="wipe",
        )
        stats = extract_per_card_stats(result, lo, side=0)
        assert stats[0]["survived"] is True
        assert stats[1]["survived"] is False

    def test_empty_match(self):
        lo = self._make_loadout(2)
        result = FakeMatchResult(
            seed=b"\x00" * 32, rounds=[], winner=None,
            side_a_final_hp=0, side_b_final_hp=0, reason="draw",
        )
        stats = extract_per_card_stats(result, lo, side=0)
        assert len(stats) == 2
        assert all(s["kills"] == 0 and s["survived"] for s in stats)


# ---------------------------------------------------------------------------
# Match history
# ---------------------------------------------------------------------------

class TestMatchHistory:
    def test_append_and_recent(self, iso):
        for i in range(5):
            append_match({"match_id": f"m{i}", "loadout_serials": [f"s{i}"]})
        entries = recent_matches(limit=3)
        assert len(entries) == 3
        assert entries[0]["match_id"] == "m2"

    def test_matches_for_serial(self, iso):
        append_match({"match_id": "m1", "loadout_serials": ["s1", "s2"]})
        append_match({"match_id": "m2", "loadout_serials": ["s2", "s3"]})
        append_match({"match_id": "m3", "loadout_serials": ["s1"]})
        result = matches_for_serial("s1")
        assert len(result) == 2
        assert result[0]["match_id"] == "m1"
        assert result[1]["match_id"] == "m3"

    def test_empty_history(self, iso):
        assert recent_matches() == []
        assert matches_for_serial("nonexistent") == []
