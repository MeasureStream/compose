from __future__ import annotations

import argparse
import copy
import datetime
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

# Force UTF-8 stdout/stderr on Windows where the default console encoding
# (cp1252) cannot represent characters like ≈ (U+2248) used in print statements.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

SCRIPTS_DIR        = Path(__file__).resolve().parent
CALIB_ROOT         = SCRIPTS_DIR.parent
MODELS_DIR         = CALIB_ROOT / "models_in"
SENSORS_DIR         = MODELS_DIR / "sensors"
REFERENCES_DIR      = MODELS_DIR / "references"
TEMPLATE_DIR       = CALIB_ROOT / "template_in"
DATA_DIR           = CALIB_ROOT / "data_in"
OUT_DIR            = CALIB_ROOT / "certificato_out"
TEST_DATA_DIR      = CALIB_ROOT / "test" / "data_in"
IMAGES_CALIB_DIR   = CALIB_ROOT / "images" / "calibration"
IMAGES_CONFORM_DIR = CALIB_ROOT / "images" / "conformity"
LAST_CALIB_DIR    = CALIB_ROOT / "last_calibration"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from model_calibration import apply_preprocessing_to_payload  # noqa: E402
from model_calibration.unit_checks import dsi_to_symbol, dsi_to_xml_unit  # noqa: E402
from calib_utils import _lookup, SensorAccuracyChecker, lsb_to_y, round_to_significant_figures  # noqa: E402


def _get_max_tollerance(sensor_json: Dict[str, Any]) -> float | None:
    """Read maxTollerance from Uncertainty array (by varName). Falls back to legacy sensorAccuracy."""
    unc = sensor_json.get("metrology", {}).get("Uncertainty", [])
    for item in unc:
        if item.get("varName") == "maxTollerance":
            return float(item.get("value", 0))
    # Legacy fallback: use first sensorAccuracy entry
    legacy = sensor_json.get("metrology", {}).get("sensorAccuracy", [])
    if legacy:
        return float(legacy[0].get("maxError", 0))
    return None


def _get_coverage_factor(sensor_json: Dict[str, Any]) -> float:
    """Extract coverage factor k from sensor JSON (readingUncertainty or Uncertainty)."""
    ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    for item in ru:
        if item.get("varName") == "coverageFactor":
            return float(item.get("value", 2.0))
    unc = sensor_json.get("metrology", {}).get("Uncertainty", [])
    for item in unc:
        k = item.get("k")
        if k is not None:
            return float(k)
    return 2.0


def _get_abs_uncertainty(sensor_json: Dict[str, Any]) -> float:
    """Read absUncertainty from Uncertainty array by varName (or legacy index)."""
    unc = sensor_json.get("metrology", {}).get("Uncertainty", [])
    for item in unc:
        if item.get("varName") == "absUncertainty":
            return float(item.get("value", 0.10))
    # Legacy fallback: first entry's absUncertainty
    if unc:
        return float(unc[0].get("absUncertainty", 0.10))
    return 0.10


def _worst_accuracy_limit(max_tollerance: float | None) -> float | None:
    return max_tollerance


def _get_calib_coeff(sensor_json: Dict[str, Any], label: str) -> float:
    coeff = sensor_json.get("calibration", {}).get("calibrationCoefficients", {}).get(label, {})
    return float(coeff.get("value", 0.0)) if isinstance(coeff, dict) else float(coeff)


def _validate_output_domain(
    cert_filled: Dict[str, Any],
    sensor_json: Dict[str, Any],
    unit_symbol: str,
) -> None:
    """R18: verify certificate measurements fall within the declared physical range.
    Pint-aware: if the sensor's phys range is in degC but the certificate is in a
    different unit (e.g. mK, K), the range values are converted to the cert unit
    before the check. When the cert unit is degC (no conversion), values are
    compared directly.
    """
    phys = sensor_json.get("ranges", {}).get("phys", {})
    phys_dsi = phys.get("dsi", "\\degreeCelsius")
    phys_min_raw = float(phys.get("min", float("-inf")))
    phys_max_raw = float(phys.get("max", float("inf")))

    if phys_min_raw == float("-inf") and phys_max_raw == float("inf"):
        return

    # Convert the range to the certificate unit (if they differ) using Pint
    phys_min = phys_min_raw
    phys_max = phys_max_raw
    if unit_symbol and unit_symbol != "°C" and unit_symbol != "degC":
        try:
            import sys as _sys
            sys.path.insert(0, str(Path(__file__).resolve().parent / "model_calibration"))
            from unit_checks import _dsi_to_pint_name, _UREG
            src = _dsi_to_pint_name(phys_dsi)
            tgt = unit_symbol
            if _UREG is not None and src != tgt:
                phys_min = float(_UREG.Quantity(phys_min_raw, src).to(tgt).magnitude)
                phys_max = float(_UREG.Quantity(phys_max_raw, src).to(tgt).magnitude)
        except Exception:
            # Pint not available or units incompatible — fall back to raw
            phys_min = phys_min_raw
            phys_max = phys_max_raw

    margin = max(0.5 * (phys_max - phys_min), 10.0)

    measurements = (
        cert_filled.get("template_parts", {})
        .get("calculated_calibration_values", {})
        .get("_measurements", [])
    )
    if not measurements:
        return

    for row in measurements:
        t_ref = row[1]
        t_c = row[2]
        me_pre = row[3]
        me_post = row[4]
        u_exp = row[5]

        if any(v < phys_min - margin or v > phys_max + margin
               for v in (t_ref, t_c, me_pre, me_post, u_exp)):
            import sys as _sys
            print(
                f"\n*** [R18] DOMAIN ERROR: certificate values outside declared physical range "
                f"[{phys_min:.0f}, {phys_max:.0f}] {unit_symbol} "
                f"(sensor JSON declares [{phys_min_raw:.0f}, {phys_max_raw:.0f}] {phys_dsi}). "
                f"Data may be in LSB domain (16-bit ADC 0-65535) instead of {unit_symbol}. "
                f"Check pipeline LSB->{unit_symbol} conversion.\n",
                file=_sys.stderr,
            )
            break


