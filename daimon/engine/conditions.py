"""Restricted-eval condition DSL for triggers.

Phase 2 (V1 vocab expansion, 2026-04-22): some triggers fire conditionally —
e.g. FLUX cards require `team.distinct_elements >= 2`, ON_LOW_HP-style cards
guard on `self.hp < self.hp_max * 0.5`. The condition is a small expression
language parsed at card-load time; invalid expressions raise during catalog
load (NEVER mid-match — the engine is deterministic and parse errors break
that contract).

Grammar (allowed AST node types):
    - Boolean ops:    and, or, not
    - Comparisons:    < > <= >= == !=  (int comparisons; chained allowed)
    - Arithmetic:     + - * // %       (no float div, no power, no bit ops)
    - Unary:          -x, +x
    - Constants:      int, float (float allowed in literals only — for "0.5")
    - Names:          whitelisted identifiers (see _ALLOWED_NAMES)
    - Attributes:     whitelisted "{base}.{attr}" pairs (see _ALLOWED_ATTRS)
    - Parens:         allowed implicitly via grouping

Disallowed (raises at parse time):
    - Function calls, method calls, subscripts, slices
    - Imports, comprehensions, lambdas, walruses
    - Any name not in the whitelist
    - Any attribute access outside the whitelist
    - Division (`/`) — use `//` for integer division to keep the type story clean

Evaluation context:
    Caller passes a dict like:
        {
            "self":    {"hp": 12, "hp_max": 30, "shield": 0, "atk": 8, "def": 4,
                        "spd": 5, "element": 1},
            "team":    {"distinct_elements": 3, "alive_count": 5, "size": 6},
            "enemies": {"alive_count": 4, "size": 6},
            "round":   2,
        }
    Names get attribute-style access via SimpleNamespace under the hood.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
from typing import Any, Dict, FrozenSet


# Top-level identifiers that may appear in a condition expression.
_ALLOWED_NAMES: FrozenSet[str] = frozenset({
    "self",
    "team",
    "enemies",
    "round",
    "True",
    "False",
})

# Attribute paths in the form "base.attr". Attempting any others raises.
_ALLOWED_ATTRS: FrozenSet[str] = frozenset({
    # self
    "self.hp",
    "self.hp_max",
    "self.shield",
    "self.atk",
    "self.def",
    "self.spd",
    "self.element",
    # Phase 4f-engine additions (charter §21.4) — read-only access to the new
    # state primitives. No new ops, no function calls, no subscripts.
    "self.burn_stacks",
    "self.shield_count",
    "self.extra_actions_used_this_round",
    # team (the unit's own side)
    "team.distinct_elements",
    "team.alive_count",
    "team.size",
    # enemies (the opposing side)
    "enemies.distinct_elements",
    "enemies.alive_count",
    "enemies.size",
})

_ALLOWED_NODES: tuple[type, ...] = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod,
    ast.Compare, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq,
    ast.Constant,
    ast.Name, ast.Load,
    ast.Attribute,
)


class ConditionError(ValueError):
    """Raised when a condition expression is malformed or uses banned constructs.

    Always raised at parse time (catalog load), never at fire time.
    """


def _validate(node: ast.AST) -> None:
    """Walk the AST and reject any node type or name/attribute outside the
    whitelist. Recursive."""
    if not isinstance(node, _ALLOWED_NODES):
        raise ConditionError(
            f"disallowed expression node: {type(node).__name__}"
        )

    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise ConditionError(f"unknown name {node.id!r}")
        return

    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            raise ConditionError(
                f"only int/float/bool literals allowed, got "
                f"{type(node.value).__name__}"
            )
        return

    if isinstance(node, ast.Attribute):
        # Build the dotted path. Only one level of attribute access is allowed
        # (e.g. self.hp); deeper chains (self.foo.bar) are rejected.
        if not isinstance(node.value, ast.Name):
            raise ConditionError(
                "nested attribute access not allowed (only base.attr)"
            )
        path = f"{node.value.id}.{node.attr}"
        if path not in _ALLOWED_ATTRS:
            raise ConditionError(f"unknown attribute path {path!r}")
        # Validate base name too (no-op since we already checked above, but
        # explicit makes the recursion legible).
        _validate(node.value)
        return

    # Recurse into children for everything else.
    for child in ast.iter_child_nodes(node):
        _validate(child)


def parse(expr: str) -> ast.Expression:
    """Parse + validate a condition string. Raises ConditionError on any
    syntax error or whitelist violation. Returns the validated AST so callers
    that want to cache the parse result can.

    Most call sites will use `compile_condition` instead (returns a callable).
    """
    if not isinstance(expr, str):
        raise ConditionError(f"condition must be string, got {type(expr).__name__}")
    if not expr.strip():
        raise ConditionError("condition is empty")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionError(f"syntax error: {e.msg}") from e
    _validate(tree)
    return tree


def compile_condition(expr: str):
    """Parse + validate + return a callable `evaluate(context_dict) -> bool`.

    The returned callable performs no parsing — repeated calls are cheap.
    """
    tree = parse(expr)
    code = compile(tree, filename="<condition>", mode="eval")

    def _evaluate(context: Dict[str, Any]) -> bool:
        # Wrap dict-valued keys as SimpleNamespace so attribute access works.
        # Pass-through scalar values (e.g. round=2).
        env: Dict[str, Any] = {}
        for k in _ALLOWED_NAMES:
            if k in ("True", "False"):
                continue  # bool literals are handled by Python itself
            v = context.get(k)
            if isinstance(v, dict):
                env[k] = SimpleNamespace(**v)
            else:
                env[k] = v
        # builtins=None blocks ALL builtin access in the eval frame.
        result = eval(code, {"__builtins__": None}, env)  # noqa: S307 — intentional restricted eval
        return bool(result)

    return _evaluate


def evaluate(expr: str, context: Dict[str, Any]) -> bool:
    """One-shot helper: parse + evaluate. For repeated evaluation,
    use `compile_condition` and cache the callable."""
    return compile_condition(expr)(context)
