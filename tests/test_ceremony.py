"""Tier-up ceremony test surface.

Covers:
  * State persistence: atomic load/save round-trip; load returns None on
    stale/corrupt/version-mismatched files.
  * Reward schedule: locked at canonical values (100/250/500/1000).
  * Pending detection: fires on every tier crossing, never on Rookie,
    never on a tier already claimed.
  * Multi-tier jump: a Rookie → Veteran climb produces ``["Novice",
    "Veteran"]`` to mint, total reward = 350.
  * Idempotency at the ledger: per-tier idempotency_key prevents
    double-mint even on partial-write replay.
  * Monotonicity: claimed_tier never decrements even if leaderboard
    wins drop after the claim.
  * Audit cross-check: claim_history matches ledger entries.
  * MCP wiring: dm_tier_up_claim returns ok/noop envelopes; dm_home
    surfaces ``tier_ceremony`` field; dm_home is read-only (calling it
    does NOT mint).
  * Home-card renderer: the banner renders crest + reward + multi-tier
    sub-line without raising; missing payload renders nothing.

Uses the same path-isolation pattern as ``test_quests.py::isolated``.
"""

from __future__ import annotations

import json

import pytest

from daimon.ceremony import (
    PendingCeremony,
    REWARD_SCHEDULE,
    TIER_ORDER,
    audit_state_against_ledger,
    claim_pending,
    pending_ceremony,
    tier_index,
)
from daimon.ceremony import state as ceremony_state
from daimon.identity import generate_identity, load_identity
from daimon.identity import keys as identity_keys
from daimon.mining import buffer as buffer_mod
from daimon.mining import ledger as ledger_mod
from daimon.play import home_card as home_card_mod


# ---------------------------------------------------------------------------
# Shared isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Redirect every ceremony-related path into a tmp dir + bootstrap an identity.

    Mirrors ``tests/test_quests.py::isolated``. We monkeypatch
    ``CONFIG_DIR`` on identity.keys (the canonical source) AND every
    cached path constant that other modules computed at import time.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(
        ceremony_state, "CEREMONY_PATH", cfg / "tier_progress.json",
    )
    generate_identity(force=True)
    return cfg


def _rank_payload(wins: int, *, status: str = "ok") -> dict:
    """Synthesize a my_rank-shaped envelope without hitting the network."""
    return {
        "status": status,
        "pubkey_hex": "deadbeef" * 8,
        "rank": 1 if wins > 0 else None,
        "tier": (
            "Champion" if wins >= 50
            else "Elite" if wins >= 25
            else "Veteran" if wins >= 10
            else "Novice" if wins >= 3
            else "Rookie"
        ),
        "wins": wins,
        "losses": 0,
        "draws": 0,
        "total_players": 1,
    }


# ---------------------------------------------------------------------------
# Reward schedule (locked-canonical)
# ---------------------------------------------------------------------------

def test_reward_schedule_locked():
    # Home card / docs / chat replies all reference these. Guarding
    # against an accidental balance change.
    assert REWARD_SCHEDULE == {
        "Rookie": 0,
        "Novice": 100,
        "Veteran": 250,
        "Elite": 500,
        "Champion": 1000,
    }


def test_tier_order_locked():
    assert TIER_ORDER == ("Rookie", "Novice", "Veteran", "Elite", "Champion")


def test_tier_index():
    assert tier_index("Rookie") == 0
    assert tier_index("Novice") == 1
    assert tier_index("Veteran") == 2
    assert tier_index("Elite") == 3
    assert tier_index("Champion") == 4
    assert tier_index("garbage") == -1


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def test_state_round_trip(isolated):
    history = [
        {"tier": "Novice", "claimed_at": "2026-04-27T12:00:00+00:00",
         "reward": 100, "wins_at_claim": 3,
         "ledger_entry_hash": "abc"},
    ]
    saved = ceremony_state.save_state(
        pubkey_hex="ff" * 32,
        claimed_tier="Novice",
        claim_history=history,
    )
    assert saved["claimed_tier"] == "Novice"

    loaded = ceremony_state.load_state()
    assert loaded is not None
    assert loaded["claimed_tier"] == "Novice"
    assert loaded["claim_history"] == history


def test_state_returns_none_on_missing_file(isolated):
    assert ceremony_state.load_state() is None


