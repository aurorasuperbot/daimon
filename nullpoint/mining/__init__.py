"""Agentic mining: agent's productive work generates currency.

Working IS playing. No special-casing of np_* calls.
"""

from nullpoint.mining.formula import (
    BASE_VALUES,
    DROP_RATE,
    PULL_COST,
    compute_reward,
)

__all__ = ["BASE_VALUES", "DROP_RATE", "PULL_COST", "compute_reward"]
