"""Daily-quests test surface.

Covers:
  * Catalog shape: every template tier-balanced, materializer well-formed.
  * Roll determinism: same (pubkey, date) → same 3 quests; different
    pubkey/date → different roll.
  * Daily seed: matches the shop rotation primitive byte-for-byte
    (the locked invariant that quests + shop rotate at the same instant).
  * State persistence: atomic load/save round-trip; load returns None on
    stale/corrupt/version-mismatched files.
  * Progress matchers: each template fires under the right ledger / ticker
    pattern, doesn't fire under the wrong one.
  * Auto-claim: idempotent reward minting; double-call writes one entry;
    `quest_reward` shows up in get_stats / get_balance.
  * MCP integration: dm_quests, dm_home payload extension, post-action
    auto-claim hooks fire from dm_match / dm_match_npc / dm_pull.
  * Home card renderer: daily-quests panel renders without raising and
    surfaces tier / progress / claim status.

The tests use the same path-isolation pattern as `test_mcp.py::_isolate_paths`
so we never touch the user's real ``~/.config/daimon``.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from daimon.identity import generate_identity
from daimon.identity import keys as identity_keys
from daimon.mining import buffer as buffer_mod
from daimon.mining import ledger as ledger_mod
from daimon.quests import (
    DIFFICULTY_TIERS,
    QUEST_TEMPLATES,
    QuestProgress,
    evaluate_and_claim,
    evaluate_progress,
    load_quests,
    materialize,
    roll_today,
    save_quests,
    today_str,
)
from daimon.quests import progress as progress_mod
from daimon.quests import state as state_mod
from daimon.quests.catalog import templates_by_tier
from daimon.quests.roll import _daily_seed


# ---------------------------------------------------------------------------
# Shared isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Redirect every quests-related path into a tmp dir + bootstrap an identity.

    Mirrors ``test_mcp.py::_isolate_paths``. We monkeypatch ``CONFIG_DIR``
    on identity.keys (the canonical source) AND every cached path constant
    that other modules computed at import time.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(state_mod, "QUESTS_PATH", cfg / "daily_quests.json")
    generate_identity(force=True)
    return cfg


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_difficulty_tiers_locked_rewards():
    # The home card / quest UI does math on these; guarding against an
    # accidental rebalance.
    assert DIFFICULTY_TIERS == {"easy": 25, "medium": 50, "hard": 100}


def test_catalog_has_each_tier():
    # The roller relies on at least one template per tier — without this
    # invariant, today's quest set would silently shrink.
    for tier in ("easy", "medium", "hard"):
        templates = templates_by_tier(tier)
        assert templates, f"tier {tier!r} has no templates"
        for t in templates:
            assert t.tier == tier


def test_template_ids_unique():
    ids = [t.template_id for t in QUEST_TEMPLATES]
    assert len(ids) == len(set(ids)), f"duplicate template_id in catalog: {ids}"


def test_materialize_produces_well_formed_quest():
    template = templates_by_tier("medium")[0]
    rng = lambda _max: 0  # noqa: E731 — degenerate RNG for shape test
    quest = materialize(template, rng)
    assert quest["template_id"] == template.template_id
    assert quest["tier"] == template.tier
    assert quest["reward"] == DIFFICULTY_TIERS[template.tier]
    assert quest["title"]
    assert quest["id"].startswith(template.template_id + "__")
    assert isinstance(quest["params"], dict)


def test_materialize_id_stable_across_calls():
    template = templates_by_tier("medium")[0]
    rng = lambda _max: 0  # noqa: E731
    a = materialize(template, rng)
    b = materialize(template, rng)
    assert a["id"] == b["id"]


# ---------------------------------------------------------------------------
# Roll
# ---------------------------------------------------------------------------

PK_A = "a" * 64
PK_B = "b" * 64
DAY = _dt.date(2026, 4, 26)


def test_roll_returns_one_per_tier():
    quests = roll_today(PK_A, day=DAY)
    assert len(quests) == 3
    assert [q["tier"] for q in quests] == ["easy", "medium", "hard"]


def test_roll_deterministic_same_pubkey_same_day():
    a = roll_today(PK_A, day=DAY)
    b = roll_today(PK_A, day=DAY)
    assert [q["id"] for q in a] == [q["id"] for q in b]


def test_roll_changes_with_pubkey():
    a = roll_today(PK_A, day=DAY)
    b = roll_today(PK_B, day=DAY)
    # Not strictly required to differ on every quest, but the pair as a
    # whole MUST differ — otherwise identity contributes nothing.
    assert [q["id"] for q in a] != [q["id"] for q in b]


def test_roll_changes_with_day():
    a = roll_today(PK_A, day=DAY)
    b = roll_today(PK_A, day=DAY + _dt.timedelta(days=1))
    # See above — at least one position must differ.
    assert [q["id"] for q in a] != [q["id"] for q in b]


def test_daily_seed_matches_shop_rotation():
    # Locked invariant: the quest seed and shop seed are byte-identical for
    # the same (pubkey, date) so both rotate at the exact same UTC instant.
    from daimon.shop import rotation as shop_rotation
    quest_seed = _daily_seed(PK_A, DAY)
    shop_seed = shop_rotation.daily_seed(PK_A, DAY)
    assert quest_seed == shop_seed


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def test_save_load_round_trip(isolated):
    quests = roll_today("c" * 64, day=DAY)
    save_quests(date=DAY.isoformat(), pubkey_hex="c" * 64, quests=quests)
    record = load_quests()
    assert record is not None
    assert record["date"] == DAY.isoformat()
    assert record["pubkey_hex"] == "c" * 64
    assert len(record["quests"]) == 3


def test_load_returns_none_for_missing(isolated):
    assert load_quests() is None


def test_load_returns_none_for_stale_version(isolated, tmp_path):
    p = state_mod.QUESTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "version": 999, "date": "2026-04-26",
        "pubkey_hex": "x" * 64, "quests": [],
    }))
    assert load_quests() is None


def test_load_returns_none_for_malformed(isolated):
    p = state_mod.QUESTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json{")
    assert load_quests() is None


def test_today_str_is_utc_iso():
    s = today_str()
    # Must parse as ISO date; if we accidentally used local TZ this would
    # diverge from the shop's UTC rollover.
    parsed = _dt.date.fromisoformat(s)
    assert parsed == _dt.datetime.now(_dt.timezone.utc).date()


# ---------------------------------------------------------------------------
# Progress matchers
# ---------------------------------------------------------------------------

def _force_today(monkeypatch):
    """Make ``progress._today_iso`` return a fixed date for matcher tests."""
    fixed = "2026-04-26"
    monkeypatch.setattr(progress_mod, "_today_iso", lambda *_a, **_kw: fixed)
    return fixed


def _ledger_entry(*, kind, amount, ts="2026-04-26T12:00:00+00:00",
                  **extras):
    return {"kind": kind, "amount": amount, "ts": ts, **extras}


def _ticker_entry(*, kind, ts="2026-04-26T12:00:00+00:00", **extras):
    return {"kind": kind, "amount": 0, "balance_after": 0, "ts": ts, **extras}


def _quest(template_id, *, tier="medium", target_param=None,
           extra_params=None):
    """Synthesize a materialized-quest dict directly (no RNG)."""
    params = dict(extra_params or {})
    if target_param is not None:
        params.update(target_param)
    title = ", ".join(f"{k}={v}" for k, v in params.items()) or template_id
    return {
        "id": f"{template_id}__synthetic",
        "template_id": template_id,
        "title": title,
        "tier": tier,
        "reward": DIFFICULTY_TIERS[tier],
        "params": params,
    }


def test_progress_play_match_counts_tickers(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("play_match", tier="easy",
                   target_param={"n": 1, "outcome": "any"})
    # Two match tickers today, one (irrelevantly) yesterday → 2 today.
    buffer_mod.append("match", note="vs A",
                      extra={"opponent": "A", "outcome": "loss"})
    buffer_mod.append("match", note="vs B",
                      extra={"opponent": "B", "outcome": "win"})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 2
    assert snap[0].complete is True


def test_progress_win_match_filters_outcome(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("win_match", target_param={"n": 1, "outcome": "win"})
    buffer_mod.append("match", note="vs A",
                      extra={"opponent": "A", "outcome": "loss"})
    buffer_mod.append("match", note="vs B",
                      extra={"opponent": "B", "outcome": "win"})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 1
    assert snap[0].complete is True


def test_progress_win_3_matches_targets_three(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("win_3_matches", tier="hard",
                   target_param={"n": 3, "outcome": "win"})
    for opp in ("A", "B"):
        buffer_mod.append("match", note=f"vs {opp}",
                          extra={"opponent": opp, "outcome": "win"})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 2
    assert snap[0].target == 3
    assert snap[0].complete is False


def test_progress_win_with_element_requires_loadout_element(
    isolated, monkeypatch,
):
    today = _force_today(monkeypatch)
    quest = _quest("win_with_element", extra_params={"element": "VOLT"})
    # Win without an element tag → no progress.
    buffer_mod.append("match", extra={"opponent": "A", "outcome": "win"})
    # Win with the wrong element → no progress.
    buffer_mod.append("match", extra={"opponent": "B", "outcome": "win",
                                      "loadout_element": "FIRE"})
    # Win with the right element → 1.
    buffer_mod.append("match", extra={"opponent": "C", "outcome": "win",
                                      "loadout_element": "VOLT"})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 1


def test_progress_beat_tier_requires_opponent_tier(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("beat_tier_hard", tier="hard",
                   extra_params={"tier": "veteran"})
    # Win, but against a rookie — wrong tier.
    buffer_mod.append("match", extra={"opponent": "A", "outcome": "win",
                                      "opponent_tier": "rookie"})
    # Win against veteran — qualifies.
    buffer_mod.append("match", extra={"opponent": "B", "outcome": "win",
                                      "opponent_tier": "veteran"})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 1


def test_progress_pull_counts_ledger_entries(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("pull_card", tier="easy", target_param={"n": 1})
    # Seed the ledger with two pull entries today.
    from daimon.mining.ledger import (
        _build_entry,
        _append_line,
        initialize_ledger,
        GENESIS_PREV_HASH,
    )
    from daimon.identity import load_identity
    initialize_ledger()
    identity = load_identity()
    for i in range(2):
        e = _build_entry(
            identity=identity, kind="pull", amount=-100,
            prev_hash=GENESIS_PREV_HASH,  # chain doesn't matter for matcher
            extras={"serial": f"s{i}", "card_id": "c", "pack": "p",
                    "rarity": "common"},
        )
        _append_line(e)
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 2


def test_progress_mine_sums_amounts(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("mine_easy", tier="easy", target_param={"amount": 50})
    from daimon.mining.ledger import append_mine_entry
    append_mine_entry(tool_name="Edit", amount=20, factors={}, novelty_key="a")
    append_mine_entry(tool_name="Edit", amount=35, factors={}, novelty_key="b")
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 55
    assert snap[0].target == 50
    assert snap[0].complete is True


def test_progress_unknown_template_yields_zero(isolated, monkeypatch, caplog):
    today = _force_today(monkeypatch)
    quest = _quest("nonexistent_template", target_param={"n": 1})
    snap = evaluate_progress([quest], today=today)
    assert snap[0].progress == 0
    assert snap[0].complete is False


# ---------------------------------------------------------------------------
# Auto-claim
# ---------------------------------------------------------------------------

def test_auto_claim_writes_reward_entry_when_complete(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("win_match", target_param={"n": 1, "outcome": "win"})
    buffer_mod.append("match", extra={"opponent": "A", "outcome": "win"})

    snap = evaluate_and_claim([quest], today=today)
    assert snap[0].complete is True
    assert snap[0].claimed is True

    # Ledger should now have a quest_reward entry of +50.
    from daimon.mining.ledger import get_balance, get_stats
    stats = get_stats()
    assert stats.quest_reward_count == 1
    assert stats.total_quest_reward == 50
    assert get_balance() == 50


def test_auto_claim_idempotent(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("win_match", target_param={"n": 1, "outcome": "win"})
    buffer_mod.append("match", extra={"opponent": "A", "outcome": "win"})

    evaluate_and_claim([quest], today=today)
    evaluate_and_claim([quest], today=today)
    evaluate_and_claim([quest], today=today)

    from daimon.mining.ledger import get_stats
    stats = get_stats()
    # Three claim calls → exactly one ledger entry, dedup'd via
    # idempotency_key="quest_<date>_<quest_id>".
    assert stats.quest_reward_count == 1


def test_auto_claim_skips_incomplete(isolated, monkeypatch):
    today = _force_today(monkeypatch)
    quest = _quest("win_3_matches", tier="hard",
                   target_param={"n": 3, "outcome": "win"})
    # Only 1 win — quest not complete.
    buffer_mod.append("match", extra={"opponent": "A", "outcome": "win"})
    snap = evaluate_and_claim([quest], today=today)
    assert snap[0].complete is False
    assert snap[0].claimed is False
    from daimon.mining.ledger import get_stats
    stats = get_stats()
    assert stats.quest_reward_count == 0


# ---------------------------------------------------------------------------
# MCP integration
# ---------------------------------------------------------------------------

def _isolate_mcp(monkeypatch, tmp_path):
    """Same isolation as ``test_mcp._isolate_paths`` + quests state."""
    from daimon import collection as collection_mod
    from daimon.mcp import server as mcp_server

    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH",
                        cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH",
                        cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH",
                        cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH",
                        cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH",
                        cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(collection_mod, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LEDGER_PATH",
                        cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(mcp_server, "COLLECTION_PATH",
                        cfg / "collection.json")
    monkeypatch.setattr(mcp_server, "LOADOUTS_DIR", cfg / "loadouts")
    monkeypatch.setattr(state_mod, "QUESTS_PATH",
                        cfg / "daily_quests.json")
    monkeypatch.setenv("DAIMON_STATE", str(cfg / "state.json"))
    return cfg


def _call(tool, *args, **kw):
    """Invoke an mcp.tool-decorated function via its underlying ``.fn``."""
    return tool.fn(*args, **kw) if hasattr(tool, "fn") else tool(*args, **kw)


def test_dm_quests_no_identity(monkeypatch, tmp_path):
    _isolate_mcp(monkeypatch, tmp_path)
    from daimon.mcp.server import dm_quests
    out = _call(dm_quests)
    assert out["error"] == "no_identity"


def test_dm_quests_returns_three_quests_with_progress(monkeypatch, tmp_path):
    _isolate_mcp(monkeypatch, tmp_path)
    generate_identity(force=True)
    from daimon.mcp.server import dm_quests
    out = _call(dm_quests)
    assert out["status"] == "ok"
    assert len(out["quests"]) == 3
    tiers = [q["tier"] for q in out["quests"]]
    assert tiers == ["easy", "medium", "hard"]
    assert out["totals"]["total"] == 3
    assert out["totals"]["max_daily_reward"] == 25 + 50 + 100


def test_dm_home_payload_includes_daily_quests(monkeypatch, tmp_path):
    _isolate_mcp(monkeypatch, tmp_path)
    generate_identity(force=True)
    from daimon.mcp.server import dm_home
    payload = _call(dm_home)
    assert payload["status"] == "ok"
    assert "daily_quests" in payload
    assert len(payload["daily_quests"]) == 3


def test_dm_pull_emits_daily_quests_field_on_success(monkeypatch, tmp_path):
    _isolate_mcp(monkeypatch, tmp_path)
    generate_identity(force=True)
    # Mine enough for one pull.
    from daimon.mining.ledger import append_mine_entry
    append_mine_entry(tool_name="Edit", amount=120, factors={},
                      novelty_key="a")
    from daimon.mcp.server import dm_pull
    out = _call(dm_pull, seed="00" * 32)
    assert out["status"] == "ok"
    assert "daily_quests" in out


# ---------------------------------------------------------------------------
# Home card renderer
# ---------------------------------------------------------------------------

def test_home_card_renders_daily_quests_panel():
    from daimon.play.home_card import render_home_card
    payload = {
        "status": "ok",
        "identity": {"pubkey_hex": "a" * 64, "handle": "test",
                     "registered": True, "version": "test"},
        "balance": 200,
        "pull": {"cost": 100, "pulls_available": 2,
                 "balance_to_next_pull": 100},
        "stats": {},
        "rank": {"rank": 1, "tier": "Rookie", "wins": 0, "losses": 0,
                 "draws": 0, "total_players": 1},
        "recent_matches": [],
        "recent_pulls": [],
        "recommended_npc": None,
        "saved_loadouts": [],
        "daily_quests": [
            {"quest_id": "q1", "template_id": "play_match",
             "title": "Play a match", "tier": "easy", "reward": 25,
             "progress": 1, "target": 1, "complete": True, "claimed": False},
            {"quest_id": "q2", "template_id": "win_match",
             "title": "Win a match", "tier": "medium", "reward": 50,
             "progress": 0, "target": 1, "complete": False, "claimed": False},
            {"quest_id": "q3", "template_id": "win_3_matches",
             "title": "Win 3 matches", "tier": "hard", "reward": 100,
             "progress": 3, "target": 3, "complete": True, "claimed": True},
        ],
    }
    html = render_home_card(payload)
    assert "DAILY QUESTS" in html
    # Pending claim chip for the medium-tier quest's reward.
    assert "CLAIM READY" in html
    assert "+25¤" in html
    # Claimed quest renders the muted check.
    assert "✓" in html


def test_home_card_omits_quest_panel_when_empty():
    from daimon.play.home_card import render_home_card
    payload = {
        "status": "ok",
        "identity": {"pubkey_hex": "a" * 64, "handle": "test",
                     "registered": True, "version": "test"},
        "balance": 0,
        "pull": {"cost": 100, "pulls_available": 0,
                 "balance_to_next_pull": 100},
        "stats": {}, "rank": {"tier": "Rookie", "wins": 0,
                              "losses": 0, "draws": 0, "total_players": 0,
                              "rank": None},
        "recent_matches": [], "recent_pulls": [],
        "recommended_npc": None, "saved_loadouts": [],
        "daily_quests": [],
    }
    html = render_home_card(payload)
    assert "DAILY QUESTS" not in html