def test_state_returns_none_on_corrupt_json(isolated):
    ceremony_state.CEREMONY_PATH.write_text("{not json")
    assert ceremony_state.load_state() is None


def test_state_returns_none_on_version_mismatch(isolated):
    ceremony_state.CEREMONY_PATH.write_text(json.dumps({
        "version": 9999, "pubkey_hex": "x", "claimed_tier": "Rookie",
        "claim_history": [],
    }))
    assert ceremony_state.load_state() is None


def test_state_returns_none_on_malformed_history_entry(isolated):
    ceremony_state.CEREMONY_PATH.write_text(json.dumps({
        "version": ceremony_state.SCHEMA_VERSION,
        "pubkey_hex": "x",
        "claimed_tier": "Novice",
        "claim_history": [{"tier": "Novice"}],  # missing fields
    }))
    assert ceremony_state.load_state() is None


# ---------------------------------------------------------------------------
# Pending detection
# ---------------------------------------------------------------------------

def test_pending_returns_none_at_zero_wins(isolated):
    pending = pending_ceremony(rank_override=_rank_payload(0))
    assert pending is None


def test_pending_returns_none_when_arena_unreachable(isolated):
    pending = pending_ceremony(
        rank_override={"error": "arena_unreachable", "message": "x"},
    )
    assert pending is None


def test_pending_returns_none_at_two_wins(isolated):
    # 2 wins → still Rookie. No ceremony.
    pending = pending_ceremony(rank_override=_rank_payload(2))
    assert pending is None


def test_pending_fires_at_novice_threshold(isolated):
    pending = pending_ceremony(rank_override=_rank_payload(3))
    assert pending is not None
    assert pending.pending_tier == "Novice"
    assert pending.prev_tier == "Rookie"
    assert pending.tiers_to_mint == ("Novice",)
    assert pending.reward_total == 100
    assert pending.wins_at_check == 3


def test_pending_fires_at_veteran_threshold(isolated):
    pending = pending_ceremony(rank_override=_rank_payload(10))
    assert pending is not None
    # Multi-tier jump from Rookie: should mint BOTH Novice + Veteran.
    assert pending.pending_tier == "Veteran"
    assert pending.tiers_to_mint == ("Novice", "Veteran")
    assert pending.reward_total == 100 + 250


def test_pending_fires_full_climb_to_champion(isolated):
    pending = pending_ceremony(rank_override=_rank_payload(50))
    assert pending is not None
    assert pending.pending_tier == "Champion"
    assert pending.tiers_to_mint == ("Novice", "Veteran", "Elite", "Champion")
    assert pending.reward_total == 100 + 250 + 500 + 1000  # 1850


def test_pending_returns_none_when_already_claimed(isolated):
    # Claim Novice, then check at the same threshold — should be no-op.
    claim_pending(rank_override=_rank_payload(3))
    pending = pending_ceremony(rank_override=_rank_payload(3))
    assert pending is None


def test_pending_fires_on_post_claim_promotion(isolated):
    # Claim Novice, then check at Veteran threshold — should fire for
    # exactly Veteran (Novice already claimed).
    claim_pending(rank_override=_rank_payload(3))
    pending = pending_ceremony(rank_override=_rank_payload(10))
    assert pending is not None
    assert pending.pending_tier == "Veteran"
    assert pending.tiers_to_mint == ("Veteran",)
    assert pending.reward_total == 250


def test_pending_to_dict_serializable():
    p = PendingCeremony(
        pending_tier="Veteran",
        prev_tier="Rookie",
        tiers_to_mint=("Novice", "Veteran"),
        reward_total=350,
        wins_at_check=10,
    )
    d = p.to_dict()
    assert d["pending_tier"] == "Veteran"
    assert d["tiers_to_mint"] == ["Novice", "Veteran"]
    assert json.dumps(d)  # round-trips


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def test_claim_noop_when_no_pending(isolated):
    res = claim_pending(rank_override=_rank_payload(0))
    assert res["status"] == "noop"
    assert res["claimed_tier"] == "Rookie"


def test_claim_noop_persists_state_file(isolated):
    # On first noop, the state file should land on disk so audits
    # always have something to read.
    assert not ceremony_state.CEREMONY_PATH.exists()
    claim_pending(rank_override=_rank_payload(0))
    assert ceremony_state.CEREMONY_PATH.exists()


