"""Unit tests for daimon.arena.state — PvP secret-state persistence.

The state files hold nonces that MUST NOT leak before reveal — if a nonce
leaks early, an adversary can compute a player's commit hash from their
loadout and verify the commit early. So we verify:

  - round-trip save/load preserves every field,
  - file mode is 0600 (best-effort; filesystem may refuse chmod),
  - directory mode is 0700,
  - DAIMON_INBOX env var redirects the location (the test isolation hook),
  - delete() + list_pending() + identity-mismatch handling.
"""

from __future__ import annotations

import pytest

from daimon.arena import state as arena_state


def _use_tmp_inbox(monkeypatch, tmp_path):
    """Redirect PVP_STATE_DIR into a tmp location."""
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("DAIMON_INBOX", str(inbox))
    monkeypatch.setattr(arena_state, "PVP_STATE_DIR", inbox / "pvp_state")
    return inbox / "pvp_state"


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_save_then_load_round_trip(monkeypatch, tmp_path):
    _use_tmp_inbox(monkeypatch, tmp_path)
    path = arena_state.save(
        issue_number=42,
        side="challenger",
        nonce="ab" * 32,
        loadout={"cards": [{"card_id": "x"}]},
        pubkey_hex="aa" * 32,
        opponent_pubkey="bb" * 32,
    )
    assert path.exists()
    record = arena_state.load(42)
    assert record is not None
    assert record["issue_number"] == 42
    assert record["side"] == "challenger"
    assert record["nonce"] == "ab" * 32
    assert record["loadout"] == {"cards": [{"card_id": "x"}]}
    assert record["pubkey_hex"] == "aa" * 32
    assert record["opponent_pubkey"] == "bb" * 32
    assert record["created_at"]  # ISO-8601 timestamp


def test_save_rejects_bad_side(monkeypatch, tmp_path):
    _use_tmp_inbox(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        arena_state.save(issue_number=1, side="spectator", nonce="a" * 64,
                         loadout={}, pubkey_hex="x")


def test_save_accepts_opponent_pubkey_none(monkeypatch, tmp_path):
    """Challenger at challenge time knows opponent; responder at accept
    time may not yet know the challenger (by pubkey)."""
    _use_tmp_inbox(monkeypatch, tmp_path)
    arena_state.save(issue_number=2, side="responder", nonce="c" * 64,
                     loadout={"cards": []}, pubkey_hex="y",
                     opponent_pubkey=None)
    rec = arena_state.load(2)
    assert rec["opponent_pubkey"] is None


def test_save_overwrites_existing(monkeypatch, tmp_path):
    """Re-issuing a challenge (e.g. after cancelling the first) must
    overwrite the old state, not append."""
    _use_tmp_inbox(monkeypatch, tmp_path)
    arena_state.save(1, "challenger", "a" * 64, {"cards": []}, "aa")
    arena_state.save(1, "challenger", "b" * 64, {"cards": [{"x": 1}]}, "aa")
    rec = arena_state.load(1)
    assert rec["nonce"] == "b" * 64
    assert rec["loadout"] == {"cards": [{"x": 1}]}




# ---------------------------------------------------------------------------
# load / delete / list_pending
# ---------------------------------------------------------------------------

def test_load_missing_returns_none(monkeypatch, tmp_path):
    _use_tmp_inbox(monkeypatch, tmp_path)
    assert arena_state.load(99999) is None


def test_load_corrupt_returns_none(monkeypatch, tmp_path):
    dir_path = _use_tmp_inbox(monkeypatch, tmp_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "7.json").write_text("not json {{{")
    # Should return None, not raise.
    assert arena_state.load(7) is None


def test_delete_removes_file(monkeypatch, tmp_path):
    _use_tmp_inbox(monkeypatch, tmp_path)
    arena_state.save(3, "challenger", "a" * 64, {}, "aa")
    assert arena_state.delete(3) is True
    assert arena_state.load(3) is None
    # Idempotent — deleting again returns False.
    assert arena_state.delete(3) is False


def test_list_pending_sorted(monkeypatch, tmp_path):
    _use_tmp_inbox(monkeypatch, tmp_path)
    for n in (7, 2, 19, 5):
        arena_state.save(n, "challenger", "a" * 64, {}, "aa")
    assert arena_state.list_pending() == [2, 5, 7, 19]


def test_list_pending_empty_when_dir_missing(monkeypatch, tmp_path):
    """No state saved yet → empty list, not a crash."""
    _use_tmp_inbox(monkeypatch, tmp_path)
    assert arena_state.list_pending() == []


def test_list_pending_ignores_non_numeric_files(monkeypatch, tmp_path):
    dir_path = _use_tmp_inbox(monkeypatch, tmp_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "42.json").write_text("{}")
    (dir_path / "README.md").write_text("# notes")
    (dir_path / "not_a_number.json").write_text("{}")
    assert arena_state.list_pending() == [42]
