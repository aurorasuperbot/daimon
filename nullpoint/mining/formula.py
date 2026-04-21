"""Mining formula.

   reward = base(tool) × value_signal × novelty × time_decay × drop_rate

All factors normalized to floats; final reward is `int(round(...))` and
clamped to [0, 100]. 100 currency = 1 gacha pull.

This is V1. Numbers are tunable and will be calibrated against real Claude
Code traces during alpha. Formula is intentionally boring — agents can read
it, but cannot game it without doing real work (value_signal is derived from
externally-observable side effects, not agent self-report).
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Optional

# 100 currency = 1 gacha pull. Hardcoded V1.
PULL_COST = 100

# Base reward per tool invocation. Higher = more "expensive/important" work.
BASE_VALUES = {
    # File / repo work
    "Edit": 4,
    "Write": 4,
    "MultiEdit": 6,
    "NotebookEdit": 4,
    # Reading / search
    "Read": 1,
    "Grep": 1,
    "Glob": 1,
    # Execution
    "Bash": 3,
    # Communication
    "Reply": 0,            # no mining for chat output
    "TodoWrite": 0,        # no mining for bookkeeping
    # Anything we don't recognize gets a neutral default
    "_default": 2,
}

# Global drop-rate tuning. 1.0 = baseline. Lower if economy inflates.
DROP_RATE = 0.5

# Soft cap per single tool call (anti-burst).
MAX_PER_INVOCATION = 100


@dataclass(frozen=True)
class MiningInput:
    tool_name: str
    success: bool                  # did the tool call succeed?
    output_bytes: int              # size of stdout/return value
    elapsed_ms: int                # how long the call took
    novelty_key: str               # hash key for dedup (e.g. file path + content hash)
    seconds_since_last_call: float # for time decay


@dataclass(frozen=True)
class MiningOutput:
    reward: int                    # currency awarded (0..MAX_PER_INVOCATION)
    factors: dict                  # debug breakdown


# ---------------------------------------------------------------------------
# Component functions. Each returns a normalized multiplier.
# ---------------------------------------------------------------------------

def _base(tool_name: str) -> int:
    return BASE_VALUES.get(tool_name, BASE_VALUES["_default"])


def _value_signal(success: bool, output_bytes: int, elapsed_ms: int) -> float:
    """Bounded measure of "did this do something."""
    if not success:
        return 0.1
    # Output size: log-scaled. 0 bytes → 0.5, 1KB → 1.0, 10KB → 1.5
    out_factor = 0.5 + min(1.5, math.log10(max(1, output_bytes)) / 2.0)
    # Elapsed time: very fast (cache hit) gets less, very slow (real work) more
    elapsed_factor = min(1.5, max(0.5, math.log10(max(10, elapsed_ms)) / 3.0 + 0.3))
    return out_factor * elapsed_factor


_NOVELTY_MEMORY: dict[str, int] = {}  # in-process; persist externally for real use


def _novelty(novelty_key: str) -> float:
    """Repeat work pays diminishing returns.

    First time a novelty_key is seen → 1.0
    Second time → 0.5
    Nth time → 1/N (capped at 0.05)
    """
    seen = _NOVELTY_MEMORY.get(novelty_key, 0)
    _NOVELTY_MEMORY[novelty_key] = seen + 1
    return max(0.05, 1.0 / (seen + 1))


def _time_decay(seconds_since_last_call: float) -> float:
    """Bursty calling pays less than steady work.

    < 1s   → 0.3
    1-5s   → 0.7
    5-60s  → 1.0
    > 60s  → 1.0 (no bonus for idle)
    """
    s = max(0.0, seconds_since_last_call)
    if s < 1:
        return 0.3
    if s < 5:
        return 0.7
    return 1.0


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def compute_reward(inp: MiningInput) -> MiningOutput:
    base = _base(inp.tool_name)
    value = _value_signal(inp.success, inp.output_bytes, inp.elapsed_ms)
    novelty = _novelty(inp.novelty_key)
    decay = _time_decay(inp.seconds_since_last_call)

    raw = base * value * novelty * decay * DROP_RATE
    reward = int(round(raw))
    reward = max(0, min(MAX_PER_INVOCATION, reward))

    return MiningOutput(
        reward=reward,
        factors={
            "base": base,
            "value_signal": round(value, 3),
            "novelty": round(novelty, 3),
            "time_decay": round(decay, 3),
            "drop_rate": DROP_RATE,
            "raw": round(raw, 3),
        },
    )


def make_novelty_key(tool_name: str, *parts: str) -> str:
    """Stable hash for novelty deduplication."""
    h = hashlib.sha256()
    h.update(tool_name.encode("utf-8"))
    for p in parts:
        h.update(b"|")
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:16]
