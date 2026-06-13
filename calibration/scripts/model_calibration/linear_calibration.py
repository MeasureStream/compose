from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np


def parse_step(step_s: str) -> Tuple[float, float]:
    m = re.match(r"\s*\(\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*\)\s*$", step_s)
    if not m:
        raise ValueError(f"Formato step non valido: {step_s}")
    return float(m.group(1)), float(m.group(2))


def get_scale_from_sensor(sensor_info: Dict[str, Any]) -> Tuple[float, float]:
    min_phys = float(sensor_info.get("minPhysVal", 0.0))
    max_phys = float(sensor_info.get("maxPhysVal", 0.0))
    if max_phys > min_phys:
        return min_phys, max_phys
    min_elec = float(sensor_info.get("minElecVal", 0.0))
    max_elec = float(sensor_info.get("maxElecVal", 0.0))
    if max_elec > min_elec:
        return min_elec, max_elec
    return -40.0, 125.0


def phys_to_lsb16(values: np.ndarray, sensor_info: Dict[str, Any], adc_max: float) -> np.ndarray:
    min_v, max_v = get_scale_from_sensor(sensor_info)
    span = max(max_v - min_v, 1e-12)
    return np.clip(np.round((values - min_v) / span * adc_max), 0.0, adc_max)


def lsb16_to_phys(values: np.ndarray, sensor_info: Dict[str, Any], adc_max: float) -> np.ndarray:
    min_v, max_v = get_scale_from_sensor(sensor_info)
    return min_v + (values / adc_max) * (max_v - min_v)


def _build_step_index_maps(
    payload: Dict[str, Any],
    lsb_scale: Dict[str, Any],
    adc_max: float,
) -> Tuple[Dict[int, List[float]], Dict[int, List[List[float]]]]:
    # ref Y readings in native unit
    ref_by_step: Dict[int, List[float]] = {}
    for sample in payload.get("reference_temperature_samples", []):
        idx    = int(sample["index_step"])
        temp_c = float(sample["reading"])
        ref_by_step.setdefault(idx, []).append(temp_c)

    sensor_by_step: Dict[int, List[List[float]]] = {}
    for frame in payload.get("sensor_raw_samples", []):
        idx = int(frame["index_step"])
        if "value" in frame:
            raw_values = frame["value"]
        elif "value_hex" in frame:
            hex_s = str(frame["value_hex"]).strip()
            raw_values = [int(hex_s[i:i+4], 16) for i in range(0, len(hex_s), 4) if len(hex_s[i:i+4]) == 4]
        else:
            raw_values = []
        sensor_by_step.setdefault(idx, []).append([float(v) for v in raw_values])

    return ref_by_step, sensor_by_step


