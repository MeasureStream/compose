# model_calibration package — calibration engine modules
# Each module exposes: calibrate(), plot_charts(), save_charts(), build_report()
#
# Available models:
#   linear_calibration    — OLS linear regression (polynomial degree 1)
#   quadratic_calibration — OLS quadratic polynomial regression (polynomial degree 2)
#   cubic_calibration     — OLS cubic polynomial regression (polynomial degree 3)
#   steinhart_calibration — OLS Steinhart-Hart (works on preprocessed X)
#   calib_plots           — unified chart generator (bundles for all procedures)
#   unit_checks           — dimensional analysis via pint

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the scripts/ dir importable so model_calibration can reuse the
# top-level evaluation_formula helper (same dir as the orchestrator).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def apply_preprocessing_to_payload(
    payload: Dict[str, Any],
    sensor_json: Optional[Dict[str, Any]] = None,
    verbose: bool = False,
) -> Optional[Dict[str, Any]]:
    """Mutate ``payload`` in place: if the sensor declares a
    ``metrology.preprocessingFormula``, apply it to every
    ``sensor_raw_samples[].value`` array and return the formula/consts
    that were used (so downstream code can label axes and report the
    transformation).

    If no preprocessing formula is declared, the payload is returned
    unchanged and ``None`` is returned. After this call, every consumer
    of ``payload`` (linear, cubic, quadratic, steinhart) sees the same
    X-domain — the engines do not need to know whether a preprocessing
    step was performed.

    The formula is evaluated pointwise via :func:`evaluation_formula.evaluate_formula`,
    so it can reference hardware constants (R_fixed, vRef, adcMax, …)
    declared in ``preprocessingFormulaConstants`` and Pint-aware
    variables.
    """
    if not sensor_json:
        return None

    metrology = sensor_json.get("metrology", {}) or {}
    pp_formula = metrology.get("preprocessingFormula")
    if not pp_formula:
        return None

    pp_consts = dict(metrology.get("preprocessingFormulaConstants", {}) or {})

    from evaluation_formula import evaluate_formula, qs

    n_samples = 0
    for frame in payload.get("sensor_raw_samples", []) or []:
        values = frame.get("value")
        if not values:
            continue
        out: List[float] = []
        for v in values:
            vars_i = {**pp_consts, "d_in": qs(float(v))}
            out.append(float(evaluate_formula(pp_formula, vars_i).magnitude))
        frame["value"] = out
        n_samples += len(out)

    if verbose:
        print(
            f"Applied preprocessing '{pp_formula}' to {n_samples} sensor samples "
            f"(constants: {list(pp_consts.keys())})."
        )

    return {
        "preprocessing_formula": pp_formula,
        "preprocessing_constants": pp_consts,
        "n_samples": n_samples,
    }
