"""Mining formula tests — bounds, monotonicity, novelty decay."""

import pytest

from daimon.mining.formula import (
    BASE_VALUES,
    DROP_RATE,
    MAX_PER_INVOCATION,
    PULL_COST,
    MiningInput,
    compute_reward,
    make_novelty_key,
    _NOVELTY_MEMORY,
)


@pytest.fixture(autouse=True)
def reset_novelty():
    _NOVELTY_MEMORY.clear()
    yield
    _NOVELTY_MEMORY.clear()


def _i(**kw) -> MiningInput:
    base = dict(
        tool_name="Edit",
        success=True,
        output_bytes=200,
        elapsed_ms=300,
        novelty_key="novel-1",
        seconds_since_last_call=10.0,
    )
    base.update(kw)
    return MiningInput(**base)


def test_pull_cost_constant():
    assert PULL_COST == 100


def test_reward_in_bounds():
    out = compute_reward(_i())
    assert 0 <= out.reward <= MAX_PER_INVOCATION


def test_failure_pays_minimal():
    success_reward = compute_reward(_i(success=True, novelty_key="a")).reward
    _NOVELTY_MEMORY.clear()
    fail_reward = compute_reward(_i(success=False, novelty_key="b")).reward
    assert fail_reward < success_reward


def test_novelty_decay():
    rewards = []
    for _ in range(5):
        rewards.append(compute_reward(_i(novelty_key="same")).reward)
    # Strictly non-increasing (with floor)
    for i in range(1, len(rewards)):
        assert rewards[i] <= rewards[i - 1]


def test_burst_calls_pay_less_than_steady():
    burst = compute_reward(_i(seconds_since_last_call=0.1, novelty_key="b1")).reward
    steady = compute_reward(_i(seconds_since_last_call=10.0, novelty_key="b2")).reward
    assert burst <= steady


def test_unknown_tool_uses_default():
    out = compute_reward(_i(tool_name="MysteryTool", novelty_key="m"))
    # No crash, reward in bounds
    assert 0 <= out.reward <= MAX_PER_INVOCATION
    assert out.factors["base"] == BASE_VALUES["_default"]


def test_no_mining_for_chat():
    """Reply / TodoWrite are baseline 0 → reward 0 even on success."""
    out = compute_reward(_i(tool_name="Reply", novelty_key="r"))
    assert out.reward == 0
    out = compute_reward(_i(tool_name="TodoWrite", novelty_key="t"))
    assert out.reward == 0


def test_novelty_key_stable():
    a = make_novelty_key("Edit", "/path/file.py", "abc123")
    b = make_novelty_key("Edit", "/path/file.py", "abc123")
    c = make_novelty_key("Edit", "/path/file.py", "abc124")
    assert a == b
    assert a != c


def test_factors_present_in_output():
    out = compute_reward(_i())
    for k in ("base", "value_signal", "novelty", "time_decay", "drop_rate", "raw"):
        assert k in out.factors