def _compute_step_statistics(
    payload: Dict[str, Any],
    temp_nominali: List[float],
    temp_nominale: float,
    sample_size: int,
    lsb_scale: Dict[str, Any],
    adc_max: float,
    verbose: bool,
    unit_symbol: str = "°C",
) -> Tuple[bool, Dict[str, np.ndarray], Dict[str, Any]]:
    if temp_nominale not in temp_nominali:
        return False, {}, {}

    step_idx = temp_nominali.index(temp_nominale)
    ref_by_step, sensor_by_step = _build_step_index_maps(payload, lsb_scale, adc_max)

    arr_ref        = np.array(ref_by_step.get(step_idx, []), dtype=float)  # Y
    sensor_frames  = sensor_by_step.get(step_idx, [])

    if arr_ref.size == 0 or len(sensor_frames) == 0:
        return False, {}, {}

    buffer_sensor_lsb: List[float] = []
    for frame_values in sensor_frames:
        frame_arr = np.array(frame_values, dtype=float)
        if frame_arr.size > 0:
            buffer_sensor_lsb.append(float(np.mean(frame_arr)))

    if not buffer_sensor_lsb:
        return False, {}, {}

    n_total   = min(arr_ref.size, len(buffer_sensor_lsb))
    n_campioni = n_total // sample_size
    n_keep    = n_campioni * sample_size
    if n_keep == 0:
        return False, {}, {}

    arr_ref    = arr_ref[:n_keep]                                              # Y
    arr_sensor = np.array(buffer_sensor_lsb[:n_keep], dtype=float)            # X [LSB]

    matrix_ref    = arr_ref.reshape((n_campioni, sample_size))
    matrix_sensor = arr_sensor.reshape((n_campioni, sample_size))

    sample_mean_ref    = np.mean(matrix_ref, axis=1)    # Y per block
    campioni_std_ref   = np.std(matrix_ref, axis=1, ddof=1)
    sample_mean_sensor = np.mean(matrix_sensor, axis=1) # X [LSB] per block
    campioni_std_sensor = np.std(matrix_sensor, axis=1, ddof=1)

    pmean_ref    = float(np.mean(sample_mean_ref))     # Y
    pstd_ref     = float(np.std(sample_mean_ref, ddof=1) / np.sqrt(n_campioni)) if n_campioni > 1 else 0.0
    pmean_sensor = float(np.mean(sample_mean_sensor))  # X [LSB]
    pstd_sensor  = float(np.std(sample_mean_sensor, ddof=1) / np.sqrt(n_campioni)) if n_campioni > 1 else 0.0

    glob_smean_ref    = float(np.mean(arr_ref))
    glob_std_ref      = float(np.std(arr_ref, ddof=1)) if n_keep > 1 else 0.0
    glob_smean_sensor = float(np.mean(arr_sensor))
    glob_std_sensor   = float(np.std(arr_sensor, ddof=1)) if n_keep > 1 else 0.0

    # sensor X spread within step [LSB]
    err        = np.abs(arr_sensor - pmean_sensor)
    max_error  = float(np.max(err)) if err.size else float("nan")
    mean_error = float(np.mean(err)) if err.size else float("nan")

    if verbose:
        print(f"\n--- {temp_nominale} {unit_symbol} : {n_keep} samples ({n_campioni} blocks) ---")
        print(f"  ref: {pmean_ref:.4f} +/- {pstd_ref:.6f} {unit_symbol}")
        print(f"  sensor: {pmean_sensor:.2f} +/- {pstd_sensor:.4f} LSB  ({lsb16_to_phys(np.array([pmean_sensor]), lsb_scale, adc_max)[0]:.4f} {unit_symbol})")
        print(f"  Max sensor spread: {max_error:.4f} LSB")

    dati_raw_item = {"ref": arr_ref, "sensor": arr_sensor}
    risultati_item = {
        "x_axis": np.arange(n_campioni),
        "smean_ref":    sample_mean_ref,    "std_ref":    campioni_std_ref,
        "smean_sensor": sample_mean_sensor, "std_sensor": campioni_std_sensor,
        "pmean_ref":    pmean_ref,   "pstd_ref":    pstd_ref,
        "pmean_sensor": pmean_sensor, "pstd_sensor": pstd_sensor,
        "max_error": max_error, "mean_error": mean_error,
    }
    return True, dati_raw_item, risultati_item


def _get_data(
    payload: Dict[str, Any],
    temp_nominali: List[float],
    sample_size: int,
    lsb_scale: Dict[str, Any],
    adc_max: float,
    verbose: bool,
    unit_symbol: str = "°C",
) -> Tuple[Dict[float, Dict[str, np.ndarray]], Dict[float, Dict[str, Any]]]:
    dati_raw: Dict[float, Dict[str, np.ndarray]] = {}
    risultati_elaborati: Dict[float, Dict[str, Any]] = {}
    for t in temp_nominali:
        ok, raw_item, result_item = _compute_step_statistics(payload, temp_nominali, t, sample_size, lsb_scale, adc_max, verbose, unit_symbol)
        if ok:
            dati_raw[t]           = raw_item
            risultati_elaborati[t] = result_item
    return dati_raw, risultati_elaborati


