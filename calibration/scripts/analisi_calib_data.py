from __future__ import annotations

import argparse
import copy
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
TEMPLATE_DIR       = CALIB_ROOT / "template_in"
DATA_DIR           = CALIB_ROOT / "data_in"
OUT_DIR            = CALIB_ROOT / "certificato_out"
TEST_DATA_DIR      = CALIB_ROOT / "test" / "data_in"
IMAGES_CALIB_DIR   = CALIB_ROOT / "images" / "calibration"
IMAGES_CONFORM_DIR = CALIB_ROOT / "images" / "conformity"

if str(MODELS_DIR) not in sys.path:
    sys.path.insert(0, str(MODELS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from VAR_REF_SENSOR import SENSOR_model, RIFERIMENTO_model, VAR_extra  # noqa: E402
from model_calibration.unit_checks import dsi_to_symbol, dsi_to_xml_unit  # noqa: E402
from calib_utils import SensorAccuracyChecker, lsb_to_degc, round_to_significant_figures  # noqa: E402


def _get_accuracy_ranges(sensor_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sensor_json.get("metrology", {}).get("sensorAccuracy", [])


def _worst_accuracy_limit(accuracy_ranges: List[Dict[str, Any]]) -> float | None:
    """Return the largest maxError across all accuracy ranges, or None if empty."""
    limits = [r.get("maxError") for r in accuracy_ranges if r.get("maxError") is not None]
    return float(max(limits)) if limits else None



def _build_cert_filled(
    cert_input: Dict[str, Any],
    sensor: SENSOR_model,
    calib_result: Dict[str, Any],
    adc_max: float,
    lsb_scale: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    out = copy.deepcopy(cert_input)
    tp = out["template_parts"]

    calib_model = calib_result.get("model", "linear")
    # Normalise step list key: regression models use "temp_nominali", interp models use "steps"
    temp_nominali: List[float] = calib_result.get("temp_nominali") or calib_result.get("steps", [])
    risultati_elaborati = calib_result["risultati_elaborati"]
    expanded_uncertainties: List[float] = calib_result["expanded_uncertainties"]
    ref_temp_means: List[float] = calib_result["ref_temp_means"]
    lsb_per_c: float = calib_result["lsb_per_c"]

    _s_json = sensor_json or {}
    _phys_dsi: str = _s_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    _phys_unit_symbol: str = dsi_to_symbol(_phys_dsi)
    _phys_unit_dsi: str = dsi_to_xml_unit(_phys_dsi)

    smt = tp["sensor_method_template"]
    smt["_notes_computed"] = [
        "Results refer to the instrument under calibration under the declared conditions.",
        "Uncertainties are determined according to ISO/IEC Guide 98-3 (GUM) and EA-4/02.",
        "Coverage factor k = 2, confidence level about 95 %.",
        "Reference instrument: Fluke 1502A thermometer readout + Pt100 probe (U=0.065 °C, k=2).",
        (
            f"Sensor under calibration: DIGITAL thermometer "
            f"({smt.get('manufacturer', '')}, model {smt.get('model', '')})."
        ),
    ]

    ntc_model = smt.get("ntc_model", {})
    ntc_model.update({
        "R25": sensor.R25,
        "B25_85": sensor.B25_85,
        "A_steinhart": sensor.A_steinhart,
        "B_steinhart": sensor.B_steinhart,
        "C_steinhart": sensor.C_steinhart,
        "alpha_25": sensor.alpha_25,
        "uncertainty_limit": sensor.uncertainty_limit,
        "calibration_procedure": sensor.calibrationProcedure,
        "calibration_formula": sensor.calibration_formula,
        "method_description": sensor.method_description,
        "formula_steinhart": sensor.formula_steinhart,
        "formula_beta": sensor.formula_beta,
        "observations": sensor.obs_list,
    })
    ntc_model["_calib_model"] = calib_model

    if calib_model == "linear":
        A = calib_result["A"]
        B = calib_result["B"]    # [°C] directly
        u_A = calib_result["u_A"]
        u_B = calib_result["u_B"]   # [°C] directly
        cov_AB = calib_result["cov_AB"]
        ntc_model.update({
            "_A_cal": A, "_B_cal": B, "_u_A": u_A, "_u_B": u_B, "_cov_AB": cov_AB,
            "_B_cal_phys": B, "_u_B_phys": u_B,
            "_B_cal_degC": B, "_u_B_degC": u_B,
        })
    elif calib_model == "cubic":
        ntc_model.update({
            "_theta": calib_result["theta"],
            "_a0": calib_result["a0"], "_a1": calib_result["a1"],
            "_a2": calib_result["a2"], "_a3": calib_result["a3"],
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"], "_u_a3": calib_result["u_a3"],
            "_cov_theta": calib_result["cov_theta"],
        })
    elif calib_model in ("linear_interp", "cubic_interp"):
        ntc_model.update({
            "_x_nodes": calib_result["x_nodes"],
            "_y_nodes": calib_result["y_nodes"],
            "_node_steps": calib_result.get("node_steps", []),
            "_rmse_degC": calib_result.get("rmse_degC"),
            "_u_H_degC": calib_result.get("u_H_degC"),
        })
    else:
        ntc_model.update({
            "_C0": calib_result["C0"], "_C1": calib_result["C1"], "_C3": calib_result["C3"],
            "_u_C0": calib_result["u_C0"], "_u_C1": calib_result["u_C1"],
            "_u_C3": calib_result["u_C3"], "_cov_theta": calib_result["cov_theta"],
        })

    smt["ntc_model"] = ntc_model

    measurements: List[List[float]] = []
    for i, t in enumerate(temp_nominali):
        pmean_log = risultati_elaborati[t]["pmean_log"]   # [LSB]
        ref_t = ref_temp_means[i]                          # [°C]

        # --- M_e_pre: as-found sensor temperature using previous calibration ---
        if calib_model == "linear":
            _prev_A = calib_result.get("old_A")
            _prev_B = calib_result.get("old_B")
            if _prev_A is not None and _prev_B is not None:
                _old_b_c = _prev_B / lsb_per_c if abs(_prev_B) > 300 else _prev_B
                t_sensor_pre = _prev_A * pmean_log + _old_b_c
                if abs(_prev_A) > 1.0:
                    t_sensor_pre = lsb_to_degc(_prev_A * pmean_log + _prev_B, lsb_scale, adc_max)
            else:
                t_sensor_pre = lsb_to_degc(pmean_log, lsb_scale, adc_max)
        elif calib_model == "cubic":
            _prev_a0 = calib_result.get("old_a0")
            _prev_a1 = calib_result.get("old_a1")
            _prev_a2 = calib_result.get("old_a2")
            _prev_a3 = calib_result.get("old_a3")
            if all(v is not None for v in [_prev_a0, _prev_a1, _prev_a2, _prev_a3]):
                from model_calibration.cubic_calibration import cubic_predict
                _old_theta = np.array([_prev_a0, _prev_a1, _prev_a2, _prev_a3], dtype=float)
                t_sensor_pre = cubic_predict(float(pmean_log), _old_theta)
            else:
                t_sensor_pre = lsb_to_degc(pmean_log, lsb_scale, adc_max)
        else:
            # interp models and cube-log: no previous coefficients concept — show raw reading
            t_sensor_pre = lsb_to_degc(pmean_log, lsb_scale, adc_max)

        # --- M_e_post: post-calibration sensor temperature using new coefficients ---
        if calib_model == "linear":
            t_sensor_post = A * pmean_log + B
        elif calib_model == "cubic":
            from model_calibration.cubic_calibration import cubic_predict
            t_sensor_post = cubic_predict(float(pmean_log), np.array(calib_result["theta"]))
        elif calib_model in ("linear_interp", "cubic_interp"):
            # Read predicted value directly from the per_step_budget entry for this step
            _budget = calib_result.get("per_step_budget", [])
            _budget_by_step = {b["t_nominal"]: b for b in _budget}
            _b = _budget_by_step.get(t)
            if _b is not None:
                # residual_degC = y_hat - y_ref  →  t_sensor_post = ref_t + residual
                t_sensor_post = ref_t + _b["residual_degC"]
            else:
                t_sensor_post = ref_t  # fallback: zero error
        else:
            from model_calibration.cube_log_calibration import steinhart_hart_predict_degc
            try:
                t_sensor_post = steinhart_hart_predict_degc(float(pmean_log), np.array(calib_result["theta"]))
            except ValueError:
                t_sensor_post = float("nan")

        error_pre = t_sensor_pre - ref_t
        error_post = t_sensor_post - ref_t
        print(
            f"[{calib_model}] row[{i}] point={i + 1} "
            f"T_sensor pre={t_sensor_pre:.10f}°C post={t_sensor_post:.10f}°C | "
            f"error pre={error_pre:.10f}°C post={error_post:.10f}°C"
        )

        measurements.append([
            float(i + 1), ref_t, t_sensor_post, error_pre, error_post, expanded_uncertainties[i],
        ])

    tp["calculated_calibration_values"] = {
        "_measurements": measurements,
        "measurements": measurements,
        "_observations": sensor.obs_list,
        "observations": sensor.obs_list,
        "conclusions": "Expanded uncertainty U(E) with coverage factor k = 2, confidence level about 95 %.",
    }

    # Interpolation uncertainty: Fluke type-B + NTC abs uncertainty.
    # ub_pt_degc is now stored in calib_result; ntc abs is in LSB converted via lsb_per_c.
    fluke_abs_c = float(calib_result.get("ub_pt_degc", calib_result.get("ub_pt_lsb", 0.0) / lsb_per_c))
    ntc_abs_c = float(sensor.absUncertainty) / lsb_per_c
    interp_sum_degc = fluke_abs_c + ntc_abs_c
    interp_fixed_2sig = round_to_significant_figures(interp_sum_degc, 2)

    cal_result_entry: Dict[str, Any] = {
        "_calib_model": calib_model,
        "_calibration_procedure": sensor.calibrationProcedure,
        "_method_description": sensor.method_description,
        "_lsb_per_c": lsb_per_c,
        "_adc_bits": int(round(math.log2(adc_max + 1.0))),
        "_phys_unit_symbol": _phys_unit_symbol,
        "_phys_unit_dsi": _phys_unit_dsi,
        "_expanded_uncertainties": expanded_uncertainties,
        "_interp_unc_sum_abs": interp_sum_degc,
        "_interp_unc_fixed_2sig": interp_fixed_2sig,
        "_ref_temp_means": ref_temp_means,
        "_temp_nominali": temp_nominali,
        "_variant": "funzione",
        # backward-compat aliases
        "_expanded_uncertainties_degC": expanded_uncertainties,
        "_interp_unc_sum_abs_degC": interp_sum_degc,
        "_interp_unc_fixed_2sig_degC": interp_fixed_2sig,
        "_ref_temp_means_degC": ref_temp_means,
    }

    if calib_model == "linear":
        cal_result_entry.update({
            "_A": A, "_B": B,
            "_B_phys": B, "_u_B_phys": u_B,
            "_u_A": u_A, "_u_B": u_B, "_cov_AB": cov_AB,
            "_u_budget_per_step": calib_result.get("u_budget_per_step", []),
            "_B_degC": B, "_u_B_degC": u_B,
        })
    elif calib_model == "cubic":
        cal_result_entry.update({
            "_theta": calib_result["theta"],
            "_a0": calib_result["a0"], "_a1": calib_result["a1"],
            "_a2": calib_result["a2"], "_a3": calib_result["a3"],
            "_u_a0": calib_result["u_a0"], "_u_a1": calib_result["u_a1"],
            "_u_a2": calib_result["u_a2"], "_u_a3": calib_result["u_a3"],
            "_cov_theta": calib_result["cov_theta"],
        })
    elif calib_model in ("linear_interp", "cubic_interp"):
        cal_result_entry.update({
            "_x_nodes":    calib_result["x_nodes"],
            "_y_nodes":    calib_result["y_nodes"],
            "_node_steps": calib_result.get("node_steps", []),
            "_rmse_degC":  calib_result.get("rmse_degC"),
            "_u_H_degC":   calib_result.get("u_H_degC"),
            "_per_step_budget": calib_result.get("per_step_budget", []),
        })
    else:
        cal_result_entry.update({
            "_C0": calib_result["C0"], "_C1": calib_result["C1"], "_C3": calib_result["C3"],
            "_u_C0": calib_result["u_C0"], "_u_C1": calib_result["u_C1"],
            "_u_C3": calib_result["u_C3"],
            "_cov_theta": calib_result["cov_theta"],
            "_theta": calib_result["theta"],
        })

    out["_calibration_result"] = cal_result_entry
    return out


def _run_calibration(procedure: str, payload: Dict, lsb_scale: Dict, sample_size: int,
                     adc_max: float, ub_pt_lsb: float, ub_tmp_lsb: float, verbose: bool,
                     risol_degc: float, old_A, old_B, old_C, old_D,
                     sensor_json, ref_json, check_units: bool, convert_units: bool):
    # ub_pt_lsb parameter is now actually ub_pt_degc (°C) — the orchestrator passes
    # the Fluke uncertainty in °C. The engine signatures accept ub_pt_degc natively.
    ub_pt_degc = ub_pt_lsb   # renamed in caller; kept as ub_pt_lsb here for compat
    unit_kwargs = dict(
        sensor_json=sensor_json, ref_json=ref_json,
        check_units=check_units, convert_units=convert_units,
    )
    if procedure == "linear":
        from model_calibration.linear_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=verbose, risol_degc=risol_degc,
            old_a=old_A, old_b=old_B, **unit_kwargs,
        )
    
    elif procedure == "cubic":
        from model_calibration.cubic_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=verbose, risol_degc=risol_degc,
            old_a=old_A, old_b=old_B, old_c=old_C, old_d=old_D, **unit_kwargs,
        )
    elif procedure == "cube-log":
        from model_calibration.cube_log_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=verbose, risol_degc=risol_degc, **unit_kwargs,
        )
    elif procedure == "linear_interp":
        from model_calibration.linear_interp_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=verbose, risol_degc=risol_degc, **unit_kwargs,
        )
    elif procedure == "cubic_interp":
        from model_calibration.cubic_interp_calibration import calibrate
        return calibrate(
            payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=sample_size,
            adc_max=adc_max, ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=verbose, risol_degc=risol_degc, **unit_kwargs,
        )
    else:
        raise ValueError(
            f"Unknown procedure '{procedure}'. "
            "Supported: linear, cubic, cube-log, linear_interp, cubic_interp"
        )


