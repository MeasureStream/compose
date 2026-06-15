from __future__ import annotations

import ast
import math
import sys
import warnings
from typing import Dict

import pint as _pint

_UREG = _pint.UnitRegistry()

# Compatibility shim: ast.TypeAlias was added in Python 3.12.
_TYPEALIAS_BLOCKED = hasattr(ast, "TypeAlias")
if sys.version_info >= (3, 12) and not _TYPEALIAS_BLOCKED:
    warnings.warn(
        f"Python {sys.version_info[:2]}: ast.TypeAlias unexpectedly missing; "
        f"formula parser running without TypeAlias support.",
        RuntimeWarning, stacklevel=2,
    )
elif sys.version_info < (3, 12):
    warnings.warn(
        f"Python {sys.version_info[:2]} (pre-3.12): ast.TypeAlias unavailable, "
        f"degraded formula parser in use (no TypeAlias blocking).",
        RuntimeWarning, stacklevel=2,
    )
else:
    warnings.warn(
        f"Python {sys.version_info[:2]}: full formula parser active ",
        RuntimeWarning, stacklevel=2,
    )

# public alias for downstream callers
ureg = _UREG

def qs(value: float) -> _pint.Quantity:
    """Wrap a scalar as a dimensionless pint Quantity."""
    return _UREG.Quantity(float(value), _UREG.dimensionless)

_ALLOWED_FUNCTIONS = frozenset({
    "abs", "max", "min", "round",
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "exp", "log", "log10",
    "degrees", "radians",
})

# AST node types BLOCKED (everything else is allowed; Call nodes further restricted by _ALLOWED_FUNCTIONS)
# ast.TypeAlias is only available in Python 3.12+ (see compatibility shim above).
_BLOCKED_NODES = frozenset({
    ast.Import, ast.ImportFrom,
    ast.Attribute,
    ast.Subscript, ast.Slice,
    ast.Lambda,
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Global, ast.Nonlocal,
    ast.Yield, ast.YieldFrom, ast.Await,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.NamedExpr,
    ast.Delete, ast.Assert, ast.Pass, ast.Break, ast.Continue, ast.Raise, ast.Return,
    ast.For, ast.AsyncFor, ast.While, ast.If, ast.With, ast.AsyncWith,
    ast.Try, ast.ExceptHandler, getattr(ast, "TryStar", None),
    ast.Match, ast.MatchValue, ast.MatchSingleton, ast.MatchSequence, ast.MatchMapping,
    ast.MatchClass, ast.MatchStar, ast.MatchAs, ast.MatchOr,
    ast.AnnAssign, ast.AugAssign, ast.Assign,
    ast.FormattedValue, ast.JoinedStr,
} | ({ast.TypeAlias} if _TYPEALIAS_BLOCKED else set()))

_ALLOWED_BINOPS = frozenset({
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
})

_ALLOWED_UNARYOPS = frozenset({
    ast.UAdd, ast.USub,
})


def evaluate_formula(expression: str, variables: Dict[str, _pint.Quantity]) -> _pint.Quantity:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("evaluationFormula must be a non-empty string")

    tree = ast.parse(expression.strip(), mode="eval")
    return _walk(tree, variables)


def build_formula_variables(
    reading_uncertainty: list,
    calibration_coefficients: Dict[str, float] | None = None,
    ureg: _pint.UnitRegistry | None = None,
) -> Dict[str, _pint.Quantity]:
    if ureg is None:
        ureg = _UREG
    variables: Dict[str, _pint.Quantity] = {}
    for entry in reading_uncertainty:
        if isinstance(entry, dict) and "varName" in entry and "value" in entry:
            variables[entry["varName"]] = ureg.Quantity(float(entry["value"]), ureg.dimensionless)
    if calibration_coefficients:
        for key, val in calibration_coefficients.items():
            variables[key] = ureg.Quantity(float(val), ureg.dimensionless)
    return variables


# ── AST walker ────────────────────────────────────────────────────────────────

def _walk(node: ast.AST, variables: Dict[str, _pint.Quantity]) -> _pint.Quantity:
    _check_node_allowed(node)

    if isinstance(node, ast.Expression):
        return _walk(node.body, variables)

    if isinstance(node, ast.Constant):
        val = node.value
        if isinstance(val, bool):
            return _UREG.Quantity(1.0 if val else 0.0, _UREG.dimensionless)
        if isinstance(val, (int, float)):
            return _UREG.Quantity(float(val), _UREG.dimensionless)
        raise ValueError(f"Constant type {type(val).__name__} not supported in formula")

    if isinstance(node, ast.Name):
        name = node.id
        if name in variables:
            return variables[name]
        raise ValueError(f"Variable '{name}' not defined. Available: {sorted(variables.keys())}")

    if isinstance(node, ast.BinOp):
        left = _walk(node.left, variables)
        right = _walk(node.right, variables)
        op_type = type(node.op)
        if op_type is ast.Add:
            return left + right
        if op_type is ast.Sub:
            return left - right
        if op_type is ast.Mult:
            return left * right
        if op_type is ast.Div:
            return left / right
        if op_type is ast.Pow:
            return left ** right
        if op_type is ast.Mod:
            return left % right
        raise ValueError(f"Binary operator {op_type.__name__} not allowed")

    if isinstance(node, ast.UnaryOp):
        operand = _walk(node.operand, variables)
        op_type = type(node.op)
        if op_type is ast.UAdd:
            return +operand
        if op_type is ast.USub:
            return -operand
        raise ValueError(f"Unary operator {op_type.__name__} not allowed")

    if isinstance(node, ast.Call):
        return _walk_call(node, variables)

    raise ValueError(f"AST node type '{type(node).__name__}' not allowed in formula")