def _compute_gum_ols_coefficients(
    x: np.ndarray,
    y: np.ndarray,
    u_x: np.ndarray,
    u_y: np.ndarray,
) -> Tuple[float, float, float, float, float]:
    # GUM OLS: Y = A*X + B in mixed domain
    n     = len(x)
    x_mean = np.mean(x)
    y_mean = np.mean(y)

    d_den = np.sum((x - x_mean) ** 2)
    if np.isclose(d_den, 0.0):
        raise ValueError("Denominatore nullo nel calcolo GUM")

    n_num = np.sum((x - x_mean) * (y - y_mean))
    a     = n_num / d_den
    b     = y_mean - a * x_mean

    dA_dx = np.zeros(n)
    dA_dy = np.zeros(n)
    dB_dx = np.zeros(n)
    dB_dy = np.zeros(n)

    for i in range(n):
        dA_dy[i] = (x[i] - x_mean) / d_den
        dA_dx[i] = ((y[i] - y_mean) - 2 * a * (x[i] - x_mean)) / d_den
        dB_dy[i] = (1 / n) - x_mean * dA_dy[i]
        dB_dx[i] = -(a / n) - x_mean * dA_dx[i]

    u_a2  = np.sum((dA_dx * u_x)**2 + (dA_dy * u_y)**2)
    u_b2  = np.sum((dB_dx * u_x)**2 + (dB_dy * u_y)**2)
    cov_ab = np.sum((dA_dx * dB_dx * u_x**2) + (dA_dy * dB_dy * u_y**2))

    return float(a), float(b), float(np.sqrt(max(0.0, u_a2))), float(np.sqrt(max(0.0, u_b2))), float(cov_ab)


MIN_STEPS_LINEAR = 2


def run_prechecks(
    payload: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    # pre-checks: >= 2 steps, mandatory pint-based unit check when model JSONs provided
    result: Dict[str, Any] = {
        "ok": True,
        "steps_ok": False,
        "n_steps": 0,
        "unit_check": None,
        "errors": [],
        "warnings": [],
    }

    temp_nominali = [parse_step(s)[0] for s in payload.get("steps", [])]
    result["n_steps"] = len(temp_nominali)
    result["steps_ok"] = len(temp_nominali) >= MIN_STEPS_LINEAR
    if not result["steps_ok"]:
        msg = f"Linear calibration requires at least {MIN_STEPS_LINEAR} steps (got {len(temp_nominali)})."
        result["errors"].append(msg)
        result["ok"] = False

    if sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "linear")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[linear unit-check]")
        result["warnings"].extend(uc.warnings)
        if not uc.ok:
            result["errors"].extend(uc.errors)
            result["ok"] = False

    return result