def _apply_calibration_skipped(cert_filled: Dict, calib_result: Dict,
                                old_A, old_B, old_C, old_D, lsb_per_c: float):
    cal = cert_filled.get("_calibration_result", {})
    model = cal.get("_calib_model", "linear")
    lpc = cal.get("_lsb_per_c", lsb_per_c)

    if model == "linear":
        init_A = old_A if old_A is not None else 1.0
        init_B = old_B if old_B is not None else 0.0
        # B is now in °C directly — no lpc division
        cal.update({"_A": init_A, "_B": init_B, "_B_degC": init_B,
                    "_u_A": 0.0, "_u_B": 0.0, "_u_B_degC": 0.0, "_cov_AB": 0.0})
        ntc = cert_filled["template_parts"]["sensor_method_template"]["ntc_model"]
        ntc.update({"_A_cal": init_A, "_B_cal": init_B, "_B_cal_degC": init_B,
                    "_u_A": 0.0, "_u_B": 0.0, "_u_B_degC": 0.0, "_cov_AB": 0.0})
    elif model == "cubic":
        init_a0 = old_A if old_A is not None else 0.0
        init_a1 = old_B if old_B is not None else 1.0
        init_a2 = old_C if old_C is not None else 0.0
        init_a3 = old_D if old_D is not None else 0.0
        for section in (cal, cert_filled["template_parts"]["sensor_method_template"]["ntc_model"]):
            section.update({
                "_a0": init_a0, "_a1": init_a1, "_a2": init_a2, "_a3": init_a3,
                "_u_a0": 0.0, "_u_a1": 0.0, "_u_a2": 0.0, "_u_a3": 0.0,
            })

    cert_filled["_calibration_result"] = cal

    # For OLS regression models (linear, cubic) with calibration skipped:
    # M_e_post == M_e_pre because no new correction is applied.
    # For interpolation models the measurement rows already contain the correct
    # M_e_post (= residual_degC from the interpolant), so we must NOT overwrite them.
    if model not in ("linear_interp", "cubic_interp"):
        meas = cert_filled["template_parts"]["calculated_calibration_values"]["measurements"]
        for row in meas:
            row[4] = row[3]
            row[2] = row[1] + row[3]
        cert_filled["template_parts"]["calculated_calibration_values"]["measurements"] = meas
        cert_filled["template_parts"]["calculated_calibration_values"]["_measurements"] = meas