def test_claim_single_tier(isolated):
    res = claim_pending(rank_override=_rank_payload(3))
    assert res["status"] == "ok"
    assert res["prev_tier"] == "Rookie"
    assert res["claimed_tier"] == "Novice"
    assert res["claimed_tiers"] == ["Novice"]
    assert res["reward_total"] == 100
    assert res["balance"] == 100  # no other ledger activity in test


def test_claim_multi_tier_jump(isolated):
    res = claim_pending(rank_override=_rank_payload(10))
    assert res["status"] == "ok"
    assert res["claimed_tier"] == "Veteran"
    assert res["claimed_tiers"] == ["Novice", "Veteran"]
    assert res["reward_total"] == 350
    # Two distinct ledger entries should exist.
    entries = ledger_mod._read_entries()
    tier_up_entries = [e for e in entries if e.get("kind") == "tier_up_reward"]
    assert len(tier_up_entries) == 2
    assert {e["tier"] for e in tier_up_entries} == {"Novice", "Veteran"}


def test_claim_full_climb_to_champion(isolated):
    res = claim_pending(rank_override=_rank_payload(50))
    assert res["status"] == "ok"
    assert res["claimed_tier"] == "Champion"
    assert res["reward_total"] == 1850
    assert ledger_mod.get_balance() == 1850


def test_claim_idempotent(isolated):
    first = claim_pending(rank_override=_rank_payload(10))
    assert first["reward_total"] == 350
    # Second call at the same wins → noop, balance unchanged.
    second = claim_pending(rank_override=_rank_payload(10))
    assert second["status"] == "noop"
    assert ledger_mod.get_balance() == 350
    # Ledger still has exactly 2 tier_up entries.
    entries = ledger_mod._read_entries()
    tier_up_entries = [e for e in entries if e.get("kind") == "tier_up_reward"]
    assert len(tier_up_entries) == 2


def test_claim_idempotent_against_state_file_drop(isolated):
    """If the state file is deleted but the ledger is intact, re-claiming
    should NOT double-mint — the per-tier idempotency_key on the ledger
    is the ultimate safety net."""
    claim_pending(rank_override=_rank_payload(3))
    # Wipe the state file (simulates corruption / hand-edit).
    ceremony_state.CEREMONY_PATH.unlink()
    # Re-claim — ledger should still dedup.
    res = claim_pending(rank_override=_rank_payload(3))
    assert res["status"] == "ok"  # state file says "Rookie" again so it tries
    assert res["reward_total"] == 0  # ...but ledger refuses the dupe
    # Ledger has exactly 1 tier_up entry, balance still 100.
    entries = ledger_mod._read_entries()
    tier_up_entries = [e for e in entries if e.get("kind") == "tier_up_reward"]
    assert len(tier_up_entries) == 1
    assert ledger_mod.get_balance() == 100


def test_claim_monotonic_after_wins_drop(isolated):
    # Claim Veteran, then simulate a wins-drop (e.g. dispute revoked
    # a win → wins back to 5, which is still ≥3 = Novice). The
    # claimed_tier should NOT decrement.
    claim_pending(rank_override=_rank_payload(10))
    # Now wins is back to 5 — still Novice tier.
    res = claim_pending(rank_override=_rank_payload(5))
    assert res["status"] == "noop"
    assert res["claimed_tier"] == "Veteran"  # monotonic — held high-water


def test_claim_history_records_each_tier(isolated):
    claim_pending(rank_override=_rank_payload(25))
    record = ceremony_state.load_state()
    assert record is not None
    history = record["claim_history"]
    assert len(history) == 3  # Novice + Veteran + Elite
    assert [h["tier"] for h in history] == ["Novice", "Veteran", "Elite"]
    # Each history entry must carry a ledger_entry_hash for audit.
    for h in history:
        assert h["ledger_entry_hash"]
        assert isinstance(h["wins_at_claim"], int)


