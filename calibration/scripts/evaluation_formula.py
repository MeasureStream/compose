from __future__ import annotations

import math
from typing import Dict

_SAFE_GLOBALS: Dict = {
    "__builtins__": {
        "abs": abs,
        "max": max,
        "min": min,
        "round": round,
        "int": int,
        "float": float,
        "True": True,
        "False": False,
        "None": None,
    },
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "pow": math.pow,
    "pi": math.pi,
    "e": math.e,
    "degrees": math.degrees,
    "radians": math.radians,
}


def evaluate_formula(formula: str, variables: Dict[str, float]) -> float:
    if not isinstance(formula, str) or not formula.strip():
        raise ValueError("evaluationFormula must be a non-empty string")

    result = eval(formula, {"__builtins__": _SAFE_GLOBALS["__builtins__"], **_SAFE_GLOBALS}, dict(variables))
    return float(result)


def build_formula_variables(
    reading_uncertainty: list,
    calibration_coefficients: Dict[str, float] | None = None,
) -> Dict[str, float]:
    variables: Dict[str, float] = {}
    for entry in reading_uncertainty:
        if isinstance(entry, dict) and "varName" in entry and "value" in entry:
            variables[entry["varName"]] = float(entry["value"])
    if calibration_coefficients:
        for key, val in calibration_coefficients.items():
            variables[key] = float(val)
    return variables
