"""Deterministic RNG seeded from a 32-byte match seed.

Uses SHA-256 of (seed || counter) to produce a stream of integers.
Pure-python, no dependency on Python's random module (which has process-state).
"""

from __future__ import annotations

import hashlib


class SeededRng:
    """Deterministic, replay-safe integer RNG.

    Two SeededRng(seed) instances always produce the same sequence,
    regardless of process, OS, or Python version.
    """

    def __init__(self, seed: bytes) -> None:
        if not isinstance(seed, bytes) or len(seed) != 32:
            raise ValueError("seed must be exactly 32 bytes")
        self._seed = seed
        self._counter = 0

    def _next_block(self) -> bytes:
        h = hashlib.sha256()
        h.update(self._seed)
        h.update(self._counter.to_bytes(8, "big"))
        self._counter += 1
        return h.digest()

    def randrange(self, n: int) -> int:
        """Return integer in [0, n)."""
        if n <= 0:
            raise ValueError("n must be positive")
        block = self._next_block()
        # Use first 8 bytes as a uint64; modulo bias is acceptable for n < 2^32
        as_int = int.from_bytes(block[:8], "big")
        return as_int % n

    def choice(self, seq: list) -> object:
        if not seq:
            raise ValueError("cannot choose from empty sequence")
        return seq[self.randrange(len(seq))]