def calibrate(
    payload: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    sample_size: int,
    adc_max: float,
    ub_ref_y: float | None = None,    # type-B std uncertainty of the reference [Y]
    ub_sensor_lsb: float = 0.0,      # type-B std uncertainty of the sensor ADC [LSB]
    verbose: bool = False,
    risol: float = 0.1,
    old_a: float | None = None,
    old_b: float | None = None,
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    convert_units: bool = False,
    unit_symbol: str = "°C",
    formula: str | None = None,
    formula_vars: Dict[str, float] | None = None,
    ufit: float | None = None,
    coverage_factor: float = 2.0,
) -> Dict[str, Any]:
    if ub_ref_y is None:
        raise ValueError("calibrate() requires ub_ref_y")

    pre = run_prechecks(payload, sensor_json, ref_json, verbose)
    unit_check_result = pre["unit_check"]
    if not pre["ok"]:
        raise ValueError("\n".join(pre["errors"]))

    temp_nominali = [parse_step(s)[0] for s in payload.get("steps", [])]
    # compute statistics per step and gather raw data for reporting/plotting
    dati_raw, risultati_elaborati = _get_data(payload, temp_nominali, sample_size, lsb_scale_sensor_info, adc_max, verbose, unit_symbol)

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)   # informational only

    if verbose:
        print(f"\n\n --- data acquisition done ---")

    # sensor X [LSB] → ref Y [{unit_symbol}]: Y = A [{unit_symbol}/LSB] * X [LSB] + B [{unit_symbol}]
    x = np.array([risultati_elaborati[t]["pmean_sensor"] for t in temp_nominali], dtype=float)  # X [LSB]
    y = np.array([risultati_elaborati[t]["pmean_ref"]    for t in temp_nominali], dtype=float)  # Y

    # Per-step ub_sensor_lsb via formula evaluation or single fixed value
    _ub_arr: np.ndarray
    if formula and formula_vars:
        from evaluation_formula import evaluate_formula, qs
        _ub_per_step = []
        for i, t in enumerate(temp_nominali):
            D_i = float(x[i])
            _vars_i = {**formula_vars, "d_in": qs(D_i)}
            _ub_per_step.append(float(evaluate_formula(formula, _vars_i).magnitude))
        _ub_arr = np.array(_ub_per_step, dtype=float)
        if verbose:
            _ub_mean = float(np.mean(_ub_arr))
            print(f"ub_sensor (per-step via formula): mean={_ub_mean:.4f} LSB, values={_ub_per_step}")
    else:
        _ub_arr = np.full(len(temp_nominali), ub_sensor_lsb, dtype=float)

    u_res = risol / np.sqrt(12.0)
    uc_sensor = np.array([np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + _ub_arr[i]**2) for i, t in enumerate(temp_nominali)], dtype=float)  # u_x [LSB]
    uc_ref    = np.array([np.sqrt(risultati_elaborati[t]["pstd_ref"]**2    + ub_ref_y**2)      for t in temp_nominali], dtype=float)  # u_y

    if old_a is not None and old_b is not None:
        _old_b_y = old_b
        y_old = old_a * x + _old_b_y
        err_old = y_old - y
        if verbose:
            print(f"\n--- Baseline pre-fit error (old A/B) ---")
            print(f"old A = {old_a:.10f}  old B = {_old_b_y:.6f} {unit_symbol}")
            print(f"signed mean error: {np.mean(err_old):.6f} {unit_symbol}")

    a_rough = (y[-1] - y[0]) / (x[-1] - x[0]) if len(x) > 1 and abs(x[-1] - x[0]) > 1e-12 else 0.0
    if abs(a_rough) > 1e-12:
        u_res_lsb = u_res / abs(a_rough)
        uc_sensor = np.sqrt(uc_sensor**2 + u_res_lsb**2)

    a, b, u_a, u_b, cov_ab = _compute_gum_ols_coefficients(x, y, uc_sensor, uc_ref)

    if verbose:
        print(f"\nA = {a:.10f} {unit_symbol}/LSB  B = {b:.6f} {unit_symbol}")
        print(f"u(A) = {u_a:.10f} {unit_symbol}/LSB  u(B) = {u_b:.6f} {unit_symbol}")
        print(f"cov(A,B) = {cov_ab:.10f}")

    # Regression uncertainty (RMSE) with degrees-of-freedom correction — N−2 for linear
    y_pred  = a * x + b
    e_fit   = y - y_pred
    N_lin   = len(x)
    rmse    = float(np.sqrt(np.sum(e_fit**2) / max(1, N_lin - 2)))
    if verbose:
        print(f"RMSE (N={N_lin}, p=2): {rmse:.6f} {unit_symbol}")

    # GUM budget per step
    sens = abs(a)   # sensitivity dY/dX [{unit_symbol}/LSB]

    u_ris = risol * lsb_per_y / np.sqrt(12.0)  # ADC resolution std uncertainty [LSB]
    u_fitting_val = ufit if ufit is not None else rmse

    expanded_uncertainties: List[float] = []
    per_step_u_budget_raw: List[Tuple] = []

    for i, t in enumerate(temp_nominali):
        R_avg     = risultati_elaborati[t]["pmean_sensor"]        # mean sensor reading [LSB]
        uA_ref    = risultati_elaborati[t]["pstd_ref"]            # u_y type-A
        uA_sensor = risultati_elaborati[t]["pstd_sensor"] * sens  # u_x type-A × sens
        u_ref     = np.sqrt(uA_ref**2 + ub_ref_y**2)

        # ub_uso²(T) = (R·u_A)² + u_B² + 2R·cov(A,B) + (A·u_ris)²
        ub_uso = np.sqrt((R_avg * u_a)**2 + u_b**2 + 2.0 * R_avg * cov_ab + (a * u_ris)**2)

        uc_sensor = np.sqrt(uA_sensor**2 + ub_uso**2)  # u_x total
        u_c       = np.sqrt(u_ref**2 + uc_sensor**2)
        U_exp     = coverage_factor * u_c
        expanded_uncertainties.append(float(U_exp))
        per_step_u_budget_raw.append((t, uA_ref, uA_sensor, ub_uso, u_fitting_val, u_ref, uc_sensor, u_c, U_exp))

    # ref means
    ref_temp_means: List[float] = [
        float(risultati_elaborati[t]["pmean_ref"])
        for t in temp_nominali
    ]

    u_budget_per_step: List[Dict[str, float]] = [
        {"t_nom": t, "uA_ref": uA_ref, "uA_sensor": uA_sensor,
         "ub_uso": ub_uso_, "u_fitting": u_fitting_,
         "u_ref": u_ref_, "u_sensor": u_sensor_,
         "u_c": u_c_, "U_exp": U_exp_, "k": coverage_factor}
        for t, uA_ref, uA_sensor, ub_uso_, u_fitting_, u_ref_, u_sensor_, u_c_, U_exp_ in per_step_u_budget_raw
    ]

    result: Dict[str, Any] = {
        "model": "linear",
        "A": float(a),
        "B": float(b),
        "u_A": float(u_a), "u_B": float(u_b), "cov_AB": float(cov_ab),
        "rmse": rmse,
        "u_fitting": u_fitting_val,
        "old_A": None if old_a is None else float(old_a),
        "old_B": None if old_b is None else float(old_b),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "u_budget_per_step": u_budget_per_step,
        "ref_temp_means": ref_temp_means,
        "lsb_per_y": lsb_per_y,          # informational only
        "ub_ref_y": ub_ref_y,
        "ub_sensor_lsb": ub_sensor_lsb,
        "ub_sensor_lsb_per_step": _ub_arr.tolist(),
        "ub_ref_lsb": ub_ref_y * lsb_per_y,
    }
    if formula:
        result["formula"] = formula
        result["formula_vars"] = dict(formula_vars) if formula_vars else {}
    if unit_check_result is not None:
        result["unit_check"] = unit_check_result
    if convert_units and sensor_json is not None and ref_json is not None:
        from .unit_checks import convert_result
        result = convert_result(result, sensor_json, ref_json)
    return result