def _build_last_calib_json(
    calib_result: Dict[str, Any],
    cert_filled: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a comprehensive JSON with all calibration results for downstream consumers."""
    import datetime as _dt

    calib_model = calib_result.get("model", "linear")
    measurements = (
        cert_filled.get("template_parts", {})
        .get("calculated_calibration_values", {})
        .get("_measurements", [])
    )
    calib_cr = cert_filled.get("_calibration_result", {})

    # Prefer converted values when --convert-units was applied
    _conv = calib_result.get("converted", {})

    def _conv_or(key: str, default):
        return _conv.get(key, default)

    out: Dict[str, Any] = {
        "model": calib_model,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "calibration_done": calib_result.get("calibration_done", "done"),
        "lsb_per_y": calib_result.get("lsb_per_y"),
        "fit_quality": {
            "rmse": calib_result.get("rmse"),
            "u_fitting": calib_result.get("u_fitting"),
            "rmse_pre": calib_cr.get("_rmse_pre"),
        },
        "reference": {
            "ub_ref_y": calib_result.get("ub_ref_y"),
        },
        "sensor": {
            "ub_sensor_lsb": calib_result.get("ub_sensor_lsb"),
            "ub_sensor_lsb_per_step": calib_result.get("ub_sensor_lsb_per_step"),
        },
        "expanded_uncertainties": _conv_or("expanded_uncertainties",
                                          calib_result.get("expanded_uncertainties")),
        "temp_nominali": calib_result.get("temp_nominali"),
        "ref_temp_means": _conv_or("ref_temp_means", calib_result.get("ref_temp_means")),
    }
    if _conv:
        out["units"] = calib_result.get("units", {})

    if calib_model == "linear":
        out["coefficients"] = {
            "A":    _conv_or("A", calib_result.get("A")),
            "B":    _conv_or("B", calib_result.get("B")),
            "u_A":  _conv_or("u_A", calib_result.get("u_A")),
            "u_B":  _conv_or("u_B", calib_result.get("u_B")),
            "cov_AB": calib_result.get("cov_AB"),
        }
        out["old_coefficients"] = {
            "A": calib_result.get("old_A"),
            "B": calib_result.get("old_B"),
        }
        budget = calib_result.get("u_budget_per_step", [])
    elif calib_model == "cubic":
        out["coefficients"] = {
            "a0": _conv_or("a0", calib_result.get("a0")),
            "a1": _conv_or("a1", calib_result.get("a1")),
            "a2": _conv_or("a2", calib_result.get("a2")),
            "a3": _conv_or("a3", calib_result.get("a3")),
            "u_a0": _conv_or("u_a0", calib_result.get("u_a0")),
            "u_a1": _conv_or("u_a1", calib_result.get("u_a1")),
            "u_a2": _conv_or("u_a2", calib_result.get("u_a2")),
            "u_a3": _conv_or("u_a3", calib_result.get("u_a3")),
            "cov_theta": calib_result.get("cov_theta"),
        }
        out["old_coefficients"] = {
            "a0": calib_result.get("old_a0"), "a1": calib_result.get("old_a1"),
            "a2": calib_result.get("old_a2"), "a3": calib_result.get("old_a3"),
        }
        budget = calib_result.get("per_step_budget", [])
    elif calib_model == "quadratic":
        out["coefficients"] = {
            "a0": _conv_or("a0", calib_result.get("a0")),
            "a1": _conv_or("a1", calib_result.get("a1")),
            "a2": _conv_or("a2", calib_result.get("a2")),
            "u_a0": _conv_or("u_a0", calib_result.get("u_a0")),
            "u_a1": _conv_or("u_a1", calib_result.get("u_a1")),
            "u_a2": _conv_or("u_a2", calib_result.get("u_a2")),
            "cov_theta": calib_result.get("cov_theta"),
        }
        out["old_coefficients"] = {
            "a0": calib_result.get("old_a0"), "a1": calib_result.get("old_a1"),
            "a2": calib_result.get("old_a2"),
        }
        budget = calib_result.get("per_step_budget", [])
    elif calib_model == "steinhart":
        out["coefficients"] = {
            "a": _conv_or("a", calib_result.get("a")),
            "b": _conv_or("b", calib_result.get("b")),
            "c": _conv_or("c", calib_result.get("c")),
            "u_a": _conv_or("u_a", calib_result.get("u_a")),
            "u_b": _conv_or("u_b", calib_result.get("u_b")),
            "u_c": _conv_or("u_c", calib_result.get("u_c")),
            "cov_theta": calib_result.get("cov_theta"),
        }
        out["old_coefficients"] = {
            "a": calib_result.get("old_a"), "b": calib_result.get("old_b"),
            "c": calib_result.get("old_c"),
        }
        out["preprocessing"] = {
            "formula": calib_result.get("preprocessing_formula"),
            "R_arr": calib_result.get("R_arr"),
            "ln_R_arr": calib_result.get("ln_R_arr"),
            "T_K_arr": calib_result.get("T_K_arr"),
        }
        budget = calib_result.get("per_step_budget", [])
    else:
        budget = []

    # Per-step calibration points with uncertainties (use converted values)
    ref_means_for_points = out["ref_temp_means"] or []
    exp_uncs_for_points = out["expanded_uncertainties"] or []
    calibration_points = []
    for i, t_nom in enumerate(calib_result.get("temp_nominali", [])):
        point: Dict[str, Any] = {
            "point": i + 1,
            "t_nominal": t_nom,
            "T_ref": ref_means_for_points[i] if i < len(ref_means_for_points) else None,
        }
        if i < len(measurements):
            m = measurements[i]
            point.update({
                "T_sensor_post": m[2],
                "M_e_pre":       m[3],
                "M_e_post":      m[4],
                "U_exp":         m[5],
            })
        if i < len(exp_uncs_for_points):
            point["U_exp"] = exp_uncs_for_points[i]
        if i < len(budget):
            b = budget[i]
            for key in ("uA_ref", "uA_sensor", "ub_uso", "u_fitting",
                        "u_ref", "u_sensor", "u_c"):
                if key in b:
                    point[key] = b[key]
        calibration_points.append(point)

    out["calibration_points"] = calibration_points
    return out


def _build_cert_filled(
    cert_input: Dict[str, Any],
    sensor_json: Dict[str, Any],
    calib_result: Dict[str, Any],
    adc_max: float,
    lsb_scale: Dict[str, Any],
    ref_json: Dict[str, Any] | None = None,
    unit_symbol: str = "°C",
) -> Dict[str, Any]:
    out = copy.deepcopy(cert_input)
    tp = out["template_parts"]

    calib_model = calib_result.get("model", "linear")
    # Normalise step list key: regression models use "temp_nominali", interp models use "steps"
    temp_nominali: List[float] = calib_result.get("temp_nominali") or calib_result.get("steps", [])
    risultati_elaborati = calib_result["risultati_elaborati"]
    lsb_per_y: float = calib_result["lsb_per_y"]

    # ── Prefer converted values when --convert-units was used ────────────────
    _conv = calib_result.get("converted", {})
    _lsb_scale = dict(lsb_scale)  # may be adjusted below
    if _conv:
        ref_temp_means: List[float] = _conv.get("ref_temp_means", calib_result["ref_temp_means"])
        expanded_uncertainties: List[float] = _conv.get("expanded_uncertainties", calib_result["expanded_uncertainties"])
        # Adjust the LSB scale so that lsb_to_y / y_to_lsb produce values in the
        # target unit rather than the source unit.
        _orig_means = calib_result.get("ref_temp_means", [])
        _conv_means = _conv.get("ref_temp_means", _orig_means)
        if _orig_means and _conv_means and len(_orig_means) == len(_conv_means):
            # Pick the last step (maximum value) to minimise relative error.
            _i_f = -1
            _of = _orig_means[_i_f]
            _cf = _conv_means[_i_f]
            _factor = _cf / _of if abs(_of) > 1e-12 else 1.0
            _lsb_scale = {
                "minPhysVal": lsb_scale["minPhysVal"] * _factor,
                "maxPhysVal": lsb_scale["maxPhysVal"] * _factor,
            }
    else:
        ref_temp_means: List[float] = calib_result["ref_temp_means"]
        expanded_uncertainties: List[float] = calib_result["expanded_uncertainties"]

    # Replace the parameter with the possibly-rescaled version for the rest
    # of this function (lsb_to_y calls, etc.).
    lsb_scale = _lsb_scale

    _phys_dsi: str = sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    _phys_unit_symbol: str = dsi_to_symbol(_phys_dsi)
    _phys_unit_dsi: str = dsi_to_xml_unit(_phys_dsi)

    smt = tp["sensor_method_template"]
    _k = _get_coverage_factor(sensor_json)
    _ref_name = (ref_json or {}).get("modelName", "")
    _ref_cert = (ref_json or {}).get("calibrationCertificateID", "")
    _ref_manufacturer = (ref_json or {}).get("manufacturer", "")
    smt["_notes_computed"] = [
        "Results refer to the instrument under calibration under the declared conditions.",
        "Uncertainties are determined according to ISO/IEC Guide 98-3 (GUM) and EA-4/02.",
        f"Coverage factor k = {_k}, confidence level about 95 %.",
        f"Reference instrument: {_ref_manufacturer} {_ref_name} (calibration certificate: {_ref_cert}).",
        (
            f"Sensor under calibration: DIGITAL thermometer "
            f"({smt.get('manufacturer', '')}, model {smt.get('model', '')})."
        ),
    ]

    sensor_model = smt.get("sensor_model", smt.get("ntc_model", {}))
    sensor_model.update({
        "uncertainty_limit": _get_abs_uncertainty(sensor_json),
        "calibration_procedure": sensor_json.get("calibration", {}).get("type", "linear"),
        "method_description": sensor_json.get("methodDescription", ""),
        "observations": sensor_json.get("obsList", []),
    })
    sensor_model["_calib_model"] = calib_model

    if calib_model == "linear":
        A = calib_result["A"]
        B = _conv.get("B", calib_result["B"])
        u_A = calib_result["u_A"]
        u_B = calib_result["u_B"]
        cov_AB = calib_result["cov_AB"]
        A_r = round_to_significant_figures(A, 4)
        B_r = round_to_significant_figures(B, 4)
        sensor_model.update({
            "_A_cal": A_r, "_B_cal": B_r, "_u_A": u_A, "_u_B": u_B, "_cov_AB": cov_AB,
        })
    elif calib_model == "cubic":
        _c_a0 = _conv.get("a0", calib_result["a0"])
        _c_a1 = _conv.get("a1", calib_result["a1"])
        _c_a2 = _conv.get("a2", calib_result["a2"])
        _c_a3 = _conv.get("a3", calib_result["a3"])
        # Build a possibly-converted theta array for cubic_predict below
        _c_theta = [_c_a0, _c_a1, _c_a2, _c_a3]
        sensor_model.update({
            "_theta": _c_theta,
            "_a0": _c_a0, "_a1": _c_a1,
            "_a2": _c_a2, "_a3": _c_a3,
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"], "_u_a3": calib_result["u_a3"],
            "_cov_theta": calib_result["cov_theta"],
        })
    elif calib_model == "quadratic":
        _c_a0 = _conv.get("a0", calib_result["a0"])
        _c_a1 = _conv.get("a1", calib_result["a1"])
        _c_a2 = _conv.get("a2", calib_result["a2"])
        _c_theta = [_c_a0, _c_a1, _c_a2]
        sensor_model.update({
            "_theta": _c_theta,
            "_a0": _c_a0, "_a1": _c_a1, "_a2": _c_a2,
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"],
            "_cov_theta": calib_result["cov_theta"],
        })
    elif calib_model == "steinhart":
        _c_a = _conv.get("a", calib_result["a"])
        _c_b = _conv.get("b", calib_result["b"])
        _c_c = _conv.get("c", calib_result["c"])
        _c_theta = [_c_a, _c_b, _c_c]
        sensor_model.update({
            "_theta": _c_theta,
            "_a": _c_a, "_b": _c_b, "_c": _c_c,
            "_u_a": calib_result["u_a"], "_u_b": calib_result["u_b"],
            "_u_c": calib_result["u_c"],
            "_cov_theta": calib_result["cov_theta"],
            "_R_arr": calib_result.get("R_arr"),
            "_preprocessing_formula": calib_result.get("preprocessing_formula"),
        })

    smt["sensor_model"] = sensor_model

    measurements: List[List[float]] = []
    for i, t in enumerate(temp_nominali):
        pmean_sensor = risultati_elaborati[t]["pmean_sensor"]  # X [LSB]
        ref_t = ref_temp_means[i]                          # [°C]

        # --- M_e_pre: as-found sensor temperature using previous calibration ---
        if calib_model == "linear":
            _prev_A = calib_result.get("old_A")
            _prev_B = calib_result.get("old_B")
            if _prev_A is not None and _prev_B is not None:
                t_sensor_pre = _prev_A * pmean_sensor + _prev_B
            else:
                t_sensor_pre = lsb_to_y(pmean_sensor, lsb_scale, adc_max)
        elif calib_model == "cubic":
            _prev_a0 = calib_result.get("old_a0")
            _prev_a1 = calib_result.get("old_a1")
            _prev_a2 = calib_result.get("old_a2")
            _prev_a3 = calib_result.get("old_a3")
            if all(v is not None for v in [_prev_a0, _prev_a1, _prev_a2, _prev_a3]):
                from model_calibration.cubic_calibration import cubic_predict
                _old_theta = np.array([_prev_a0, _prev_a1, _prev_a2, _prev_a3], dtype=float)
                t_sensor_pre = cubic_predict(float(pmean_sensor), _old_theta)
            else:
                t_sensor_pre = lsb_to_y(pmean_sensor, lsb_scale, adc_max)
        elif calib_model == "quadratic":
            _prev_a0 = calib_result.get("old_a0")
            _prev_a1 = calib_result.get("old_a1")
            _prev_a2 = calib_result.get("old_a2")
            if all(v is not None for v in [_prev_a0, _prev_a1, _prev_a2]):
                from model_calibration.quadratic_calibration import quadratic_predict
                _old_theta = np.array([_prev_a0, _prev_a1, _prev_a2], dtype=float)
                t_sensor_pre = quadratic_predict(float(pmean_sensor), _old_theta)
            else:
                t_sensor_pre = lsb_to_y(pmean_sensor, lsb_scale, adc_max)
        elif calib_model == "steinhart":
            _prev_a = calib_result.get("old_a")
            _prev_b = calib_result.get("old_b")
            _prev_c = calib_result.get("old_c")
            if all(v is not None for v in [_prev_a, _prev_b, _prev_c]):
                from model_calibration.steinhart_calibration import steinhart_predict_sh
                _R_arr = calib_result.get("R_arr", [])
                _R_i = float(_R_arr[i]) if i < len(_R_arr) else float(pmean_sensor)
                _old_theta = np.array([_prev_a, _prev_b, _prev_c], dtype=float)
                t_sensor_pre = steinhart_predict_sh(_R_i, _old_theta)
            else:
                t_sensor_pre = lsb_to_y(pmean_sensor, lsb_scale, adc_max)
        else:
            t_sensor_pre = lsb_to_y(pmean_sensor, lsb_scale, adc_max)

        # --- M_e_post: post-calibration sensor temperature using new coefficients ---
        if calib_model == "linear":
            t_sensor_post = A * pmean_sensor + B
        elif calib_model == "cubic":
            from model_calibration.cubic_calibration import cubic_predict
            t_sensor_post = cubic_predict(float(pmean_sensor), np.array(_c_theta))
        elif calib_model == "quadratic":
            from model_calibration.quadratic_calibration import quadratic_predict
            t_sensor_post = quadratic_predict(float(pmean_sensor), np.array(_c_theta))
        elif calib_model == "steinhart":
            from model_calibration.steinhart_calibration import steinhart_predict_sh
            _R_arr = calib_result.get("R_arr", [])
            _R_i = float(_R_arr[i]) if i < len(_R_arr) else float(pmean_sensor)
            t_sensor_post = steinhart_predict_sh(_R_i, np.array(_c_theta))
        else:
            t_sensor_post = lsb_to_y(pmean_sensor, lsb_scale, adc_max)

        error_pre = t_sensor_pre - ref_t
        error_post = t_sensor_post - ref_t
        print(
            f"[{calib_model}] row[{i}] point={i + 1} "
            f"sensor pre={t_sensor_pre:.10f} {unit_symbol} post={t_sensor_post:.10f} {unit_symbol} | "
            f"error pre={error_pre:.10f} {unit_symbol} post={error_post:.10f} {unit_symbol}"
        )

        measurements.append([
            float(i + 1), ref_t, t_sensor_pre, error_pre, error_post, expanded_uncertainties[i],
        ])

    rmse_pre = float(math.sqrt(sum(e[3]**2 for e in measurements) / max(1, len(measurements))))
    print(f"[{calib_model}] RMSE pre-error: {rmse_pre:.6f} {unit_symbol}")

    measurements_rounded = [
        [int(row[0]),
         round(row[1], 2),
         round(row[2], 2),
         round(row[3], 2),
         round(row[4], 2),
         round(row[5], 2)]
        for row in measurements
    ]

    tp["calculated_calibration_values"] = {
        "_measurements": measurements,
        "measurements": measurements_rounded,
        "_observations": sensor_json.get("obsList", []),
        "observations": sensor_json.get("obsList", []),
        "conclusions": f"Expanded uncertainty U(E) with coverage factor k = {_k}, confidence level about 95 %.",
    }

    # Uncertainty estimate for certificate page 4: ref type-B + sensor abs, informational only.
    ref_abs_y = float(calib_result.get("ub_ref_y", calib_result.get("ub_ref_lsb", 0.0) / lsb_per_y))
    _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    sensor_abs_y = float(_lookup(_sensor_ru, "varName", "absUncertainty", {}).get("value", 5.0)) / lsb_per_y
    interp_sum_y =     ref_abs_y + sensor_abs_y
    interp_fixed_2sig = round_to_significant_figures(interp_sum_y, 2)

    cal_result_entry: Dict[str, Any] = {
        "_calib_model": calib_model,
        "_calibration_procedure": sensor_json.get("calibration", {}).get("type", "linear"),
        "_method_description": sensor_json.get("methodDescription", ""),
        "_lsb_per_y": lsb_per_y,
        "_adc_bits": int(round(math.log2(adc_max + 1.0))),
        "_phys_unit_symbol": unit_symbol,
        "_phys_unit_dsi": _phys_unit_dsi,
        "_expanded_uncertainties": expanded_uncertainties,
        "_interp_unc_sum_abs": interp_sum_y,
        "_interp_unc_fixed_2sig": interp_fixed_2sig,
        "_rmse": calib_result.get("rmse", 0.0),
        "_rmse_pre": rmse_pre,
        "_ref_temp_means": ref_temp_means,
        "_temp_nominali": temp_nominali,
        "_variant": "funzione",
        # backward-compat aliases
        "_expanded_uncertainties_phys": expanded_uncertainties,
        "_unc_sum_abs_phys": interp_sum_y,
        "_unc_fixed_2sig_phys": interp_fixed_2sig,
        "_ref_means_phys": ref_temp_means,
    }

    if calib_model == "linear":
        u_budget_raw = calib_result.get("u_budget_per_step", [])
        u_budget_rounded = [
            {**b,
             "uA_ref": round_to_significant_figures(b["uA_ref"], 2),
             "uA_sensor": round_to_significant_figures(b["uA_sensor"], 2),
             "u_c": round_to_significant_figures(b["u_c"], 2)}
            for b in u_budget_raw
        ]
        cal_result_entry.update({
            "_A": A, "_B": B,
            "_u_budget_per_step": u_budget_rounded,
        })
    elif calib_model == "cubic":
        u_budget_raw = calib_result.get("per_step_budget", [])
        u_budget_rounded = [
            {**b,
             "u_c": b.get("mu_E", 0.0) / _k, "k": _k,
             "uA_ref": round_to_significant_figures(b["uA_ref"], 2),
             "uA_sensor": round_to_significant_figures(b["uA_sensor"], 2)}
            for b in u_budget_raw
        ]
        cal_result_entry.update({
            "_theta": _c_theta,
            "_a0": _c_a0, "_a1": _c_a1,
            "_a2": _c_a2, "_a3": _c_a3,
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"], "_u_a3": calib_result["u_a3"],
            "_cov_theta": calib_result["cov_theta"],
            "_u_budget_per_step": u_budget_rounded,
        })
    elif calib_model == "quadratic":
        u_budget_raw = calib_result.get("per_step_budget", [])
        u_budget_rounded = [
            {**b,
             "u_c": b.get("mu_E", 0.0) / _k, "k": _k,
             "uA_ref": round_to_significant_figures(b["uA_ref"], 2),
             "uA_sensor": round_to_significant_figures(b["uA_sensor"], 2)}
            for b in u_budget_raw
        ]
        cal_result_entry.update({
            "_theta": _c_theta,
            "_a0": _c_a0, "_a1": _c_a1, "_a2": _c_a2,
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"],
            "_cov_theta": calib_result.get("cov_theta"),
            "_u_budget_per_step": u_budget_rounded,
        })
    elif calib_model == "steinhart":
        u_budget_raw = calib_result.get("per_step_budget", [])
        u_budget_rounded = [
            {**b,
             "u_c": b.get("mu_E", 0.0) / _k, "k": _k,
             "uA_ref": round_to_significant_figures(b["uA_ref"], 2),
             "uA_sensor": round_to_significant_figures(b["uA_sensor"], 2)}
            for b in u_budget_raw
        ]
        cal_result_entry.update({
            "_theta": _c_theta,
            "_a": _c_a, "_b": _c_b, "_c": _c_c,
            "_u_a": calib_result["u_a"], "_u_b": calib_result["u_b"],
            "_u_c": calib_result["u_c"],
            "_cov_theta": calib_result.get("cov_theta"),
            "_R_arr": calib_result.get("R_arr"),
            "_u_budget_per_step": u_budget_rounded,
        })


    if ref_json is not None:
        cal_result_entry["_ref_instrument"] = {
            "modelName": ref_json.get("modelName", ""),
            "mpn": ref_json.get("mpn", ""),
            "manufacturer": ref_json.get("manufacturer", ""),
            "calibrationCertificateID": ref_json.get("calibrationCertificateID", ""),
            "issuedBy": ref_json.get("issuedBy", ""),
        }

    cal_result_entry["_sensor_schema_version"] = sensor_json.get("schemaVersion", "")
    cal_result_entry["_ref_schema_version"] = (ref_json or {}).get("schemaVersion", "")

    out["_calibration_result"] = cal_result_entry
    return out


def _run_calibration(procedure: str, payload: Dict, lsb_scale: Dict, sample_size: int,
                     adc_max: float, ub_ref_lsb: float, ub_sensor_lsb: float, verbose: bool,
                     risol: float, old_A, old_B, old_C, old_D,
                     sensor_json, ref_json, convert_units: bool,
                     unit_symbol: str = "°C",
                     formula: str | None = None,
                     formula_vars: Dict[str, float] | None = None,
                     ufit: float | None = None,
                     coverage_factor: float = 2.0):
    ub_ref_y = ub_ref_lsb   # caller passes reference uncertainty in Y
    unit_kwargs = dict(
        sensor_json=sensor_json, ref_json=ref_json,
        convert_units=convert_units,
        unit_symbol=unit_symbol,
    )
    formula_kwargs = dict(formula=formula, formula_vars=formula_vars)
    ufit_kwargs = dict(ufit=ufit)
    if procedure == "linear":
        from model_calibration.linear_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
            verbose=verbose, risol=risol,
            old_a=old_A, old_b=old_B, **unit_kwargs, **formula_kwargs, **ufit_kwargs,
            coverage_factor=coverage_factor,
        )
    elif procedure == "cubic":
        from model_calibration.cubic_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
            verbose=verbose, risol=risol,
            old_a=old_A, old_b=old_B, old_c=old_C, old_d=old_D, **unit_kwargs, **formula_kwargs, **ufit_kwargs,
            coverage_factor=coverage_factor,
        )
    elif procedure == "quadratic":
        from model_calibration.quadratic_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
            verbose=verbose, risol=risol,
            old_a=old_A, old_b=old_B, old_c=old_C, **unit_kwargs, **formula_kwargs, **ufit_kwargs,
            coverage_factor=coverage_factor,
        )
    elif procedure == "steinhart":
        from model_calibration.steinhart_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
            verbose=verbose, risol=risol,
            old_a=old_A, old_b=old_B, old_c=old_C, **unit_kwargs, **formula_kwargs, **ufit_kwargs,
            coverage_factor=coverage_factor,
        )
    else:
        raise ValueError(
            f"Unknown procedure '{procedure}'. "
            "Supported: linear, cubic, quadratic, steinhart"
        )


def _apply_calibration_skipped(cert_filled: Dict, calib_result: Dict,
                                old_A, old_B, old_C, old_D, lsb_per_y: float):
    cal = cert_filled.get("_calibration_result", {})
    model = cal.get("_calib_model", "linear")
    lpc = cal.get("_lsb_per_y", lsb_per_y)

    if model == "linear":
        init_A = old_A if old_A is not None else 1.0
        init_B = old_B if old_B is not None else 0.0
        init_A_r = round_to_significant_figures(init_A, 4)
        init_B_r = round_to_significant_figures(init_B, 4)
        # B is now in °C directly — no lpc division
        cal.update({"_A": init_A, "_B": init_B,
                    "_u_A": 0.0, "_u_B": 0.0, "_cov_AB": 0.0})
        sm = cert_filled["template_parts"]["sensor_method_template"].get("sensor_model", {})
        sm.update({"_A_cal": init_A_r, "_B_cal": init_B_r,
                   "_u_A": 0.0, "_u_B": 0.0, "_cov_AB": 0.0})
        cert_filled["template_parts"]["sensor_method_template"]["sensor_model"] = sm
    elif model == "cubic":
        init_a0 = old_A if old_A is not None else 0.0
        init_a1 = old_B if old_B is not None else 1.0
        init_a2 = old_C if old_C is not None else 0.0
        init_a3 = old_D if old_D is not None else 0.0
        for section in (cal, cert_filled["template_parts"]["sensor_method_template"].get("sensor_model", {})):
            section.update({
                "_a0": init_a0, "_a1": init_a1, "_a2": init_a2, "_a3": init_a3,
                "_u_a0": 0.0, "_u_a1": 0.0, "_u_a2": 0.0, "_u_a3": 0.0,
            })
    elif model == "quadratic":
        init_a0 = old_A if old_A is not None else 0.0
        init_a1 = old_B if old_B is not None else 1.0
        init_a2 = old_C if old_C is not None else 0.0
        for section in (cal, cert_filled["template_parts"]["sensor_method_template"].get("sensor_model", {})):
            section.update({
                "_a0": init_a0, "_a1": init_a1, "_a2": init_a2,
                "_u_a0": 0.0, "_u_a1": 0.0, "_u_a2": 0.0,
            })
    elif model == "steinhart":
        init_a = old_A if old_A is not None else 0.0
        init_b = old_B if old_B is not None else 0.0
        init_c = old_C if old_C is not None else 0.0
        for section in (cal, cert_filled["template_parts"]["sensor_method_template"].get("sensor_model", {})):
            section.update({
                "_a": init_a, "_b": init_b, "_c": init_c,
                "_u_a": 0.0, "_u_b": 0.0, "_u_c": 0.0,
            })

    cert_filled["_calibration_result"] = cal

    # When calibration is skipped, M_e_post == M_e_pre
    if True:
        meas = cert_filled["template_parts"]["calculated_calibration_values"]["measurements"]
        for row in meas:
            row[4] = row[3]
            row[2] = row[1] + row[3]
        cert_filled["template_parts"]["calculated_calibration_values"]["measurements"] = meas
        cert_filled["template_parts"]["calculated_calibration_values"]["_measurements"] = meas


def main() -> None:
    default_input_json  = DATA_DIR    / "export2_tmp126_lsb16.json"
    default_sensor_json = SENSORS_DIR / "ntc_temperature.json"
    default_ref_json    = REFERENCES_DIR / "fluke_9142.json"
    default_cert_input  = TEMPLATE_DIR / "certificato_funzione_input.json"
    default_cert_output = OUT_DIR / "certificato_funzione_filled.json"
    default_pdf_output  = str(OUT_DIR / "ntc_cert_funzione.pdf")
    default_xml_output  = OUT_DIR / "ntc_calibration_certificate.xml"
    default_last_calib   = LAST_CALIB_DIR / "last_calibration.json"

    parser = argparse.ArgumentParser(
        description="NTC calibration orchestrator — reads LSB16 JSON, calibrates, generates certificate."
    )
    parser.add_argument("--input",   type=Path, default=default_input_json)
    parser.add_argument("--sensor",  type=Path, default=default_sensor_json)
    parser.add_argument("--ref",     type=Path, default=default_ref_json)
    parser.add_argument("--cert-input",  type=Path, default=default_cert_input)
    parser.add_argument("--cert-output", type=Path, default=default_cert_output)
    parser.add_argument("--pdf",  type=str, default=default_pdf_output)
    parser.add_argument("--xml",  type=Path, default=default_xml_output)
    parser.add_argument("--last-calibration", type=Path, default="mariotto.json",
        help="Read previous calibration result JSON (input: old coefficients, rmse_pre for ufit). "
             "If the file exists it is loaded and used as the as-found baseline for the current run.")
    parser.add_argument("--result-calibration", type=Path, default=default_last_calib,
        help="Write full calibration result JSON (coefficients, uncertainties, per-step budget, measurements) for downstream consumers")
    parser.add_argument("--conformity-output", type=Path, default=None)
    parser.add_argument("--images-dir", type=Path, default=None,
        help="Override base directory for plot images (replaces IMAGES_CALIB_DIR/IMAGES_CONFORM_DIR). "
             "Subfolders 'calibration' and 'conformity' will be created inside.")
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-pdf",  action="store_true", default=False)
    parser.add_argument("--no-xml",  action="store_true", default=False)
    parser.add_argument(
        "--procedure", type=str, default=None,
        choices=["linear", "cubic", "quadratic", "steinhart"],
    )
    parser.add_argument(
        "--update-parameters", type=str, default="none",
        choices=["none", "always", "if-out-of-tolerance"],
        help="Parameter update strategy: none (do not adjust), always (adjust regardless), if-out-of-tolerance (adjust only when as-found errors exceed limits)",
    )
    parser.add_argument("--check-units",   action=argparse.BooleanOptionalAction, default=False,
        help="(deprecated — unit checks now run automatically when model JSONs are provided)")
    parser.add_argument("--convert-units", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--charts-interactive", action="store_true", default=False,
        help="Show charts interactively via matplotlib (blocks until all windows are closed). "
             "Skips saving PNGs. Mutually usable with --charts (both save AND show). "
             "Requires a display / GUI backend (not suitable for headless/Docker runs)."
    )
    parser.add_argument(
        "--tolerance", type=float, default=None,
        help="Override the sensor's maxTollerance (Check G as-found accuracy limit). "
             "When omitted, the value is read from the sensor JSON (Uncertainty[varName=maxTollerance] "
             "or legacy sensorAccuracy[0].maxError).",
    )
    parser.add_argument("--mae-y", type=float, default=0.30,
        help="Maximum Acceptable Error for Check H (default: 0.30)")
    parser.add_argument("--pfa-threshold-pct", type=float, default=20.0,
        help="PFA threshold percentage for Check H (default: 20.0)")
    parser.add_argument("--pfa-u-std-mode", type=str, default="combined",
        choices=["combined", "type_a"],
        help="Uncertainty mode for PFA computation: combined (u_exp/k) or type_a (uA_sensor) (default: combined)")
    parser.add_argument("--u-ref", type=float, default=0.065,
        help="Reference expanded uncertainty U_ref (k=2) for overlap check (default: 0.065)")
    # Previous calibration coefficients injected by the orchestrator from the sensor DB record.
    # When provided they override coeffA/B/C/D read from the sensor JSON (which may be 0.0 = unset).
    # linear: --old-a = A,  --old-b = B
    # cubic:  --old-a = a0, --old-b = a1, --old-c = a2, --old-d = a3
    parser.add_argument("--old-a", type=float, default=None, help="Previous calibration coefficient A (overrides sensor JSON coeffA if provided)")
    parser.add_argument("--old-b", type=float, default=None, help="Previous calibration coefficient B (overrides sensor JSON coeffB if provided)")
    parser.add_argument("--old-c", type=float, default=None, help="Previous calibration coefficient C / a2 / C3 (overrides sensor JSON coeffC if provided)")
    parser.add_argument("--old-d", type=float, default=None, help="Previous calibration coefficient D / a3 (overrides sensor JSON coeffD if provided)")
    args = parser.parse_args()

    # Run-unique seed stamped on every generated image and in the logs.
    _run_seed = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    print(f"Run seed: {_run_seed}")

    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass

    # Allow --images-dir to override the default image output directories
    _images_calib_dir  = IMAGES_CALIB_DIR
    _images_conform_dir = IMAGES_CONFORM_DIR
    if args.images_dir is not None:
        _images_calib_dir  = args.images_dir / "calibration"
        _images_conform_dir = args.images_dir / "conformity"

    if args.charts:
        _images_calib_dir.mkdir(parents=True, exist_ok=True)
        _images_conform_dir.mkdir(parents=True, exist_ok=True)

    # load sensor and reference JSON directly
    sensor_json = json.loads(Path(args.sensor).read_text(encoding="utf-8"))
    ref_json    = json.loads(Path(args.ref).read_text(encoding="utf-8"))

    _phys_dsi = sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    _unit_sym  = dsi_to_symbol(_phys_dsi)
    _tgt_dsi   = sensor_json.get("unit", _phys_dsi)
    _tgt_unit_sym = dsi_to_symbol(_tgt_dsi)
    _measurand = sensor_json.get("type", "temperature").capitalize()

    _cert_unit_sym = _tgt_unit_sym if args.convert_units else _unit_sym

    print(f"measurand:    {_measurand}")
    print(f"unit:         {_unit_sym}")
    if args.convert_units and _tgt_unit_sym != _unit_sym:
        print(f"target unit:  {_tgt_unit_sym}")
    if args.verbose:
        print(f"sensor model: {args.sensor}")
        print(f"ref model:    {args.ref}")
        print(f"cert-input:  {args.cert_input}")
        print(f"cert-output: {args.cert_output}")
        print(f"pdf output:  {args.pdf}")
        print(f"xml output:  {args.xml}")

    adc_bits  = sensor_json.get("ranges", {}).get("elec", {}).get("adcBits", 16)
    adc_max   = float((1 << adc_bits) - 1)

    lsb_min = float(sensor_json.get("ranges", {}).get("threshold", {}).get("min", -40.0))
    lsb_max = float(sensor_json.get("ranges", {}).get("threshold", {}).get("max", 105.0))
    print(f"LSB scale range: [{lsb_min}, {lsb_max}] {_unit_sym}")
    lsb_per_y = adc_max / (lsb_max - lsb_min)   # informational

    sensor_metrology = sensor_json.get("metrology", {})
    ref_metrology = ref_json.get("metrology", {})

    # Reference (PT100/Fluke) type-B standard uncertainty in native °C
    ref_unc_list = ref_metrology.get("Uncertainty", [])
    ub_ref_y = float(ref_unc_list[0].get("ub", 0.0)) if ref_unc_list else 0.0

    # NTC ADC type-B standard uncertainty [LSB] from sensor JSON (uB value, already /k)
    sensor_reading_uncertainty = sensor_metrology.get("readingUncertainty", [])
    ub_sensor_lsb = float(_lookup(sensor_reading_uncertainty, "varName", "uB", {}).get("value", 0.30))

    # Calibration fitting uncertainty (declared by sensor manufacturer) [°C]
    # _ufit_val = float(_lookup(sensor_reading_uncertainty, "varName", "ufit", {}).get("value", 0)) #old
    _metrology_uncertainty = sensor_metrology.get("Uncertainty", [])
    _ufit_val = float(_lookup(_metrology_uncertainty, "varName", "ufit", {}).get("value", 0))
    ufit = _ufit_val if _ufit_val > 0 else None

    # Informational: sum of absolute uncertainties for certificate page 4
    sensor_abs_lsb = float(_lookup(sensor_reading_uncertainty, "varName", "absUncertainty", {}).get("value", 5.0))
    sensor_abs_y     = sensor_abs_lsb / lsb_per_y
    abs_unc_sum_y = ub_ref_y + sensor_abs_y

    # Read evaluationFormula from JSON (vars built later after coefficient resolution)
    _eval_formula_raw = sensor_metrology.get("evaluationFormula", "")
    _formula_str: str | None = _eval_formula_raw.strip() if _eval_formula_raw else None
    if args.verbose and _formula_str:
        print(f"evaluationFormula: {_formula_str}")

    sample_size = 20
    lsb_scale   = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}

    payload = json.loads(args.input.read_text(encoding="utf-8"))

    _pp_info = apply_preprocessing_to_payload(
        payload, sensor_json=sensor_json, verbose=args.verbose,
    )

    if args.verbose:
        print(f"Input JSON (LSB16): {args.input}")
        print(f"Calibration procedure: {sensor_json.get('calibration', {}).get('type', 'linear')}")
        print(f"LSB scale (informational): [{lsb_min}, {lsb_max}] {_unit_sym}  ({lsb_per_y:.4f} LSB/{_unit_sym})")
        print(f"ub_ref    = {ub_ref_y:.6f} {_unit_sym}  (reference type-B)")
        print(f"ub_sensor = {ub_sensor_lsb:.4f} LSB  (sensor ADC type-B)")
        print(f"sensor abs:   {sensor_abs_lsb:.4f} LSB  {sensor_abs_y:.6f} {_unit_sym}  (informational)")
        print(f"sum abs ({_unit_sym}): {abs_unc_sum_y:.6f}")

    _PROCEDURE_ALIASES: dict = {
        "qubic-interpolation": "linear",
    }

    _json_procedure = sensor_json.get("calibration", {}).get("type", "linear").strip().lower()

    if args.procedure is not None:
        procedure = args.procedure.strip().lower()
        if args.verbose:
            print(f"INFO: --procedure override '{procedure}' -> replacing JSON '{_json_procedure}'.")
    else:
        procedure = _json_procedure

    if procedure in _PROCEDURE_ALIASES:
        mapped = _PROCEDURE_ALIASES[procedure]
        if args.verbose:
            print(f"INFO: mapping '{procedure}' -> '{mapped}'.")
        procedure = mapped

    if procedure not in ("linear", "cubic", "quadratic", "steinhart"):
        print(f"WARNING: Unknown procedure '{procedure}', falling back to JSON default '{_json_procedure}'.", file=sys.stderr)
        procedure = _json_procedure
        if procedure in _PROCEDURE_ALIASES:
            procedure = _PROCEDURE_ALIASES[procedure]

    # Resolve previous calibration coefficients.
    # Priority: CLI --old-a/b/c/d (injected from DB by dcc_service) > sensor JSON coeffA/B/C/D.
    # The 0.0 sentinel means "not set" — treat as None so engines use their identity defaults.
    def _coeff_from_json(val: float) -> "float | None":
        return val if val != 0.0 else None

    # Read old coefficients from --last-calibration JSON (if file exists).
    # Source precedence: CLI --old-* > last-calibration file > sensor JSON coeff* > None.
    # Two input schemas are supported:
    #   (a) Comprehensive JSON written by a previous run of this script:
    #         { "model": "linear"|"cubic", "coefficients": { "A","B",... or "a0","a1",... } }
    #   (b) Filled certificate JSON containing a top-level "_calibration_result" block
    #       (e.g. last_calibration/simulated_cubic.json): keys prefixed with "_"
    #         { "_calibration_result": { "_calib_model": "linear"|"cubic",
    #                                    "_A","_B"      for linear,
    #                                    "_a0","_a1","_a2","_a3" for cubic } }
    _last_calib_old: Dict[str, Any] = {}
    if args.last_calibration is not None and args.last_calibration.exists():
        try:
            _last_calib_data = json.loads(args.last_calibration.read_text(encoding="utf-8"))

            # (b) _calibration_result block (strip leading "_" from keys)
            _calib_result_block = _last_calib_data.get("_calibration_result")
            if isinstance(_calib_result_block, dict):
                _last_coeffs = {k.lstrip("_"): v for k, v in _calib_result_block.items()}
                _last_model = _calib_result_block.get("_calib_model", "linear")
            else:
                # (a) comprehensive JSON written by a previous run
                _last_coeffs = _last_calib_data.get("coefficients", {}) or {}
                _last_model = _last_calib_data.get("model", "linear")

            if args.verbose:
                _summary_keys = (
                    "model", "lsb_per_y", "fit_quality",
                    "coefficients", "old_coefficients",
                    "temp_nominali", "ref_temp_means", "expanded_uncertainties",
                )
                _summary = {k: _last_calib_data[k] for k in _summary_keys if k in _last_calib_data}
                if isinstance(_calib_result_block, dict):
                    _whitelist = {
                        "calib_model", "A", "B", "a0", "a1", "a2", "a3",
                        "u_A", "u_B", "u_a0", "u_a1", "u_a2", "u_a3",
                        "rmse", "rmse_pre", "lsb_per_y",
                        "temp_nominali", "ref_temp_means", "expanded_uncertainties",
                        "cov_AB", "cov_theta",
                    }
                    _summary["_calibration_result"] = {
                        k.lstrip("_"): v for k, v in _calib_result_block.items()
                        if k.lstrip("_") in _whitelist
                    }
                print("=== --last-calibration: loaded previous calibration ===")
                print(f"  source file:    {args.last_calibration}")
                print(f"  detected model: {_last_model}")
                print(f"  summary:        {json.dumps(_summary, indent=2, ensure_ascii=False, default=str)}")

            if _last_model == "linear":
                _last_calib_old = {
                    "A": _last_coeffs.get("A"),
                    "B": _last_coeffs.get("B"),
                    "C": None,
                    "D": None,
                }
            elif _last_model == "cubic":
                _last_calib_old = {
                    "A": _last_coeffs.get("a0"),
                    "B": _last_coeffs.get("a1"),
                    "C": _last_coeffs.get("a2"),
                    "D": _last_coeffs.get("a3"),
                }
            elif _last_model == "quadratic":
                _last_calib_old = {
                    "A": _last_coeffs.get("a0"),
                    "B": _last_coeffs.get("a1"),
                    "C": _last_coeffs.get("a2"),
                    "D": None,
                }
            elif _last_model == "steinhart":
                _last_calib_old = {
                    "A": _last_coeffs.get("a"),
                    "B": _last_coeffs.get("b"),
                    "C": _last_coeffs.get("c"),
                    "D": None,
                }
        except Exception as _exc:
            if args.verbose:
                print(f"WARNING: failed to read --last-calibration {args.last_calibration}: {_exc}")

    def _src_for(key: str) -> str:
        cli_attr = f"old_{key.lower()}"
        if getattr(args, cli_attr, None) is not None:
            return f"CLI --old-{key.lower()}"
        if _last_calib_old.get(key) is not None:
            return f"--last-calibration {args.last_calibration.name}"
        if _get_calib_coeff(sensor_json, key) != 0.0:
            return f"sensor JSON coeff{key}"
        return "identity (first calibration)"

    def _resolve_old(key: str) -> "float | None":
        cli_attr = f"old_{key.lower()}"
        if getattr(args, cli_attr, None) is not None:
            return getattr(args, cli_attr)
        if _last_calib_old.get(key) is not None:
            v = _last_calib_old[key]
            return float(v) if v is not None else None
        return _coeff_from_json(_get_calib_coeff(sensor_json, key))

    old_A: float | None = _resolve_old("A")
    old_B: float | None = _resolve_old("B")
    old_C: float | None = _resolve_old("C")
    old_D: float | None = _resolve_old("D")

    if args.verbose:
        print("=== Previous results (as-found baseline) ===")
        print(f"  old_A = {old_A}  [source: {_src_for('A')}]")
        print(f"  old_B = {old_B}  [source: {_src_for('B')}]")
        print(f"  old_C = {old_C}  [source: {_src_for('C')}]")
        print(f"  old_D = {old_D}  [source: {_src_for('D')}]")
        print(f"  ufit  = {ufit}  [source: {'sensor JSON' if ufit else 'not set'}]")

    # Build formula variables from readingUncertainty and calibration coefficients
    _formula_vars: Dict[str, float] | None = None
    if _formula_str:
        from evaluation_formula import build_formula_variables
        _coeffs: Dict[str, float] = {}
        for key, val in (("A", old_A), ("B", old_B), ("C", old_C), ("D", old_D)):
            if val is not None and val != 0.0:
                _coeffs[key] = float(val)
        _formula_vars = build_formula_variables(sensor_reading_uncertainty, _coeffs)
        if args.verbose:
            print(f"formula vars: {_formula_vars}")

    _k_cov = _get_coverage_factor(sensor_json)
    try:
        calib_result = _run_calibration(
            procedure=procedure, payload=payload, lsb_scale=lsb_scale,
            sample_size=sample_size, adc_max=adc_max,
            ub_ref_lsb=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
            verbose=args.verbose, risol=float(_lookup(sensor_reading_uncertainty, "varName", "resolution", {}).get("value", 1)) / lsb_per_y,
            old_A=old_A, old_B=old_B, old_C=old_C, old_D=old_D,
            sensor_json=sensor_json, ref_json=ref_json,
            convert_units=args.convert_units,
            unit_symbol=_unit_sym,
            formula=_formula_str, formula_vars=_formula_vars,
            ufit=ufit,
            coverage_factor=_k_cov,
        )
        calib_result["run_seed"] = _run_seed
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # sensor accuracy gate
    calibration_skipped = False
    max_tollerance = (
        args.tolerance if args.tolerance is not None
        else _get_max_tollerance(sensor_json)
    )
    if args.tolerance is not None and args.verbose:
        print(
            f"[INFO] --tolerance override active: using {max_tollerance} "
            "instead of sensor JSON maxTollerance for Check G."
        )
    checker = SensorAccuracyChecker(max_tollerance) if max_tollerance is not None else None

    if checker is not None:
        temp_nominali_cr = calib_result.get("temp_nominali") or calib_result.get("steps", [])
        risultati_cr     = calib_result["risultati_elaborati"]
        ref_means_cr     = calib_result["ref_temp_means"]
        proc_model       = calib_result.get("model", "linear")

        as_found_errors: List[float] = []
        for _i, (_t, _ref_t) in enumerate(zip(temp_nominali_cr, ref_means_cr)):
            pmean_sensor_cr = risultati_cr[_t]["pmean_sensor"]
            if proc_model == "linear" and old_A is not None and old_B is not None:
                t_sensor_pre = old_A * pmean_sensor_cr + old_B
            elif proc_model == "cubic" and all(v is not None for v in [old_A, old_B, old_C, old_D]):
                from model_calibration.cubic_calibration import cubic_predict
                old_theta = np.array([old_A, old_B, old_C, old_D], dtype=float)
                t_sensor_pre = cubic_predict(float(pmean_sensor_cr), old_theta)
            elif proc_model == "quadratic" and all(v is not None for v in [old_A, old_B, old_C]):
                from model_calibration.quadratic_calibration import quadratic_predict
                old_theta = np.array([old_A, old_B, old_C], dtype=float)
                t_sensor_pre = quadratic_predict(float(pmean_sensor_cr), old_theta)
            elif proc_model == "steinhart" and all(v is not None for v in [old_A, old_B, old_C]):
                from model_calibration.steinhart_calibration import steinhart_predict_sh
                _R_cr = float(pmean_sensor_cr)
                old_theta = np.array([old_A, old_B, old_C], dtype=float)
                t_sensor_pre = steinhart_predict_sh(_R_cr, old_theta)
            else:
                t_sensor_pre = lsb_to_y(pmean_sensor_cr, lsb_scale, adc_max)
            as_found_errors.append(t_sensor_pre - _ref_t)

        accuracy_check = checker.check_all_points(ref_means_cr, as_found_errors)
        calib_result["_sensor_accuracy_check"] = accuracy_check

        if args.update_parameters == "none":
            print(
                "\n[INFO] --update-parameters=none: "
                "Parameter adjustment disabled by user. Keeping the previous "
                "(as-found) coefficients unchanged.\n"
                "       Result flag: calibration_done = 'not_necessary'"
            )
            calib_result["calibration_done"] = "not_necessary"
            calibration_skipped = True
        elif args.update_parameters == "always":
            print(
                "\n[INFO] --update-parameters=always: "
                "Forcing calibration parameter update regardless of as-found errors."
            )
            calib_result["calibration_done"] = "done"
        elif accuracy_check["all_in_range"]:
            print(
                "\n[INFO] --update-parameters=if-out-of-tolerance: "
                "ALL as-found errors are within the declared sensorAccuracy limits.\n"
                "       Calibration parameter update is NOT necessary.\n"
                "       Result flag: calibration_done = 'not_necessary'"
            )
            calib_result["calibration_done"] = "not_necessary"
            calibration_skipped = True
        else:
            failed = [p for p in accuracy_check["per_point"] if not p["in_range"]]
            print(
                f"\n[INFO] --update-parameters=if-out-of-tolerance: "
                f"{len(failed)} as-found error(s) exceed sensorAccuracy limits. "
                "Calibration will proceed."
            )
            for p in failed:
                print(
                    f"       Point {p['point']}: ref={p['T_ref_y']:.4f} {_unit_sym}  "
                    f"as-found={p['as_found_error_y']:+.6f} {_unit_sym}  "
                    f"limit=±{p['max_allowed_error_y']:.4f} {_unit_sym}  => OUT OF RANGE"
                )
            calib_result["calibration_done"] = "done"
    else:
        calib_result["_sensor_accuracy_check"] = None
        # No checker available (sensor JSON lacks maxTollerance and no
        # --tolerance override) — fall back to args.update_parameters alone.
        if args.update_parameters == "none":
            print(
                "\n[INFO] --update-parameters=none: "
                "Parameter adjustment disabled by user (no accuracy checker available).\n"
                "       Result flag: calibration_done = 'not_necessary'"
            )
            calib_result["calibration_done"] = "not_necessary"
            calibration_skipped = True
        else:
            calib_result["calibration_done"] = "done"

    if args.convert_units and args.verbose:
        conv = calib_result.get("converted", {})
        units = calib_result.get("units", {})
        cerr = calib_result.get("conversion_errors", [])
        if conv:
            print("\n=== Unit conversion results ===")
            for k, v in conv.items():
                print(f"  {k}: {v}  [{units.get(k, '?')}]")
        if cerr:
            print("[convert-units] Warnings:")
            for e in cerr:
                print(f"  {e}")

    if args.verbose:
        print("\n=== Calibration result ===")
        model = calib_result.get("model", procedure)
        if model == "linear":
            summary = {
                "model": model,
                "A": calib_result["A"], "B": calib_result["B"],
                "u_A": calib_result["u_A"], "u_B": calib_result["u_B"],
                "cov_AB": calib_result["cov_AB"],
                "expanded_uncertainties": calib_result["expanded_uncertainties"],
            }
        elif model == "cubic":
            summary = {
                "model": model,
                "a0": calib_result["a0"], "a1": calib_result["a1"],
                "a2": calib_result["a2"], "a3": calib_result["a3"],
                "u_a0": calib_result["u_a0"], "u_a1": calib_result["u_a1"],
                "u_a2": calib_result["u_a2"], "u_a3": calib_result["u_a3"],
                "expanded_uncertainties": calib_result["expanded_uncertainties"],
            }
        elif model == "quadratic":
            summary = {
                "model": model,
                "a0": calib_result["a0"], "a1": calib_result["a1"],
                "a2": calib_result["a2"],
                "u_a0": calib_result["u_a0"], "u_a1": calib_result["u_a1"],
                "u_a2": calib_result["u_a2"],
                "expanded_uncertainties": calib_result["expanded_uncertainties"],
            }
        elif model == "steinhart":
            summary = {
                "model": model,
                "a": calib_result["a"], "b": calib_result["b"],
                "c": calib_result["c"],
                "u_a": calib_result["u_a"], "u_b": calib_result["u_b"],
                "u_c": calib_result["u_c"],
                "expanded_uncertainties": calib_result["expanded_uncertainties"],
            }
        print(json.dumps(summary, indent=2))

        calc_interp_unc  = max(calib_result.get("expanded_uncertainties", [0.0]))
        fixed_interp_unc = round_to_significant_figures(abs_unc_sum_y, 2)
        print(f"\n=== Interpolation uncertainty check [{_unit_sym}] ===")
        # print(f"fixed:      {fixed_interp_unc:.6f}")
        print(f"calculated: {calc_interp_unc:.6f}")
        print(f"difference: {calc_interp_unc - fixed_interp_unc:+.6f}")

    cert_input_data = json.loads(args.cert_input.read_text(encoding="utf-8"))
    cert_filled = _build_cert_filled(
        cert_input=cert_input_data,
        sensor_json=sensor_json,
        calib_result=calib_result,
        adc_max=adc_max,
        lsb_scale=lsb_scale,
        ref_json=ref_json,
        unit_symbol=_cert_unit_sym,
    )

    cert_filled["_calibration_done"] = calib_result.get("calibration_done", "done")
    cert_filled["_sensor_accuracy_check"] = calib_result.get("_sensor_accuracy_check")

    # ── R18: output boundary validation — verify measurements are in physical units, not LSB ──
    _validate_output_domain(cert_filled, sensor_json, _cert_unit_sym)

    if calibration_skipped:
        _apply_calibration_skipped(cert_filled, calib_result, old_A, old_B, old_C, old_D, lsb_per_y)

    # ── R18: write full calibration result JSON for downstream consumers ──
    # Only written when a parameter adjustment was actually applied this
    # run — the file's "coefficients" block is meant to seed the NEXT
    # calibration's as-found baseline, and when no adjustment happened
    # there are no new coefficients to hand down: the next run must keep
    # falling back to the current baseline (last real adjustment, or the
    # sensor JSON template coefficients if there never was one).
    if args.result_calibration is not None:
        if calibration_skipped:
            if args.result_calibration.exists():
                args.result_calibration.unlink()
                print(
                    f"[INFO] No parameter adjustment this run "
                    f"(calibration_done={calib_result.get('calibration_done')}) — "
                    f"removed stale {args.result_calibration} from a previous attempt."
                )
            elif args.verbose:
                print(
                    f"[INFO] No parameter adjustment this run — "
                    f"{args.result_calibration} not written."
                )
        else:
            last_calib_json = _build_last_calib_json(calib_result, cert_filled)
            args.result_calibration.parent.mkdir(parents=True, exist_ok=True)
            args.result_calibration.write_text(
                json.dumps(last_calib_json, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            if args.verbose:
                print(
                    f"Calibration result JSON written to: {args.result_calibration}  "
                    f"(calibration_done={calib_result.get('calibration_done')})"
                )

    if args.verbose:
        print("\n=== Measurements (full FP precision) ===")
        for i, row in enumerate(cert_filled["template_parts"]["calculated_calibration_values"]["measurements"]):
            print(
                f"  row[{i}]: point={row[0]}, ref={row[1]:.10f} {_cert_unit_sym}, "
                f"post={row[2]:.10f} {_cert_unit_sym}, M_e_pre={row[3]:.10f} {_cert_unit_sym}, "
                f"M_e_post={row[4]:.10f} {_cert_unit_sym}, U_exp={row[5]:.10f} {_cert_unit_sym}"
            )

    args.cert_output.parent.mkdir(parents=True, exist_ok=True)
    args.cert_output.write_text(
        json.dumps(cert_filled, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if args.verbose:
        print(f"Certificate JSON written to: {args.cert_output}")

    try:
        import checks_helper as _checks

        filled_data = json.loads(args.cert_output.read_text(encoding="utf-8"))
        calib_cr      = _checks.extract_calib(filled_data)
        measurements  = _checks.extract_measurements(filled_data)

        limit_y    = _get_abs_uncertainty(sensor_json)
        conf_model    = calib_cr.get("_calib_model", "linear")

        sG, rG = _checks.check_G(measurements, max_tollerance, conf_model, verbose=False)
        sA, rA = _checks.check_A(measurements, verbose=False)
        sB, rB = _checks.check_B(measurements, limit_y, verbose=False)

        u_budget_conf = calib_cr.get("_u_budget_per_step", [])
        sH, rH = _checks.check_H(
            measurements, mae_y=args.mae_y,
            pfa_threshold_pct=args.pfa_threshold_pct,
            verbose=args.verbose, u_std_mode=args.pfa_u_std_mode,
            u_budget_per_step=u_budget_conf,
            adc_bits=adc_bits, adc_max=adc_max,
            coverage_factor=_get_coverage_factor(sensor_json),
        )

        conformity_summary = {
            "G": sG, "A": sA, "B": sB, "H": sH,
            "calibration_done": calib_result.get("calibration_done", "done"),
            "overall": (
                "COMPLIANT"
                if all(s == "PASS" for s in [sG, sA, sB, sH])
                else "NON-COMPLIANT"
            ),
        }

        if args.verbose:
            print("\n=== Conformity check ===")
            for k, v in conformity_summary.items():
                if k in ("calibration_done", "overall"):
                    continue
                print(f"  [{k}] {v}")
            pfa_vals = [r["PFA_pct"] for r in rH] if isinstance(rH, list) else []
            if pfa_vals:
                print(
                    "  [H] PFA by point: "
                    + "  ".join(f"P{r['punto']}={r['PFA_pct']:.1f}%" for r in rH)
                )
            print(
                f"  [H] MAE={args.mae_y:.3f}{_unit_sym}  "
                f"threshold={args.pfa_threshold_pct:.0f}%  "
                f"u_std_mode={args.pfa_u_std_mode}"
            )

        if args.conformity_output is not None:
            conformity_data = {
                "summary": conformity_summary,
                "check_G": rG, "check_A": rA, "check_B": rB, "check_H": rH,
                "check_H_params": {
                    "mae_y": args.mae_y,
                    "pfa_threshold_pct": args.pfa_threshold_pct,
                    "u_std_mode": args.pfa_u_std_mode,
                },
            }
            args.conformity_output.parent.mkdir(parents=True, exist_ok=True)
            args.conformity_output.write_text(
                json.dumps(conformity_data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            if args.verbose:
                print(f"Conformity JSON written to: {args.conformity_output}")

        if (args.charts or args.charts_interactive) and measurements:
            from checks_helper import save_charts as save_conf_charts
            saved_conf = save_conf_charts(
                measurements=measurements,
                accuracy_ranges=max_tollerance,
                limit_y=limit_y,
                variant="funzione",
                output_dir=_images_conform_dir,
                unit_symbol=_cert_unit_sym,
            )
            if args.verbose:
                for p in saved_conf:
                    print(f"Conformity chart saved: {p}")

        _conformity_data = {
            "summary": conformity_summary,
            "check_G": rG, "check_A": rA, "check_B": rB, "check_H": rH,
            "check_H_params": {
                "mae_y": args.mae_y,
                "pfa_threshold_pct": args.pfa_threshold_pct,
                "u_std_mode": args.pfa_u_std_mode,
            },
            "guard_band": max_tollerance,
        }
        cert_filled["_calibration_result"]["_conformity"] = _conformity_data
        args.cert_output.write_text(
            json.dumps(cert_filled, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

    except Exception as ex:
        print(f"Conformity check error: {ex}", file=sys.stderr)

    if not args.no_pdf:
        try:
            import certificato_funzione as _cert_pdf
            _cert_pdf.configure_from_input(_cert_pdf.load_input_data(args.cert_output))
            _cert_pdf.build_pdf(args.pdf)
            if args.verbose:
                print(f"PDF certificate written to: {args.pdf}")
        except Exception:
            import subprocess
            result_proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "certificato_funzione.py"),
                 "--input", str(args.cert_output), "--output", str(args.pdf)],
                capture_output=True, text=True,
            )
            if result_proc.returncode != 0:
                print(f"PDF generation error:\n{result_proc.stderr}", file=sys.stderr)
            elif args.verbose:
                print(f"PDF certificate written to: {args.pdf}")

    if not args.no_xml:
        try:
            import generate_dcc_xml as _dcc_xml
            import io
            data = _dcc_xml.load_input_data(args.cert_output)
            tree = _dcc_xml.build_dcc_tree(data)
            buf  = io.BytesIO()
            tree.write(buf, encoding="utf-8", xml_declaration=False)
            header = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            args.xml.parent.mkdir(parents=True, exist_ok=True)
            args.xml.write_bytes(header + buf.getvalue())
            if args.verbose:
                print(f"DCC XML written to: {args.xml}")
        except Exception as ex:
            print(f"XML generation error: {ex}", file=sys.stderr)

    saved = []  # init here so conformity block can reference it even when --charts is off
    if args.charts:
        try:
            model = calib_result.get("model", procedure)

            # Derive display labels and unit from sensor/reference JSONs
            _sensor_lbl = sensor_json.get("name", sensor_json.get("deviceType", "Sensor"))
            _ref_lbl    = ref_json.get("name", ref_json.get("deviceType", "Reference"))
            _acc_limit  = _worst_accuracy_limit(max_tollerance)

            _common_kw = dict(
                unit_symbol=_unit_sym,
                measurand_label=_measurand,
                sensor_label=_sensor_lbl,
                ref_label=_ref_lbl,
                accuracy_limit=_acc_limit,
            )

            if model == "linear":
                from model_calibration.linear_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    a=calib_result["A"], b=calib_result["B"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    output_dir=_images_calib_dir,
                    _calib_result=calib_result,
                    **_common_kw,
                )
            elif model == "cubic":
                from model_calibration.cubic_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    output_dir=_images_calib_dir, cov_theta=calib_result["cov_theta"],
                    _calib_result=calib_result,
                    **_common_kw,
                )
            elif model == "quadratic":
                from model_calibration.quadratic_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    output_dir=_images_calib_dir, cov_theta=calib_result.get("cov_theta"),
                    _calib_result=calib_result,
                    **_common_kw,
                )
            elif model == "steinhart":
                from model_calibration.steinhart_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    output_dir=_images_calib_dir, cov_theta=calib_result.get("cov_theta"),
                    _calib_result=calib_result,
                    **_common_kw,
                )
            else:
                saved = []
            if args.verbose:
                for p in saved:
                    print(f"Calibration chart saved: {p}")
        except Exception as ex:
            import traceback
            print(f"Calibration chart save error: {ex}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()

    if args.charts_interactive:
        try:
            model = calib_result.get("model", procedure)
            if args.verbose:
                print("\n[interactive] Opening calibration charts — close the window(s) to continue.")

            if model == "linear":
                from model_calibration.linear_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    a=calib_result["A"], b=calib_result["B"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                )
            elif model == "cubic":
                from model_calibration.cubic_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    cov_theta=calib_result["cov_theta"],
                )
            elif model == "quadratic":
                from model_calibration.quadratic_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    cov_theta=calib_result.get("cov_theta"),
                )
            elif model == "steinhart":
                from model_calibration.steinhart_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_ref_lsb=calib_result.get("ub_ref_lsb", ub_ref_y * lsb_per_y),
                    ub_sensor_lsb=ub_sensor_lsb,
                    cov_theta=calib_result.get("cov_theta"),
                )
        except Exception as ex:
            import traceback
            print(f"[interactive] Calibration chart error: {ex}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()


if __name__ == "__main__":
    main()