def main() -> None:
    # conformity thresholds for Check H
    CONFORMITY_MAE_DEGC: float = 0.30
    CONFORMITY_PFA_THRESHOLD_PCT: float = 20.0
    CONFORMITY_PFA_U_STD_MODE: str = "combined"

    default_input_json  = DATA_DIR    / "export2_tmp126_lsb16.json"
    default_sensor_json = MODELS_DIR  / "ntc_temperature.json"
    default_ref_json    = MODELS_DIR  / "fluke_9142.json"
    default_cert_input  = TEMPLATE_DIR / "certificato_funzione_input.json"
    default_cert_output = OUT_DIR / "certificato_funzione_filled.json"
    default_pdf_output  = str(OUT_DIR / "ntc_cert_funzione.pdf")
    default_xml_output  = OUT_DIR / "ntc_calibration_certificate.xml"

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
    parser.add_argument("--conformity-output", type=Path, default=None)
    parser.add_argument("--images-dir", type=Path, default=None,
        help="Override base directory for plot images (replaces IMAGES_CALIB_DIR/IMAGES_CONFORM_DIR). "
             "Subfolders 'calibration' and 'conformity' will be created inside.")
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-pdf",  action="store_true", default=False)
    parser.add_argument("--no-xml",  action="store_true", default=False)
    parser.add_argument(
        "--procedure", type=str, default=None,
        choices=["linear", "cubic", "cube-log", "linear_interp", "cubic_interp"],
    )
    parser.add_argument(
        "--update-parameters-if-out-range-error",
        action=argparse.BooleanOptionalAction, default=False,
    )
    parser.add_argument("--check-units",   action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--convert-units", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--charts-interactive", action="store_true", default=False,
        help="Show charts interactively via matplotlib (blocks until all windows are closed). "
             "Skips saving PNGs. Mutually usable with --charts (both save AND show). "
             "Requires a display / GUI backend (not suitable for headless/Docker runs)."
    )
    # Previous calibration coefficients injected by the orchestrator from the sensor DB record.
    # When provided they override coeffA/B/C/D read from the sensor JSON (which may be 0.0 = unset).
    # linear / linear_interp: --old-a = A,  --old-b = B
    # cubic  / cubic_interp:  --old-a = a0, --old-b = a1, --old-c = a2, --old-d = a3
    # cube-log:                --old-a = C0, --old-b = C1, --old-c = C3
    parser.add_argument("--old-a", type=float, default=None,
        help="Previous calibration coefficient A (overrides sensor JSON coeffA if provided)")
    parser.add_argument("--old-b", type=float, default=None,
        help="Previous calibration coefficient B (overrides sensor JSON coeffB if provided)")
    parser.add_argument("--old-c", type=float, default=None,
        help="Previous calibration coefficient C / a2 / C3 (overrides sensor JSON coeffC if provided)")
    parser.add_argument("--old-d", type=float, default=None,
        help="Previous calibration coefficient D / a3 (overrides sensor JSON coeffD if provided)")
    args = parser.parse_args()

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

    # load sensor and reference models
    sensor = SENSOR_model.from_json(args.sensor)
    fluke  = RIFERIMENTO_model.from_json(args.ref)
    extra  = VAR_extra()

    # we pass the company data json
    sensor_json: Dict[str, Any] = sensor._data
    ref_json: Dict[str, Any]    = fluke._data

    if args.verbose:
        print(f"sensor model: {args.sensor}")
        print(f"ref model:    {args.ref}")
        print(f"cert-input:  {args.cert_input}")
        print(f"cert-output: {args.cert_output}")
        print(f"pdf output:  {args.pdf}")
        print(f"xml output:  {args.xml}")

    adc_bits  = extra._adc_bits
    adc_max   = float((1 << adc_bits) - 1)

    lsb_min = sensor._minPhyThreshold
    lsb_max = sensor._maxPhyThreshold
    print(f"LSB temperature range: [{lsb_min}, {lsb_max}] °C")
    lsb_per_c = adc_max / (lsb_max - lsb_min)   # informational

    # Reference (PT100/Fluke) uncertainty in native °C — no conversion to LSB
    ub_pt_degc = extra._U_pt_c / extra._k_pt     # [°C] standard uncertainty

    # NTC ADC uncertainty in native LSB — from sensor JSON (already /k)
    ub_tmp_lsb = sensor.uB                        # [LSB] standard uncertainty

    # Interpolation uncertainty: Fluke abs + NTC abs (sensor-side in °C via mean sensitivity)
    # We use lsb_per_c here only as a rough estimate for the certificate; it is
    # already informational (absUncertainty is a pre-calibration datasheet bound).
    ntc_abs_lsb   = float(sensor.absUncertainty)
    ntc_abs_c     = ntc_abs_lsb / lsb_per_c      # informational estimate
    abs_unc_sum_c = ub_pt_degc + ntc_abs_c

    sample_size = 20
    lsb_scale   = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}

    payload = json.loads(args.input.read_text(encoding="utf-8"))

    if args.verbose:
        print(f"Input JSON (LSB16): {args.input}")
        print(f"Calibration procedure: {sensor.calibrationProcedure}")
        print(f"LSB scale (informational): [{lsb_min}, {lsb_max}] °C  ({lsb_per_c:.4f} LSB/°C)")
        print(f"ub_pt  = {ub_pt_degc:.6f} °C  (reference, native)")
        print(f"ub_tmp = {ub_tmp_lsb:.4f} LSB  (NTC ADC, from sensor JSON)")
        print(f"NTC abs:   {ntc_abs_lsb:.4f} LSB  {ntc_abs_c:.6f} °C  (informational)")
        print(f"Sum abs (°C): {abs_unc_sum_c:.6f}")

    if args.procedure is not None:
        procedure = args.procedure.strip().lower()
        if args.verbose:
            print(f"INFO: --procedure override -> '{procedure}'.")
    else:
        procedure = sensor.calibrationProcedure.strip().lower()

    _PROCEDURE_ALIASES = {
        "qubic-interpolation": "linear",
        "linear-interpolation": "linear_interp",
        "cubic-interpolation":  "cubic_interp",
    }
    if procedure in _PROCEDURE_ALIASES:
        mapped = _PROCEDURE_ALIASES[procedure]
        if args.verbose:
            print(f"INFO: mapping '{procedure}' -> '{mapped}'.")
        procedure = mapped

    # Resolve previous calibration coefficients.
    # Priority: CLI --old-a/b/c/d (injected from DB by dcc_service) > sensor JSON coeffA/B/C/D.
    # The 0.0 sentinel means "not set" — treat as None so engines use their identity defaults.
    def _coeff_from_json(val: float) -> "float | None":
        return val if val != 0.0 else None

    old_A: float | None = args.old_a if args.old_a is not None else _coeff_from_json(sensor.coeffA)
    old_B: float | None = args.old_b if args.old_b is not None else _coeff_from_json(sensor.coeffB)
    old_C: float | None = args.old_c if args.old_c is not None else _coeff_from_json(sensor.coeffC)
    old_D: float | None = args.old_d if args.old_d is not None else _coeff_from_json(sensor.coeffD)

    if args.verbose:
        print(f"Previous coefficients: A={old_A}, B={old_B}, C={old_C}, D={old_D}")

    try:
        calib_result = _run_calibration(
            procedure=procedure, payload=payload, lsb_scale=lsb_scale,
            sample_size=sample_size, adc_max=adc_max,
            ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,   # ub_pt_lsb arg carries °C value; _run_calibration handles this
            verbose=args.verbose, risol_degc=sensor.resolution_degC,
            old_A=old_A, old_B=old_B, old_C=old_C, old_D=old_D,
            sensor_json=sensor_json, ref_json=ref_json,
            check_units=args.check_units, convert_units=args.convert_units,
        )
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # sensor accuracy gate
    calibration_skipped = False
    accuracy_ranges = _get_accuracy_ranges(sensor_json)
    checker = SensorAccuracyChecker(accuracy_ranges) if accuracy_ranges else None

    if checker is not None:
        temp_nominali_cr = calib_result.get("temp_nominali") or calib_result.get("steps", [])
        risultati_cr     = calib_result["risultati_elaborati"]
        ref_means_cr     = calib_result["ref_temp_means"]
        proc_model       = calib_result.get("model", "linear")

        as_found_errors: List[float] = []
        for _i, (_t, _ref_t) in enumerate(zip(temp_nominali_cr, ref_means_cr)):
            pmean_log = risultati_cr[_t]["pmean_log"]
            if proc_model == "linear" and old_A is not None and old_B is not None:
                # Detect LSB-domain old coefficients (|A|>>1 or |B|>>100)
                if abs(old_A) > 1.0 or abs(old_B) > 300:
                    t_sensor_pre = lsb_to_degc(old_A * pmean_log + old_B, lsb_scale, adc_max)
                else:
                    t_sensor_pre = old_A * pmean_log + old_B   # already °C
            elif proc_model == "cubic" and all(v is not None for v in [old_A, old_B, old_C, old_D]):
                from model_calibration.cubic_calibration import cubic_predict
                old_theta = np.array([old_A, old_B, old_C, old_D], dtype=float)
                t_sensor_pre = cubic_predict(float(pmean_log), old_theta)
            elif proc_model in ("linear_interp", "cubic_interp"):
                # as-found = raw sensor reading converted to physical unit (no prior calibration)
                t_sensor_pre = lsb_to_degc(pmean_log, lsb_scale, adc_max)
            else:
                t_sensor_pre = lsb_to_degc(pmean_log, lsb_scale, adc_max)
            as_found_errors.append(t_sensor_pre - _ref_t)

        accuracy_check = checker.check_all_points(ref_means_cr, as_found_errors)
        calib_result["_sensor_accuracy_check"] = accuracy_check

        if args.update_parameters_if_out_range_error:
            if accuracy_check["all_in_range"]:
                print(
                    "\n[INFO] --update-parameters-if-out-range-error: "
                    "ALL as-found errors are within the declared sensorAccuracy limits.\n"
                    "       Calibration parameter update is NOT necessary.\n"
                    "       Result flag: calibration_done = 'not_necessary'"
                )
                calib_result["calibration_done"] = "not_necessary"
                calibration_skipped = True
            else:
                failed = [p for p in accuracy_check["per_point"] if not p["in_range"]]
                print(
                    f"\n[INFO] --update-parameters-if-out-range-error: "
                    f"{len(failed)} as-found error(s) exceed sensorAccuracy limits. "
                    "Calibration will proceed."
                )
                for p in failed:
                    print(
                        f"       Point {p['point']}: T_ref={p['T_ref_degC']:.4f}°C  "
                        f"as-found={p['as_found_error_degC']:+.6f}°C  "
                        f"limit=±{p['max_allowed_error_degC']:.4f}°C  => OUT OF RANGE"
                    )
                calib_result["calibration_done"] = "done"
    else:
        calib_result["_sensor_accuracy_check"] = None

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
                "expanded_uncertainties_degC": calib_result["expanded_uncertainties"],
            }
        elif model == "cubic":
            summary = {
                "model": model,
                "a0": calib_result["a0"], "a1": calib_result["a1"],
                "a2": calib_result["a2"], "a3": calib_result["a3"],
                "u_a0": calib_result["u_a0"], "u_a1": calib_result["u_a1"],
                "u_a2": calib_result["u_a2"], "u_a3": calib_result["u_a3"],
                "expanded_uncertainties_degC": calib_result["expanded_uncertainties"],
            }
        elif model in ("linear_interp", "cubic_interp"):
            summary = {
                "model": model,
                "x_nodes": calib_result["x_nodes"],
                "y_nodes": calib_result["y_nodes"],
                "node_steps": calib_result.get("node_steps", []),
                "rmse_degC": calib_result.get("rmse_degC"),
                "u_H_degC":  calib_result.get("u_H_degC"),
                "n_interior": calib_result.get("n_interior"),
                "expanded_uncertainties_degC": calib_result["expanded_uncertainties"],
            }
        else:
            summary = {
                "model": model,
                "C0": calib_result["C0"], "C1": calib_result["C1"], "C3": calib_result["C3"],
                "u_C0": calib_result["u_C0"], "u_C1": calib_result["u_C1"],
                "u_C3": calib_result["u_C3"],
                "expanded_uncertainties_degC": calib_result["expanded_uncertainties"],
            }
        print(json.dumps(summary, indent=2))

        calc_interp_unc  = max(calib_result.get("expanded_uncertainties", [0.0]))
        fixed_interp_unc = round_to_significant_figures(abs_unc_sum_c, 2)
        print("\n=== Interpolation uncertainty check [degC] ===")
        print(f"fixed:      {fixed_interp_unc:.6f}")
        print(f"calculated: {calc_interp_unc:.6f}")
        print(f"difference: {calc_interp_unc - fixed_interp_unc:+.6f}")

    cert_input_data = json.loads(args.cert_input.read_text(encoding="utf-8"))
    cert_filled = _build_cert_filled(
        cert_input=cert_input_data,
        sensor=sensor,
        calib_result=calib_result,
        adc_max=adc_max,
        lsb_scale=lsb_scale,
        sensor_json=sensor_json,
        ref_json=ref_json,
    )

    cert_filled["_calibration_done"] = calib_result.get("calibration_done", "done")
    cert_filled["_sensor_accuracy_check"] = calib_result.get("_sensor_accuracy_check")

    if calibration_skipped:
        _apply_calibration_skipped(cert_filled, calib_result, old_A, old_B, old_C, old_D, lsb_per_c)

    if args.verbose:
        print("\n=== Measurements (full FP precision) ===")
        for i, row in enumerate(cert_filled["template_parts"]["calculated_calibration_values"]["measurements"]):
            print(
                f"  row[{i}]: point={row[0]}, T_ref={row[1]:.10f}, "
                f"T_c_post={row[2]:.10f}, M_e_pre={row[3]:.10f}, "
                f"M_e_post={row[4]:.10f}, U_exp={row[5]:.10f}"
            )

    args.cert_output.parent.mkdir(parents=True, exist_ok=True)
    args.cert_output.write_text(
        json.dumps(cert_filled, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if args.verbose:
        print(f"Certificate JSON written to: {args.cert_output}")

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

    if args.charts:
        try:
            model = calib_result.get("model", procedure)

            # Derive display labels and unit from sensor/reference JSONs
            _s_phys_dsi = sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
            _chart_unit = dsi_to_symbol(_s_phys_dsi)
            _sensor_lbl = sensor_json.get("name", sensor_json.get("deviceType", "Sensor"))
            _ref_lbl    = ref_json.get("name", ref_json.get("deviceType", "Reference"))
            _acc_limit  = _worst_accuracy_limit(accuracy_ranges)

            _common_kw = dict(
                unit_symbol=_chart_unit,
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
                    ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc),
                    ub_tmp_lsb=ub_tmp_lsb,
                    output_dir=_images_calib_dir,
                    _calib_result=calib_result,   # pass full result for correct GUM budget
                    **_common_kw,
                )
            elif model == "linear_interp":
                from model_calibration.linear_interp_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    result=calib_result,
                    lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    output_dir=_images_calib_dir,
                    **_common_kw,
                )
            elif model == "cubic_interp":
                from model_calibration.cubic_interp_calibration import save_charts as save_calib_charts
                saved = save_calib_charts(
                    result=calib_result,
                    lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    output_dir=_images_calib_dir,
                    **_common_kw,
                )
            else:
                if model == "cubic":
                    from model_calibration.cubic_calibration import save_charts as save_calib_charts
                    saved = save_calib_charts(
                        theta=calib_result["theta"],
                        temp_nominali=calib_result["temp_nominali"],
                        dati_raw=calib_result["dati_raw"],
                        risultati_elaborati=calib_result["risultati_elaborati"],
                        sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                        adc_max=adc_max,
                        ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc * lsb_per_c),
                        ub_tmp_lsb=ub_tmp_lsb,
                        output_dir=_images_calib_dir, cov_theta=calib_result["cov_theta"],
                        _calib_result=calib_result,   # pass full result for GUM budget
                        **_common_kw,
                    )
                else:
                    from model_calibration.cube_log_calibration import save_charts as save_calib_charts
                    saved = save_calib_charts(
                        theta=calib_result["theta"],
                        temp_nominali=calib_result["temp_nominali"],
                        dati_raw=calib_result["dati_raw"],
                        risultati_elaborati=calib_result["risultati_elaborati"],
                        sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                        adc_max=adc_max,
                        ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc * lsb_per_c),
                        ub_tmp_lsb=ub_tmp_lsb,
                        output_dir=_images_calib_dir, cov_theta=calib_result["cov_theta"],
                    )
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
                    ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc),
                    ub_tmp_lsb=ub_tmp_lsb,
                )
            elif model == "linear_interp":
                from model_calibration.linear_interp_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    result=calib_result,
                    lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                )
            elif model == "cubic_interp":
                from model_calibration.cubic_interp_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    result=calib_result,
                    lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
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
                    ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc * lsb_per_c),
                    ub_tmp_lsb=ub_tmp_lsb,
                    cov_theta=calib_result["cov_theta"],
                )
            else:  # cube-log
                from model_calibration.cube_log_calibration import plot_charts as plot_calib_charts
                plot_calib_charts(
                    theta=calib_result["theta"],
                    temp_nominali=calib_result["temp_nominali"],
                    dati_raw=calib_result["dati_raw"],
                    risultati_elaborati=calib_result["risultati_elaborati"],
                    sample_size=sample_size, lsb_scale_sensor_info=lsb_scale,
                    adc_max=adc_max,
                    ub_pt_lsb=calib_result.get("ub_pt_lsb", ub_pt_degc * lsb_per_c),
                    ub_tmp_lsb=ub_tmp_lsb,
                    cov_theta=calib_result["cov_theta"],
                )
        except Exception as ex:
            import traceback
            print(f"[interactive] Calibration chart error: {ex}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()

    try:
        import verifica_conformita as _conformita

        filled_data = json.loads(args.cert_output.read_text(encoding="utf-8"))
        calib_cr      = _conformita.extract_calib(filled_data)
        measurements  = _conformita.extract_measurements(filled_data)
        notes         = _conformita.extract_notes(filled_data)

        limit_degc    = _conformita._parse_limit(sensor.uncertainty_limit) or 0.10
        resolution    = sensor.resolution_degC
        min_phys      = sensor._minPhyThreshold
        max_phys      = sensor._maxPhyThreshold

        u_exp_list  = calib_cr["_expanded_uncertainties_degC"]
        # normalise step list key across regression and interpolation models
        temp_nom    = calib_cr.get("_temp_nominali") or calib_cr.get("_steps", [])
        conf_model  = calib_cr.get("_calib_model", "linear")

        sG, rG = _conformita.check_G(measurements, accuracy_ranges, conf_model, verbose=False)
        sA, rA = _conformita.check_A(measurements, verbose=False)
        sB, rB = _conformita.check_B(measurements, limit_degc, verbose=False)
        sD, rD = _conformita.check_D(measurements, u_exp_list, min_phys, max_phys, resolution, verbose=False)
        sE, rE = _conformita.check_E(notes, u_exp_list, verbose=False)

        if conf_model == "linear":
            A_conf   = calib_cr["_A"]
            B_conf   = calib_cr["_B"]
            u_A_conf = calib_cr["_u_A"]
            u_B_conf = calib_cr["_u_B"]
            cov_conf = calib_cr["_cov_AB"]
            sC, rC = _conformita.check_C(measurements, A_conf, B_conf, min_phys, max_phys, verbose=False)
            sF, rF = _conformita.check_F(measurements, A_conf, B_conf, min_phys, max_phys, "funzione", verbose=False)
        else:
            # cubic, cube-log, linear_interp, cubic_interp: checks C and F are OLS-specific
            A_conf = B_conf = u_A_conf = u_B_conf = cov_conf = None
            na_label = f"N/A ({conf_model})"
            sC, rC = na_label, {}
            sF, rF = na_label, {}

        # _u_budget_per_step is OLS-only; interp models store per_step_budget separately
        u_budget_conf = calib_cr.get("_u_budget_per_step", [])
        sH, rH = _conformita.check_H(
            measurements, mae_degc=CONFORMITY_MAE_DEGC,
            pfa_threshold_pct=CONFORMITY_PFA_THRESHOLD_PCT,
            verbose=False, u_std_mode=CONFORMITY_PFA_U_STD_MODE,
            u_budget_per_step=u_budget_conf,
        )

        conformity_summary = {
            "G": sG, "A": sA, "B": sB, "C": sC, "D": sD, "E": sE, "F": sF, "H": sH,
            "calibration_done": calib_result.get("calibration_done", "done"),
            "overall": (
                "CONFORME"
                if all(s == "PASS" for s in [sA, sB, sD, sE, sH]
                       + ([sC, sF] if conf_model == "linear" else []))
                else "NON CONFORME"
            ),
        }

        if args.verbose:
            print("\n=== Conformity check ===")
            for k, v in conformity_summary.items():
                print(f"  [{k}] {v}")
            pfa_vals = [r["PFA_pct"] for r in rH] if isinstance(rH, list) else []
            if pfa_vals:
                print(
                    "  [H] PFA per punto: "
                    + "  ".join(f"P{r['punto']}={r['PFA_pct']:.1f}%" for r in rH)
                )
            print(
                f"  [H] MAE=±{CONFORMITY_MAE_DEGC:.3f}°C  "
                f"soglia={CONFORMITY_PFA_THRESHOLD_PCT:.0f}%  "
                f"u_std_mode={CONFORMITY_PFA_U_STD_MODE}"
            )

        if args.conformity_output is not None:
            conformity_data = {
                "summary": conformity_summary,
                "check_G": rG, "check_A": rA, "check_B": rB, "check_C": rC,
                "check_D": rD, "check_E": rE, "check_F": rF, "check_H": rH,
                "check_H_params": {
                    "mae_degc": CONFORMITY_MAE_DEGC,
                    "pfa_threshold_pct": CONFORMITY_PFA_THRESHOLD_PCT,
                    "u_std_mode": CONFORMITY_PFA_U_STD_MODE,
                },
            }
            args.conformity_output.parent.mkdir(parents=True, exist_ok=True)
            args.conformity_output.write_text(
                json.dumps(conformity_data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            if args.verbose:
                print(f"Conformity JSON written to: {args.conformity_output}")

        if args.charts and conf_model == "linear":
            from verifica_conformita import save_charts as save_conf_charts
            saved_conf = save_conf_charts(
                measurements=measurements,
                budget_results=rD,
                A=A_conf, B=B_conf, u_A=u_A_conf, u_B=u_B_conf, cov_AB=cov_conf,
                min_phys=min_phys, max_phys=max_phys,
                limit_degc=limit_degc, variant="funzione",
                temp_nominali=temp_nom, output_dir=_images_conform_dir,
            )
            if args.verbose:
                for p in saved_conf:
                    print(f"Conformity chart saved: {p}")

        if args.charts_interactive and conf_model == "linear":
            if args.verbose:
                print("\n[interactive] Opening conformity charts — close the window(s) to continue.")
            from verifica_conformita import plot_charts as plot_conf_charts
            plot_conf_charts(
                measurements=measurements,
                budget_results=rD,
                A=A_conf, B=B_conf, u_A=u_A_conf, u_B=u_B_conf, cov_AB=cov_conf,
                min_phys=min_phys, max_phys=max_phys,
                limit_degc=limit_degc, variant="funzione",
                temp_nominali=temp_nom,
            )

    except Exception as ex:
        print(f"Conformity check error: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