def _walk_call(node: ast.Call, variables: Dict[str, _pint.Quantity]) -> _pint.Quantity:
    if not isinstance(node.func, ast.Name):
        raise ValueError("Only simple function calls (by name) are allowed")
    func_name = node.func.id
    if func_name not in _ALLOWED_FUNCTIONS:
        raise ValueError(
            f"Function '{func_name}' is not allowed. "
            f"Allowed: {sorted(_ALLOWED_FUNCTIONS)}"
        )

    args = [_walk(arg, variables) for arg in node.args]

    # ── math.handling: pint integrates with numpy but not with math.* ──
    if func_name == "sqrt":
        if len(args) != 1:
            raise ValueError("sqrt() takes exactly 1 argument")
        return _to_quantity(math.sqrt(_to_float(args[0])), args[0].units)

    if func_name in ("sin", "cos", "tan"):
        if len(args) != 1:
            raise ValueError(f"{func_name}() takes exactly 1 argument")
        fn = getattr(math, func_name)
        return _UREG.Quantity(fn(_to_float(args[0])), _UREG.dimensionless)

    if func_name in ("asin", "acos", "atan"):
        if len(args) != 1:
            raise ValueError(f"{func_name}() takes exactly 1 argument")
        fn = getattr(math, func_name)
        return _UREG.Quantity(fn(_to_float(args[0])), _UREG.dimensionless)

    if func_name == "atan2":
        if len(args) != 2:
            raise ValueError("atan2() takes exactly 2 arguments")
        return _UREG.Quantity(math.atan2(_to_float(args[0]), _to_float(args[1])), _UREG.dimensionless)

    if func_name in ("exp", "log", "log10"):
        if len(args) != 1:
            raise ValueError(f"{func_name}() takes exactly 1 argument")
        fn = getattr(math, func_name)
        return _UREG.Quantity(fn(_to_float(args[0])), _UREG.dimensionless)

    if func_name in ("degrees", "radians"):
        if len(args) != 1:
            raise ValueError(f"{func_name}() takes exactly 1 argument")
        fn = getattr(math, func_name)
        return _UREG.Quantity(fn(_to_float(args[0])), _UREG.dimensionless)

    if func_name == "abs":
        if len(args) != 1:
            raise ValueError("abs() takes exactly 1 argument")
        return abs(args[0])

    if func_name in ("max", "min"):
        if len(args) < 2:
            raise ValueError(f"{func_name}() requires at least 2 arguments")
        result = args[0]
        fn = max if func_name == "max" else min
        for a in args[1:]:
            result = fn(result, a)
        return result

    if func_name == "round":
        if len(args) == 1:
            ndigits = 0
        elif len(args) == 2:
            ndigits = int(round(_to_float(args[1])))
        else:
            raise ValueError("round() takes 1 or 2 arguments")
        mag = round(_to_float(args[0]), ndigits)
        return _UREG.Quantity(mag, args[0].units)

    raise ValueError(f"Function '{func_name}' not implemented")


def _to_float(q: _pint.Quantity) -> float:
    return float(q.to_base_units().magnitude)


def _to_quantity(value: float, unit: _pint.Unit) -> _pint.Quantity:
    return _UREG.Quantity(value, unit)


def _check_node_allowed(node: ast.AST) -> None:
    node_type = type(node)
    if node_type in _BLOCKED_NODES:
        raise ValueError(
            f"AST node type '{node_type.__name__}' is forbidden in formula expressions. "
            f"Only basic arithmetic, variables, and math functions are permitted."
        )
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls (by name) are allowed")
        if node.func.id not in _ALLOWED_FUNCTIONS:
            raise ValueError(
                f"Function '{node.func.id}' is not allowed. "
                f"Allowed: {sorted(_ALLOWED_FUNCTIONS)}"
            )
    for _field_name, field_value in ast.iter_fields(node):
        if isinstance(field_value, ast.AST):
            _check_node_allowed(field_value)
        elif isinstance(field_value, list):
            for item in field_value:
                if isinstance(item, ast.AST):
                    _check_node_allowed(item)