def test_claim_no_identity_returns_envelope(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(identity_keys, "CONFIG_DIR", cfg)
    monkeypatch.setattr(identity_keys, "PRIVATE_KEY_PATH", cfg / "identity.key")
    monkeypatch.setattr(identity_keys, "PUBLIC_KEY_PATH", cfg / "identity.pub")
    monkeypatch.setattr(identity_keys, "METADATA_PATH", cfg / "identity.json")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", cfg / "mining_ledger.jsonl")
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", cfg / "mine_buffer.jsonl")
    monkeypatch.setattr(
        ceremony_state, "CEREMONY_PATH", cfg / "tier_progress.json",
    )
    # No generate_identity call — identity file doesn't exist.
    res = claim_pending(rank_override=_rank_payload(3))
    assert res.get("error") == "no_identity"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_audit_clean_after_claim(isolated):
    claim_pending(rank_override=_rank_payload(10))
    audit = audit_state_against_ledger()
    assert audit["ok"] is True
    assert audit["claims"] == 2
    assert audit["ledger_tier_up_entries"] == 2


def test_audit_detects_missing_ledger_entry(isolated):
    # Manually write a history entry with no corresponding ledger row.
    identity = load_identity()
    ceremony_state.save_state(
        pubkey_hex=identity.pubkey_hex,
        claimed_tier="Novice",
        claim_history=[{
            "tier": "Novice",
            "claimed_at": "2026-04-27T00:00:00+00:00",
            "reward": 100,
            "wins_at_claim": 3,
            "ledger_entry_hash": "fake",
        }],
    )
    audit = audit_state_against_ledger()
    assert audit["ok"] is False
    assert any(m["tier"] == "Novice" for m in audit["missing"])


# ---------------------------------------------------------------------------
# Ledger extension (LedgerStats fields)
# ---------------------------------------------------------------------------

def test_ledger_stats_tracks_tier_up_rewards(isolated):
    claim_pending(rank_override=_rank_payload(25))
    stats = ledger_mod.get_stats()
    assert stats.tier_up_reward_count == 3
    assert stats.total_tier_up_reward == 100 + 250 + 500


def test_ledger_verify_passes_after_tier_up_claim(isolated):
    claim_pending(rank_override=_rank_payload(50))
    v = ledger_mod.verify_ledger()
    assert v["ok"] is True
    assert v["balance"] == 1850


# ---------------------------------------------------------------------------
# MCP wiring (dm_home + dm_tier_up_claim)
# ---------------------------------------------------------------------------

def _patch_my_rank(monkeypatch, rank_payload: dict):
    """Stub arena_ops.my_rank everywhere it's used (ops + ceremony.tier_up).

    ceremony.tier_up imports arena_ops, but dm_home calls arena_ops.my_rank
    via the ``arena_ops`` symbol bound in mcp.server. Patch both to
    keep test state isolated from the real arena.
    """
    from daimon.mcp import server as mcp_server
    monkeypatch.setattr(mcp_server.arena_ops, "my_rank",
                        lambda: dict(rank_payload))


def test_dm_home_surfaces_pending_ceremony(isolated, monkeypatch):
    _patch_my_rank(monkeypatch, _rank_payload(10))
    from daimon.mcp.server import dm_home
    payload = dm_home.fn() if hasattr(dm_home, "fn") else dm_home()
    assert payload.get("status") == "ok"
    assert payload.get("tier_ceremony") is not None
    cer = payload["tier_ceremony"]
    assert cer["pending_tier"] == "Veteran"
    assert cer["tiers_to_mint"] == ["Novice", "Veteran"]
    assert cer["reward_total"] == 350


def test_dm_home_is_read_only(isolated, monkeypatch):
    """Calling dm_home must NOT mint a tier_up reward."""
    _patch_my_rank(monkeypatch, _rank_payload(10))
    from daimon.mcp.server import dm_home
    bal_before = ledger_mod.get_balance()
    _ = dm_home.fn() if hasattr(dm_home, "fn") else dm_home()
    bal_after = ledger_mod.get_balance()
    assert bal_before == bal_after  # 0 == 0; no side effect


def test_dm_tier_up_claim_returns_ok(isolated, monkeypatch):
    _patch_my_rank(monkeypatch, _rank_payload(10))
    from daimon.mcp.server import dm_tier_up_claim
    res = (dm_tier_up_claim.fn()
           if hasattr(dm_tier_up_claim, "fn") else dm_tier_up_claim())
    assert res["status"] == "ok"
    assert res["claimed_tier"] == "Veteran"
    assert res["reward_total"] == 350


def test_dm_tier_up_claim_returns_noop(isolated, monkeypatch):
    _patch_my_rank(monkeypatch, _rank_payload(0))
    from daimon.mcp.server import dm_tier_up_claim
    res = (dm_tier_up_claim.fn()
           if hasattr(dm_tier_up_claim, "fn") else dm_tier_up_claim())
    assert res["status"] == "noop"


def test_dm_home_clears_ceremony_field_after_claim(isolated, monkeypatch):
    _patch_my_rank(monkeypatch, _rank_payload(3))
    from daimon.mcp.server import dm_home, dm_tier_up_claim
    # Claim, then re-check dm_home — tier_ceremony should be null now.
    _ = (dm_tier_up_claim.fn()
         if hasattr(dm_tier_up_claim, "fn") else dm_tier_up_claim())
    payload = dm_home.fn() if hasattr(dm_home, "fn") else dm_home()
    assert payload["tier_ceremony"] is None


# ---------------------------------------------------------------------------
# Home-card renderer
# ---------------------------------------------------------------------------

def test_render_tier_ceremony_returns_empty_on_none():
    assert home_card_mod._render_tier_ceremony(None) == ""
    assert home_card_mod._render_tier_ceremony({}) == ""


def test_render_tier_ceremony_renders_single_tier():
    payload = {
        "pending_tier": "Novice",
        "prev_tier": "Rookie",
        "tiers_to_mint": ["Novice"],
        "reward_total": 100,
        "wins_at_check": 3,
    }
    html = home_card_mod._render_tier_ceremony(payload)
    assert "TIER UP" in html
    assert "Novice" in html
    assert "+100¤" in html
    assert "promoted from Rookie" in html
    # Single tier should NOT include the "includes ..." sub-line.
    assert "includes" not in html


def test_render_tier_ceremony_renders_multi_tier_jump():
    payload = {
        "pending_tier": "Veteran",
        "prev_tier": "Rookie",
        "tiers_to_mint": ["Novice", "Veteran"],
        "reward_total": 350,
        "wins_at_check": 10,
    }
    html = home_card_mod._render_tier_ceremony(payload)
    assert "Veteran" in html
    assert "+350¤" in html
    assert "includes Novice + Veteran" in html


def test_render_tier_ceremony_renders_three_tier_jump():
    payload = {
        "pending_tier": "Elite",
        "prev_tier": "Rookie",
        "tiers_to_mint": ["Novice", "Veteran", "Elite"],
        "reward_total": 850,
        "wins_at_check": 25,
    }
    html = home_card_mod._render_tier_ceremony(payload)
    assert "+850¤" in html
    # 3+ tiers fall through to comma-join with " + " before the last one.
    assert "Novice, Veteran + Elite" in html


def test_render_tier_ceremony_drops_malformed_payload():
    # Missing pending_tier → drop silently.
    assert home_card_mod._render_tier_ceremony(
        {"reward_total": 100}
    ) == ""
    # Zero reward → drop silently.
    assert home_card_mod._render_tier_ceremony({
        "pending_tier": "Novice",
        "prev_tier": "Rookie",
        "tiers_to_mint": ["Novice"],
        "reward_total": 0,
    }) == ""


def test_render_home_card_includes_ceremony_above_play_cta():
    """Banner should appear in the rendered HTML, positioned before the
    PLAY VS button so a tier crossing is the first thing the player
    sees on next open."""
    payload = {
        "status": "ok",
        "identity": {"pubkey_hex": "ab" * 32, "handle": "tester"},
        "balance": 100,
        "pull": {"cost": 100, "pulls_available": 1, "balance_to_next_pull": 100},
        "rank": {"tier": "Veteran", "rank": 5, "wins": 10, "losses": 2,
                 "draws": 0, "total_players": 50},
        "recent_matches": [],
        "recent_pulls": [],
        "recommended_npc": {
            "npc_id": "x", "name": "Sparring Sam", "tier": "Rookie",
            "rank": 1, "flavor": "warm-up", "reason": "test",
        },
        "saved_loadouts": [],
        "daily_quests": [],
        "tier_ceremony": {
            "pending_tier": "Veteran", "prev_tier": "Rookie",
            "tiers_to_mint": ["Novice", "Veteran"],
            "reward_total": 350, "wins_at_check": 10,
        },
    }
    html = home_card_mod.render_home_card(payload)
    # Both must appear.
    assert "TIER UP" in html
    assert "PLAY VS" in html
    # And TIER UP must come BEFORE PLAY VS in the document order.
    assert html.index("TIER UP") < html.index("PLAY VS")
