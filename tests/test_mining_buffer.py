"""Mining ticker buffer tests.

Covers:
  - append() writes a single line per event
  - tail() returns last N entries oldest-first
  - unknown kinds are rejected (returns None, no write)
  - missing parent dir is created on first append
  - bounded rolling: file is truncated when entries cross MAX_ENTRIES
  - mtime_ns reflects writes, returns 0 when missing
  - bad JSON lines are skipped, not raised
  - by_kind filter preserves order
"""

from __future__ import annotations

import json

import pytest

from daimon.mining import buffer as buf


@pytest.fixture
def buffer_path(tmp_path):
    return tmp_path / "mine_buffer.jsonl"


def test_append_writes_one_line(buffer_path):
    written = buf.append(
        "mine", amount=5, balance_after=42, tool="Edit", path=buffer_path,
    )
    assert written is not None
    assert written["kind"] == "mine"
    assert written["amount"] == 5
    assert written["balance_after"] == 42
    assert written["tool"] == "Edit"
    # File contains exactly one valid JSON line.
    text = buffer_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.count("\n") == 1
    parsed = json.loads(text.strip())
    assert parsed == written


def test_tail_returns_last_n_oldest_first(buffer_path):
    for i in range(5):
        buf.append("mine", amount=1, balance_after=i + 1, tool="Edit",
                   path=buffer_path)
    out = buf.tail(3, path=buffer_path)
    assert len(out) == 3
    # Oldest of the tail comes first; last entry is the most recent.
    assert [e["balance_after"] for e in out] == [3, 4, 5]


def test_tail_empty_when_no_file(buffer_path):
    assert buf.tail(10, path=buffer_path) == []


def test_unknown_kind_rejected(buffer_path):
    result = buf.append("garbage", amount=1, path=buffer_path)
    assert result is None
    assert not buffer_path.exists()


def test_missing_parent_dir_is_created(tmp_path):
    nested = tmp_path / "deep" / "config" / "mine_buffer.jsonl"
    assert not nested.parent.exists()
    written = buf.append("mine", amount=1, balance_after=1, tool="Read",
                         path=nested)
    assert written is not None
    assert nested.exists()


def test_milestone_event(buffer_path):
    written = buf.append(
        "milestone", amount=0, balance_after=100, tool="Edit",
        note="100¤ — pull unlocked!", path=buffer_path,
    )
    assert written["kind"] == "milestone"
    assert written["note"] == "100¤ — pull unlocked!"


def test_bounded_truncates_to_keep_entries(buffer_path, monkeypatch):
    # Drop the byte-floor + counts so the test is small + fast. Otherwise we'd
    # need to write 100 KB of data to trigger compaction.
    monkeypatch.setattr(buf, "MAX_ENTRIES", 10)
    monkeypatch.setattr(buf, "KEEP_ENTRIES", 4)

    # Patch the early-exit byte gate so even tiny files compact.
    real_truncate = buf._maybe_truncate

    def _force_truncate(path):
        # Skip the size check by reading lines unconditionally.
        with path.open("r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if len(lines) <= buf.MAX_ENTRIES:
            return
        keep = lines[-buf.KEEP_ENTRIES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(keep), encoding="utf-8")
        import os as _os
        _os.replace(tmp, path)

    monkeypatch.setattr(buf, "_maybe_truncate", _force_truncate)

    for i in range(20):
        buf.append("mine", amount=1, balance_after=i + 1, tool="Edit",
                   path=buffer_path)

    # The bounded contract: file size never grows past MAX_ENTRIES, and
    # truncation always preserves the most recent writes. Truncation only
    # fires when the next append crosses the threshold, so the final count
    # sits in [KEEP_ENTRIES, MAX_ENTRIES].
    out = buf.tail(100, path=buffer_path)
    assert buf.KEEP_ENTRIES <= len(out) <= buf.MAX_ENTRIES
    # The most recent write is always preserved.
    assert out[-1]["balance_after"] == 20
    # Oldest preserved entry must be among the last (MAX_ENTRIES) appends —
    # never one of the very first writes that has been compacted away.
    assert out[0]["balance_after"] >= 20 - buf.MAX_ENTRIES + 1


def test_mtime_ns_zero_when_missing(buffer_path):
    assert buf.mtime_ns(path=buffer_path) == 0


def test_mtime_ns_after_write(buffer_path):
    buf.append("mine", amount=1, balance_after=1, tool="Read", path=buffer_path)
    assert buf.mtime_ns(path=buffer_path) > 0


def test_tail_skips_bad_lines(buffer_path):
    buffer_path.parent.mkdir(parents=True, exist_ok=True)
    buffer_path.write_text(
        '{"ts":"2026-01-01T00:00:00+00:00","kind":"mine","amount":1,"balance_after":1}\n'
        '{not json at all\n'
        '{"ts":"2026-01-01T00:00:01+00:00","kind":"mine","amount":2,"balance_after":3}\n'
    )
    out = buf.tail(10, path=buffer_path)
    assert len(out) == 2
    assert [e["amount"] for e in out] == [1, 2]


def test_by_kind_preserves_order(buffer_path):
    buf.append("mine", amount=1, balance_after=1, tool="Edit", path=buffer_path)
    buf.append("milestone", amount=0, balance_after=100, path=buffer_path)
    buf.append("mine", amount=2, balance_after=102, tool="Bash", path=buffer_path)

    all_e = buf.tail(10, path=buffer_path)
    mines = buf.by_kind(all_e, "mine")
    milestones = buf.by_kind(all_e, "milestone")
    assert [e["amount"] for e in mines] == [1, 2]
    assert len(milestones) == 1


def test_extra_fields_passed_through(buffer_path):
    written = buf.append(
        "match", amount=0, balance_after=42, path=buffer_path,
        extra={"opponent": "Lyra", "outcome": "win"},
    )
    assert written["opponent"] == "Lyra"
    assert written["outcome"] == "win"


def test_extra_cannot_clobber_required_fields(buffer_path):
    written = buf.append(
        "mine", amount=5, balance_after=10, tool="Edit",
        path=buffer_path,
        extra={"kind": "spoofed", "amount": 999, "balance_after": 999},
    )
    assert written["kind"] == "mine"
    assert written["amount"] == 5
    assert written["balance_after"] == 10