def build_report(
    temp_nominali: List[float],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    a: float,
    b: float,
    u_a: float,
    u_b: float,
    cov_ab: float,
    adc_bits: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_ref_lsb: float = 0.0,
    ub_sensor_lsb: float = 0.0,
    ub_ref_y: float | None = None,
    unit_symbol: str = "°C",
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)
    _ub_ref = ub_ref_y if ub_ref_y is not None else ub_ref_lsb / lsb_per_y
    u = unit_symbol

    lines: List[str] = []
    lines.append(f"# Calibration Report — Linear OLS (X [LSB] sensor, Y [{u}] reference)")
    lines.append("")
    lines.append("## Per-step statistics")
    lines.append("")
    lines.append(f"| step | target [{u}] | Y_ref [{u}] | u_Y_ref [{u}] | X_sensor [LSB] | u_X_sensor [LSB] | max_X_spread [LSB] |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for i, t in enumerate(temp_nominali):
        r = risultati_elaborati[t]
        lines.append(f"| {i} | {t:.3f} | {r['pmean_ref']:.4f} | {r['pstd_ref']:.6f} | {r['pmean_sensor']:.2f} | {r['pstd_sensor']:.4f} | {r['max_error']:.4f} |")

    lines.append("")
    lines.append(f"## Calibration coefficients (Y [{u}] = A [{u}/LSB] * X [LSB] + B [{u}])")
    lines.append("")
    lines.append(f"- A: {a:.10f} {u}/LSB")
    lines.append(f"- B: {b:.6f} {u}")
    lines.append(f"- u(A): {u_a:.10f} {u}/LSB")
    lines.append(f"- u(B): {u_b:.6f} {u}")
    lines.append(f"- cov(A,B): {cov_ab:.10f}")
    if u_a > 0 and u_b > 0:
        lines.append(f"- corr(A,B): {cov_ab / (u_a * u_b):.6f}")

    lines.append("")
    lines.append("## Uncertainty budget")
    lines.append(f"- LSB scale (informational): [{min_v}, {max_v}] {u}  →  {lsb_per_y:.4f} LSB/{u}")
    lines.append(f"- Sensor format: unsigned {adc_bits}-bit LSB")
    lines.append(f"- ref u_B: {_ub_ref:.6f} {u}")
    lines.append(f"- sensor u_B: {ub_sensor_lsb:.4f} LSB  ×  |A| = {ub_sensor_lsb * abs(a):.6f} {u}")

    return "\n".join(lines) + "\n"


def plot_charts(
    a: float, b: float,
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_ref_lsb: float,
    ub_sensor_lsb: float,
    unit_symbol: str = "°C",
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)

    fig1, axs = plt.subplots(2, 3, figsize=(18, 10))
    axcmeanflat = axs.flatten()
    fig1.suptitle(f"Sample analysis (n={sample_size}) [LSB]", fontsize=16)
    for i, temp in enumerate(temp_nominali):
        ax = axcmeanflat[i]
        if temp in risultati_elaborati:
            res = risultati_elaborati[temp]
            x = res["x_axis"]
            ub_ref_band    = np.sqrt(res["std_ref"]**2 + ub_ref_lsb**2)
            ub_sensor_band = np.sqrt(res["std_sensor"]**2 + ub_sensor_lsb**2)
            ax.plot(x, res["smean_ref"],    "b-o", label="ref",    linewidth=1, markersize=2)
            ax.plot(x, res["smean_ref"]    + ub_ref_band,    "b--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_ref"]    - ub_ref_band,    "b--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_sensor"], "r-o", label="sensor", linewidth=1, markersize=2)
            ax.plot(x, res["smean_sensor"] + ub_sensor_band, "r--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_sensor"] - ub_sensor_band, "r--", alpha=0.4, linewidth=1)
            ax.set_title(f"Nominale: {temp} {unit_symbol}")
            ax.set_ylabel("LSB")
            ax.grid(True, alpha=0.3)
            if i == 0:
                ax.legend()
        else:
            ax.text(0.5, 0.5, "Dati assenti", ha="center")

    fig2, axs2 = plt.subplots(1, 2, figsize=(18, 10))
    ref_val    = [risultati_elaborati[t]["pmean_ref"]    for t in temp_nominali]
    sensor_val = [risultati_elaborati[t]["pmean_sensor"] for t in temp_nominali]
    ref_err    = [np.sqrt(risultati_elaborati[t]["pstd_ref"]**2    + ub_ref_lsb**2)    for t in temp_nominali]
    sensor_err = [np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + ub_sensor_lsb**2) for t in temp_nominali]

    axs2[0].set_title("ref vs sensor [LSB]", fontsize=12)
    axs2[0].errorbar(ref_val, ref_val, xerr=ref_err, yerr=ref_err, fmt=".", color="b", ecolor="b", capsize=5, label="ref")
    axs2[0].errorbar(ref_val, sensor_val, yerr=sensor_err, fmt=".", color="r", ecolor="r", capsize=5, label="sensor")
    if ref_val:
        mn = min(min(ref_val), min(sensor_val))
        mx = max(max(ref_val), max(sensor_val))
        axs2[0].plot([mn, mx], [mn, mx], "k:", alpha=0.5, label="identity")
    axs2[0].set_xlabel("ref [LSB]")
    axs2[0].set_ylabel("sensor [LSB]")
    axs2[0].grid(True)
    axs2[0].legend()

    errore = np.array(sensor_val) - np.array(ref_val)
    uc_combined = np.array([np.sqrt(se**2 + re**2) for se, re in zip(sensor_err, ref_err)])
    axs2[1].set_title("error: sensor − ref [LSB]", fontsize=12)
    axs2[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs2[1].errorbar(temp_nominali, errore, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="error ± u_c [LSB]")
    axs2[1].grid(True, alpha=0.3)
    axs2[1].set_xlabel(f"nominal [{unit_symbol}]")
    axs2[1].set_ylabel("sensor − ref [LSB]")
    axs2[1].legend()

    fig5, axs5 = plt.subplots(1, 2, figsize=(18, 10))
    sensor_cal = [a * risultati_elaborati[t]["pmean_sensor"] + b for t in temp_nominali]
    axs5[0].set_title("Calibration curve [LSB]", fontsize=12)
    axs5[0].errorbar(sensor_val, sensor_cal, fmt=".", color="r", ecolor="r", capsize=5)
    axs5[0].plot(sensor_val, ref_val, "b", linewidth=0.7, label="ref function")
    axs5[0].plot(sensor_val, sensor_cal, "r", linewidth=1, label="Calibration function")
    axs5[0].plot(sensor_val, sensor_val, color="grey", linestyle="--", linewidth=1, label="Readings")
    for i, (x_i, y_i, t_i) in enumerate(zip(sensor_val, sensor_cal, temp_nominali)):
        axs5[0].annotate(f"{t_i:.0f}", (x_i, y_i),
                         textcoords="offset points", xytext=(4, -8),
                         fontsize=7, alpha=0.7, color="tab:red")
    axs5[0].set_xlabel("sensor X [LSB]")
    axs5[0].set_ylabel("LSB")
    axs5[0].grid(True)
    axs5[0].legend()

    errore_tarato = np.array(sensor_cal) - np.array(ref_val)
    axs5[1].set_title("Post-calibration error [LSB]", fontsize=12)
    axs5[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs5[1].errorbar(temp_nominali, errore_tarato, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="error_cal ± u_c [LSB]")
    axs5[1].grid(True, alpha=0.3)
    axs5[1].set_xlabel(f"nominal [{unit_symbol}]")
    axs5[1].set_ylabel("sensor_cal − ref [LSB]")
    axs5[1].legend()

    plt.tight_layout()
    plt.show()


def save_charts(
    a: float, b: float,
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_ref_lsb: float,
    ub_sensor_lsb: float,
    output_dir: Path,
    prefix: str = "calib_linear",
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
    _calib_result: Dict[str, Any] | None = None,
    coverage_factor: float = 2.0,
) -> List[Path]:
    # 5 standard charts — pulls GUM budget from calibrate() result when available
    from .calib_plots import bundle_from_linear, save_five_charts

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    ub_ref_y = ub_ref_lsb / lsb_per_y

    if _calib_result is not None:
        result = _calib_result
    else:
        u_res = 0.1 / np.sqrt(12.0)
        uB_i = ub_sensor_lsb / lsb_per_y
        exp_unc = []
        for t in temp_nominali:
            uA_ref = risultati_elaborati[t]["pstd_ref"]
            uA_i   = risultati_elaborati[t]["pstd_sensor"] / lsb_per_y
            mu_E   = np.sqrt((uA_ref**2 + ub_ref_y**2) +
                             (uA_i**2 + uB_i**2 + u_res**2))
            exp_unc.append(float(coverage_factor * mu_E))
        result = {
            "model": "linear",
            "A": a, "B": b,
            "temp_nominali": temp_nominali,
            "risultati_elaborati": risultati_elaborati,
            "ref_temp_means": [float(risultati_elaborati[t]["pmean_ref"]) for t in temp_nominali],
            "expanded_uncertainties": exp_unc,
            "ub_ref_y":      ub_ref_y,
            "ub_ref_lsb":    ub_ref_lsb,
            "ub_sensor_lsb": ub_sensor_lsb,
        }

    bundle = bundle_from_linear(
        calib_result=result,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        measurand_label=measurand_label,
        sensor_label=sensor_label,
        ref_label=ref_label,
        accuracy_limit=accuracy_limit,
    )
    bundle.sample_data = risultati_elaborati
    bundle.sample_size  = sample_size

    return save_five_charts(bundle, Path(output_dir), prefix)


def main() -> None:
    import sys
    scripts_dir = Path(__file__).resolve().parent.parent
    calib_root  = scripts_dir.parent
    models_dir  = calib_root / "models_in"

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from calib_utils import _lookup

    sensor_json = json.loads((models_dir / "sensors" / "ntc_temperature.json").read_text(encoding="utf-8"))

    _phys_dsi   = sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    _unit_sym   = dsi_to_symbol(_phys_dsi)
    _measurand  = sensor_json.get("type", "measurand").capitalize()

    print(f"measurand: {_measurand}")
    print(f"unit:      {_unit_sym}")

    default_input_json  = calib_root / "test" / "data_in" / "export2_tmp126_lsb16.json"
    default_report_path = calib_root / "certificato_out" / "calibration_report_linear.md"

    adc_bits      = sensor_json.get("ranges", {}).get("elec", {}).get("adcBits", 16)
    adc_max       = float((1 << adc_bits) - 1)
    lsb_min       = float(sensor_json.get("ranges", {}).get("threshold", {}).get("min", -40.0))
    lsb_max       = float(sensor_json.get("ranges", {}).get("threshold", {}).get("max", 105.0))
    ub_ref_y     = 0.0325
    _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    _risol_lsb  = float(_lookup(_sensor_ru, "varName", "resolution", {}).get("value", 1))
    _lsb_per_y  = adc_max / (lsb_max - lsb_min) if lsb_max != lsb_min else 452.0
    risol       = _risol_lsb / _lsb_per_y
    ub_sensor_lsb = float(_lookup(_sensor_ru, "varName", "uB", {}).get("value", 0.30))

    parser = argparse.ArgumentParser(description=f"Linear calibration (GUM OLS, mixed domain: X [LSB], Y [{_unit_sym}]) — standalone")
    parser.add_argument("--input",   type=Path, default=default_input_json)
    parser.add_argument("--report",  type=Path, default=default_report_path)
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    payload   = json.loads(args.input.read_text(encoding="utf-8"))
    lsb_scale = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}

    result = calibrate(
        payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=20,
        adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
        verbose=args.verbose, risol=risol, unit_symbol=_unit_sym,
    )

    report = build_report(
        temp_nominali=result["temp_nominali"],
        risultati_elaborati=result["risultati_elaborati"],
        a=result["A"], b=result["B"], u_a=result["u_A"], u_b=result["u_B"],
        cov_ab=result["cov_AB"], adc_bits=adc_bits,
        lsb_scale_sensor_info=lsb_scale, adc_max=adc_max,
        ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
        unit_symbol=_unit_sym,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print(f"\n=== Calibration result (linear OLS, GUM, mixed domain X[LSB]→Y[{_unit_sym}]) ===")
    print(json.dumps({"model": result["model"], "A": result["A"], "B": result["B"],
                      "u_A": result["u_A"], "u_B": result["u_B"], "cov_AB": result["cov_AB"],
                      f"expanded_uncertainties_{_unit_sym}": result["expanded_uncertainties"]}, indent=2))
    print(f"\nReport written to: {args.report}")

    if args.charts:
        try:
            plot_charts(
                a=result["A"], b=result["B"],
                temp_nominali=result["temp_nominali"], dati_raw=result["dati_raw"],
                risultati_elaborati=result["risultati_elaborati"], sample_size=20,
                lsb_scale_sensor_info=lsb_scale, adc_max=adc_max,
                ub_ref_lsb=result["ub_ref_lsb"], ub_sensor_lsb=ub_sensor_lsb,
                unit_symbol=_unit_sym,
            )
        except Exception as ex:
            print(f"\nCharts disabled: {ex}")


if __name__ == "__main__":
    main()
