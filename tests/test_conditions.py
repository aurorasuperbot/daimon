"""Tests for the trigger-condition DSL (`daimon.engine.conditions`).

Coverage:
  - Valid expressions parse + evaluate correctly
  - All AST whitelist boundaries are exercised (boolops, comparisons,
    arithmetic, attribute access, name access)
  - Disallowed constructs raise ConditionError at parse time
  - Evaluator never sees malformed input (parse-time guarantees)
  - Bool literal short-circuit behavior is preserved
"""

from __future__ import annotations

import pytest

from daimon.engine.conditions import (
    ConditionError,
    compile_condition,
    evaluate,
    parse,
)


# ---------------------------------------------------------------------------
# Standard contexts used across tests.
# ---------------------------------------------------------------------------

CTX_HEALTHY = {
    "self":    {"hp": 30, "hp_max": 30, "shield": 0, "atk": 8, "def": 4,
                "spd": 5, "element": 1},
    "team":    {"distinct_elements": 1, "alive_count": 6, "size": 6},
    "enemies": {"distinct_elements": 1, "alive_count": 6, "size": 6},
    "round":   1,
}

CTX_LOW_HP = {
    "self":    {"hp": 7, "hp_max": 30, "shield": 0, "atk": 8, "def": 4,
                "spd": 5, "element": 1},
    "team":    {"distinct_elements": 3, "alive_count": 4, "size": 6},
    "enemies": {"distinct_elements": 2, "alive_count": 2, "size": 6},
    "round":   4,
}


# ---------------------------------------------------------------------------
# Happy path — comparisons + attribute access.
# ---------------------------------------------------------------------------

class TestBasicComparisons:
    def test_self_hp_full(self):
        assert evaluate("self.hp == self.hp_max", CTX_HEALTHY) is True
        assert evaluate("self.hp == self.hp_max", CTX_LOW_HP) is False

    def test_self_hp_low_threshold(self):
        # 7 / 30 = 0.233 < 0.5
        assert evaluate("self.hp < self.hp_max // 2", CTX_LOW_HP) is True
        assert evaluate("self.hp < self.hp_max // 2", CTX_HEALTHY) is False

    def test_team_distinct_elements(self):
        assert evaluate("team.distinct_elements >= 2", CTX_LOW_HP) is True
        assert evaluate("team.distinct_elements >= 2", CTX_HEALTHY) is False

    def test_enemies_alive_count(self):
        assert evaluate("enemies.alive_count <= 2", CTX_LOW_HP) is True
        assert evaluate("enemies.alive_count <= 2", CTX_HEALTHY) is False

    def test_round_number(self):
        assert evaluate("round >= 4", CTX_LOW_HP) is True
        assert evaluate("round >= 4", CTX_HEALTHY) is False

    def test_chained_comparison(self):
        # 1 <= round <= 5 — both sides must hold
        assert evaluate("1 <= round <= 5", CTX_LOW_HP) is True
        assert evaluate("1 <= round <= 5", CTX_HEALTHY) is True


# ---------------------------------------------------------------------------
# Boolean ops + parentheses.
# ---------------------------------------------------------------------------

class TestBoolOps:
    def test_and_short_circuit(self):
        e = "self.hp > 5 and team.distinct_elements >= 2"
        assert evaluate(e, CTX_LOW_HP) is True   # 7 > 5 AND 3 >= 2
        assert evaluate(e, CTX_HEALTHY) is False  # 30 > 5 BUT 1 >= 2 fails

    def test_or(self):
        e = "round >= 4 or team.distinct_elements >= 4"
        assert evaluate(e, CTX_LOW_HP) is True
        assert evaluate(e, CTX_HEALTHY) is False

    def test_not(self):
        assert evaluate("not (self.hp == self.hp_max)", CTX_LOW_HP) is True
        assert evaluate("not (self.hp == self.hp_max)", CTX_HEALTHY) is False

    def test_complex_combo(self):
        e = "(self.hp < 10 and round >= 2) or team.distinct_elements >= 4"
        assert evaluate(e, CTX_LOW_HP) is True
        assert evaluate(e, CTX_HEALTHY) is False


# ---------------------------------------------------------------------------
# Arithmetic.
# ---------------------------------------------------------------------------

class TestArithmetic:
    def test_floor_division(self):
        # hp_max=30, half=15, hp=7 < 15
        assert evaluate("self.hp < self.hp_max // 2", CTX_LOW_HP) is True

    def test_modulo(self):
        # round=4, 4 % 2 == 0
        assert evaluate("round % 2 == 0", CTX_LOW_HP) is True
        assert evaluate("round % 2 == 0", CTX_HEALTHY) is False

    def test_unary_minus(self):
        assert evaluate("-self.hp < 0", CTX_HEALTHY) is True

    def test_multiply(self):
        # 30 * 2 == 60
        assert evaluate("self.hp_max * 2 == 60", CTX_HEALTHY) is True


# ---------------------------------------------------------------------------
# Float literals (allowed in expressions but coerced to bool result).
# ---------------------------------------------------------------------------

class TestFloatLiterals:
    def test_float_in_arithmetic(self):
        # Python: 7 < 30 * 0.5 → 7 < 15.0 → True
        # We allow float literals to make threshold expressions natural.
        assert evaluate("self.hp < self.hp_max * 0.5", CTX_LOW_HP) is True


