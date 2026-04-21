"""Seeded RNG determinism tests."""

import pytest

from nullpoint.engine.rng import SeededRng


def test_same_seed_same_sequence():
    a = SeededRng(b"\x00" * 32)
    b = SeededRng(b"\x00" * 32)
    for _ in range(100):
        assert a.randrange(1000) == b.randrange(1000)


def test_different_seed_different_sequence():
    a = SeededRng(b"\x00" * 32)
    b = SeededRng(b"\x01" * 32)
    diffs = sum(1 for _ in range(50) if a.randrange(1000) != b.randrange(1000))
    # Effectively always all 50 differ
    assert diffs > 40


def test_choice_works():
    rng = SeededRng(b"\x00" * 32)
    seq = ["a", "b", "c", "d"]
    picks = [rng.choice(seq) for _ in range(20)]
    assert all(p in seq for p in picks)


def test_choice_empty_raises():
    rng = SeededRng(b"\x00" * 32)
    with pytest.raises(ValueError):
        rng.choice([])


def test_seed_size_validation():
    with pytest.raises(ValueError):
        SeededRng(b"too short")
    with pytest.raises(ValueError):
        SeededRng("string")  # type: ignore


def test_randrange_zero_raises():
    rng = SeededRng(b"\x00" * 32)
    with pytest.raises(ValueError):
        rng.randrange(0)
