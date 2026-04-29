"""Agentic mining: agent's productive work generates currency.

Working IS playing. No special-casing of dm_* calls.
"""

from daimon.mining.formula import (
    BASE_VALUES,
    DROP_RATE,
    PULL_COST,
    MiningInput,
    MiningOutput,
    compute_reward,
    make_novelty_key,
)
from daimon.mining.ledger import (
    LEDGER_PATH,
    InsufficientBalanceError,
    LedgerCorruptError,
    LedgerError,
    LedgerStats,
    append_mine_entry,
    append_pull_entry,
    get_balance,
    get_recent_entries,
    get_stats,
    initialize_ledger,
    repair_ledger,
    verify_ledger,
)

__all__ = [
    "BASE_VALUES",
    "DROP_RATE",
    "InsufficientBalanceError",
    "LEDGER_PATH",
    "LedgerCorruptError",
    "LedgerError",
    "LedgerStats",
    "MiningInput",
    "MiningOutput",
    "PULL_COST",
    "append_mine_entry",
    "append_pull_entry",
    "compute_reward",
    "get_balance",
    "get_recent_entries",
    "get_stats",
    "initialize_ledger",
    "make_novelty_key",
    "repair_ledger",
    "verify_ledger",
]