# ---------------------------------------------------------------------------
# Whitelist enforcement — disallowed nodes raise at parse time.
# ---------------------------------------------------------------------------

class TestRejectsBannedConstructs:
    def test_function_call_rejected(self):
        with pytest.raises(ConditionError, match="disallowed"):
            parse("len([1,2,3]) > 0")

    def test_method_call_rejected(self):
        with pytest.raises(ConditionError):
            parse("self.hp.bit_length() > 0")

    def test_subscript_rejected(self):
        with pytest.raises(ConditionError, match="disallowed"):
            parse("self.hp[0] > 0")

    def test_lambda_rejected(self):
        with pytest.raises(ConditionError):
            parse("(lambda: True)()")

    def test_import_in_expression_rejected(self):
        # __import__('os') is a Call node — rejected as a disallowed AST type
        # before name-resolution even gets a chance. Either way, blocked.
        with pytest.raises(ConditionError, match="disallowed"):
            parse("__import__('os')")

    def test_unknown_name_rejected(self):
        with pytest.raises(ConditionError, match="unknown name 'foo'"):
            parse("foo > 0")

    def test_unknown_attribute_rejected(self):
        with pytest.raises(ConditionError, match="unknown attribute"):
            parse("self.unknown_field > 0")

    def test_nested_attribute_rejected(self):
        with pytest.raises(ConditionError, match="nested"):
            parse("self.team.size > 0")

    def test_division_rejected(self):
        # We reject / and force // to keep type story integer-clean
        with pytest.raises(ConditionError, match="disallowed"):
            parse("self.hp / 2 > 5")

    def test_power_rejected(self):
        with pytest.raises(ConditionError, match="disallowed"):
            parse("self.hp ** 2 > 0")

    def test_bitwise_rejected(self):
        with pytest.raises(ConditionError, match="disallowed"):
            parse("self.hp & 1 == 0")

    def test_string_literal_rejected(self):
        # Strings shouldn't appear in conditions — defensive.
        with pytest.raises(ConditionError, match="only int/float/bool"):
            parse("self.hp == 'foo'")

    def test_walrus_rejected(self):
        with pytest.raises(ConditionError):
            parse("(x := self.hp) > 0")


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string_rejected(self):
        with pytest.raises(ConditionError, match="empty"):
            parse("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ConditionError, match="empty"):
            parse("   \t  ")

    def test_non_string_rejected(self):
        with pytest.raises(ConditionError, match="must be string"):
            parse(42)

    def test_syntax_error_at_parse(self):
        with pytest.raises(ConditionError, match="syntax error"):
            parse("self.hp >>")

    def test_compile_then_call_repeated(self):
        # Cached callable should evaluate consistently across multiple calls
        f = compile_condition("self.hp < 10")
        assert f(CTX_LOW_HP) is True
        assert f(CTX_HEALTHY) is False
        assert f(CTX_LOW_HP) is True  # repeated, no state pollution

    def test_bool_literal_true(self):
        assert evaluate("True", CTX_HEALTHY) is True

    def test_bool_literal_false(self):
        assert evaluate("False", CTX_HEALTHY) is False

    def test_truthy_int_coerced(self):
        # Result is bool-coerced: any nonzero int evaluates to truthy
        assert evaluate("self.hp", CTX_HEALTHY) is True

    def test_zero_int_falsy(self):
        ctx = {**CTX_HEALTHY, "self": {**CTX_HEALTHY["self"], "hp": 0}}
        assert evaluate("self.hp", ctx) is False


# ---------------------------------------------------------------------------
# Realistic SYNCRETIC-style and ON_LOW_HP-style conditions.
# ---------------------------------------------------------------------------

class TestRealisticConditions:
    def test_syncretic_dual_element(self):
        f = compile_condition("team.distinct_elements >= 2")
        # Mono-element team
        assert f(CTX_HEALTHY) is False
        # Multi-element team
        assert f(CTX_LOW_HP) is True

    def test_syncretic_full_rainbow(self):
        f = compile_condition("team.distinct_elements >= 4")
        assert f(CTX_LOW_HP) is False  # only 3 distinct
        ctx = {**CTX_LOW_HP, "team": {**CTX_LOW_HP["team"], "distinct_elements": 5}}
        assert f(ctx) is True

    def test_low_hp_50pct(self):
        f = compile_condition("self.hp < self.hp_max // 2")
        assert f(CTX_LOW_HP) is True
        assert f(CTX_HEALTHY) is False

    def test_low_hp_25pct(self):
        f = compile_condition("self.hp <= self.hp_max // 4")
        # hp=7, hp_max=30, 30//4 = 7. So 7 <= 7 → True
        assert f(CTX_LOW_HP) is True
        # Healthy: 30 <= 7 → False
        assert f(CTX_HEALTHY) is False

    def test_late_game_swing(self):
        f = compile_condition("round >= 4 and self.hp < self.hp_max // 2")
        assert f(CTX_LOW_HP) is True
        assert f(CTX_HEALTHY) is False

    def test_outnumbered(self):
        f = compile_condition("team.alive_count < enemies.alive_count")
        assert f(CTX_LOW_HP) is False  # 4 alive vs 2 alive
        ctx = {
            **CTX_LOW_HP,
            "team":    {**CTX_LOW_HP["team"], "alive_count": 1},
            "enemies": {**CTX_LOW_HP["enemies"], "alive_count": 5},
        }
        assert f(ctx) is True
