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
    # Reference readings are stored in °C (their native unit from the PT100).
    # We no longer convert them to synthetic LSB: the calibration function maps
    # D_out [LSB] -> T [°C] directly, so the reference axis stays in °C.
    ref_by_step: Dict[int, List[float]] = {}
    for sample in payload.get("reference_temperature_samples", []):
        idx    = int(sample["index_step"])
        temp_c = float(sample["reading"])
        ref_by_step.setdefault(idx, []).append(temp_c)   # °C, not LSB

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
) -> Tuple[bool, Dict[str, np.ndarray], Dict[str, Any]]:
    if temp_nominale not in temp_nominali:
        return False, {}, {}

    step_idx = temp_nominali.index(temp_nominale)
    ref_by_step, sensor_by_step = _build_step_index_maps(payload, lsb_scale, adc_max)

    arr_rtd_degc   = np.array(ref_by_step.get(step_idx, []), dtype=float)  # °C
    sensor_frames  = sensor_by_step.get(step_idx, [])

    if arr_rtd_degc.size == 0 or len(sensor_frames) == 0:
        return False, {}, {}

    buffer_log_lsb: List[float] = []
    for frame_values in sensor_frames:
        frame_arr = np.array(frame_values, dtype=float)
        if frame_arr.size > 0:
            buffer_log_lsb.append(float(np.mean(frame_arr)))

    if not buffer_log_lsb:
        return False, {}, {}

    n_total   = min(arr_rtd_degc.size, len(buffer_log_lsb))
    n_campioni = n_total // sample_size
    n_keep    = n_campioni * sample_size
    if n_keep == 0:
        return False, {}, {}

    arr_rtd_clean = arr_rtd_degc[:n_keep]           # °C
    arr_log_clean = np.array(buffer_log_lsb[:n_keep], dtype=float)  # LSB

    matrix_rtd = arr_rtd_clean.reshape((n_campioni, sample_size))
    matrix_log = arr_log_clean.reshape((n_campioni, sample_size))

    sample_mean_rtd = np.mean(matrix_rtd, axis=1)   # °C per block
    campioni_std_rtd = np.std(matrix_rtd, axis=1, ddof=1)
    sample_mean_log = np.mean(matrix_log, axis=1)   # LSB per block
    campioni_std_log = np.std(matrix_log, axis=1, ddof=1)

    pmean_rtd = float(np.mean(sample_mean_rtd))     # °C
    pstd_rtd  = float(np.std(sample_mean_rtd, ddof=1) / np.sqrt(n_campioni)) if n_campioni > 1 else 0.0   # °C
    pmean_log = float(np.mean(sample_mean_log))     # LSB
    pstd_log  = float(np.std(sample_mean_log, ddof=1) / np.sqrt(n_campioni)) if n_campioni > 1 else 0.0   # LSB

    glob_smean_rtd = float(np.mean(arr_rtd_clean))
    glob_std_rtd   = float(np.std(arr_rtd_clean, ddof=1)) if n_keep > 1 else 0.0
    glob_smean_log = float(np.mean(arr_log_clean))
    glob_std_log   = float(np.std(arr_log_clean, ddof=1)) if n_keep > 1 else 0.0

    # max_error / mean_error: within-step spread of the sensor readings [LSB].
    # Reference is now in °C so a direct LSB-vs-reference subtraction is not
    # meaningful here.  These fields are informational (used in verbose reports).
    err        = np.abs(arr_log_clean - pmean_log)
    max_error  = float(np.max(err)) if err.size else float("nan")
    mean_error = float(np.mean(err)) if err.size else float("nan")

    if verbose:
        print(f"\n--- {temp_nominale}°C : {n_keep} misure ({n_campioni} campioni) ---")
        print(f"  RTD: {pmean_rtd:.4f} +/- {pstd_rtd:.6f} °C")
        print(f"  Log: {pmean_log:.2f} +/- {pstd_log:.4f} LSB  ({lsb16_to_phys(np.array([pmean_log]), lsb_scale, adc_max)[0]:.4f} °C)")
        print(f"  Max sensor spread: {max_error:.4f} LSB")

    dati_raw_item = {"rtd": arr_rtd_clean, "log": arr_log_clean}
    risultati_item = {
        "x_axis": np.arange(n_campioni),
        "smean_rtd": sample_mean_rtd, "std_rtd": campioni_std_rtd,
        "smean_log": sample_mean_log, "std_log": campioni_std_log,
        "pmean_rtd": pmean_rtd, "pstd_rtd": pstd_rtd,
        "pmean_log": pmean_log, "pstd_log": pstd_log,
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
) -> Tuple[Dict[float, Dict[str, np.ndarray]], Dict[float, Dict[str, Any]]]:
    dati_raw: Dict[float, Dict[str, np.ndarray]] = {}
    risultati_elaborati: Dict[float, Dict[str, Any]] = {}
    for t in temp_nominali:
        ok, raw_item, result_item = _compute_step_statistics(payload, temp_nominali, t, sample_size, lsb_scale, adc_max, verbose)
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
    # GUM OLS: y = A*x + B in LSB domain
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
    check_units: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run all pre-calibration checks for the linear model.
    Checks performed:
      1. Node count — payload must have at least MIN_STEPS_LINEAR steps.
      2. Unit check — pint-based dimensional analysis on sensor/ref JSON (optional).
    """
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

    if check_units and sensor_json is not None and ref_json is not None:
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
    ub_pt_degc: float | None = None,  # type-B std uncertainty of the reference [°C]
    ub_tmp_lsb: float = 0.0,          # type-B std uncertainty of the NTC ADC [LSB]
    verbose: bool = False,
    risol_degc: float = 0.1,
    old_a: float | None = None,
    old_b: float | None = None,
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    convert_units: bool = False,
    # legacy alias — callers that still pass ub_pt_lsb get forwarded correctly
    ub_pt_lsb: float | None = None,
) -> Dict[str, Any]:
    # Backwards-compat shim: if caller used the old ub_pt_lsb keyword arg,
    # convert it to °C using the sensor range from lsb_scale_sensor_info.
    if ub_pt_lsb is not None and ub_pt_degc is None:
        min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
        lsb_per_c_local = adc_max / max(max_v - min_v, 1e-12)
        ub_pt_degc = ub_pt_lsb / lsb_per_c_local
    if ub_pt_degc is None:
        raise ValueError("calibrate() requires either ub_pt_degc [°C] or ub_pt_lsb [LSB]")

    pre = run_prechecks(payload, sensor_json, ref_json, check_units, verbose)
    unit_check_result = pre["unit_check"]
    if not pre["ok"]:
        raise ValueError("\n".join(pre["errors"]))

    temp_nominali = [parse_step(s)[0] for s in payload.get("steps", [])]

    dati_raw, risultati_elaborati = _get_data(payload, temp_nominali, sample_size, lsb_scale_sensor_info, adc_max, verbose)

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)   # kept as informational value only

    if verbose:
        print("\n\n --- Fine acquisizione dati ---")

    # x = NTC sensor readings [LSB]; y = PT100 reference readings [°C]
    # The calibration function T [°C] = A [°C/LSB] * D [LSB] + B [°C]
    x = np.array([risultati_elaborati[t]["pmean_log"] for t in temp_nominali], dtype=float)  # LSB
    y = np.array([risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali], dtype=float)  # °C

    # u_x [LSB], u_y [°C] — heterogeneous units, consistent with the mixed domain
    u_res_degC = risol_degc / np.sqrt(12.0)
    uc_tmp = np.array([np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali], dtype=float)   # LSB
    uc_pt  = np.array([np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_degc**2)  for t in temp_nominali], dtype=float)  # °C

    if old_a is not None and old_b is not None:
        # old_a was A [°C/LSB], old_b was B [°C] (post-refactor convention)
        # or old_a≈1 and old_b was in LSB (legacy). We accept both: if |old_b| > 300
        # it is almost certainly a legacy LSB offset; convert it.
        _old_b_c = old_b / lsb_per_c if abs(old_b) > 300 else old_b
        y_old = old_a * x + _old_b_c
        err_old_degC = y_old - y
        if verbose:
            print(f"\n--- Baseline pre-fit error (old A/B) ---")
            print(f"old A = {old_a:.10f}  old B = {_old_b_c:.6f} °C")
            print(f"signed mean error: {np.mean(err_old_degC):.6f} °C")

    a, b, u_a, u_b, cov_ab = _compute_gum_ols_coefficients(x, y, uc_tmp, uc_pt)
    # a [°C/LSB], b [°C], u_a [°C/LSB], u_b [°C]

    if verbose:
        print(f"\nA = {a:.10f} °C/LSB  B = {b:.6f} °C")
        print(f"u(A) = {u_a:.10f} °C/LSB  u(B) = {u_b:.6f} °C")
        print(f"cov(A,B) = {cov_ab:.10f}")

    # ------------------------------------------------------------------
    # GUM uncertainty budget per step — everything in °C.
    #
    # Sensor type-A: pstd_log [LSB] * |A| [°C/LSB] = °C
    # Sensor type-B: ub_tmp_lsb [LSB] * |A| [°C/LSB] = °C
    # Reference type-A: pstd_rtd [°C] — directly
    # Reference type-B: ub_pt_degc [°C] — directly
    # Resolution: risol_degc / sqrt(12) [°C]
    # ------------------------------------------------------------------
    sens = abs(a)   # local sensitivity dT/dD [°C/LSB] — constant for linear model

    expanded_uncertainties: List[float] = []
    per_step_u_budget_raw: List[Tuple] = []

    for t in temp_nominali:
        uA_ref   = risultati_elaborati[t]["pstd_rtd"]          # °C (already)
        uA_i     = risultati_elaborati[t]["pstd_log"] * sens    # LSB * °C/LSB = °C
        uB_i_degC = ub_tmp_lsb * sens                          # LSB * °C/LSB = °C
        mu_T_ref = np.sqrt(uA_ref**2 + ub_pt_degc**2)
        mu_T_i   = np.sqrt(uA_i**2 + uB_i_degC**2 + u_res_degC**2)
        mu_E     = np.sqrt(mu_T_ref**2 + mu_T_i**2)
        U_E      = 2.0 * mu_E
        expanded_uncertainties.append(float(U_E))
        per_step_u_budget_raw.append((t, uA_ref, uA_i, mu_T_ref, mu_T_i, mu_E, U_E))

    # ref_temp_means: already in °C (pmean_rtd is now °C)
    ref_temp_means: List[float] = [
        float(risultati_elaborati[t]["pmean_rtd"])
        for t in temp_nominali
    ]

    u_budget_per_step: List[Dict[str, float]] = [
        {"t_nom_degC": t, "uA_ref_degC": uA_ref, "uA_i_degC": uA_i,
         "u_T_ref_degC": mu_T_ref_, "u_T_i_degC": mu_T_i_,
         "u_c_degC": mu_E_, "U_exp_degC": U_E_, "k": 2.0}
        for t, uA_ref, uA_i, mu_T_ref_, mu_T_i_, mu_E_, U_E_ in per_step_u_budget_raw
    ]

    result: Dict[str, Any] = {
        "model": "linear",
        "A": float(a),   # [°C/LSB]
        "B": float(b),   # [°C]
        "u_A": float(u_a), "u_B": float(u_b), "cov_AB": float(cov_ab),
        "old_A": None if old_a is None else float(old_a),
        "old_B": None if old_b is None else float(old_b),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "u_budget_per_step": u_budget_per_step,
        "ref_temp_means": ref_temp_means,
        "lsb_per_c": lsb_per_c,      # informational only
        "ub_pt_degc": ub_pt_degc,    # [°C]
        "ub_tmp_lsb": ub_tmp_lsb,    # [LSB]
        # legacy keys for callers that still read ub_pt_lsb from the result
        "ub_pt_lsb": ub_pt_degc * lsb_per_c,
    }
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
    ub_pt_lsb: float = 0.0,    # kept for backward compat; ignored when ub_pt_degc given
    ub_tmp_lsb: float = 0.0,
    ub_pt_degc: float | None = None,
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)
    _ub_pt_c = ub_pt_degc if ub_pt_degc is not None else ub_pt_lsb / lsb_per_c

    lines: List[str] = []
    lines.append("# Calibration Report — Linear OLS (mixed domain: sensor [LSB], reference [°C])")
    lines.append("")
    lines.append("## Per-step statistics")
    lines.append("")
    lines.append("| step | target [°C] | pmean_ref [°C] | pstd_ref [°C] | pmean_sensor [LSB] | pstd_sensor [LSB] | max_sensor_spread [LSB] |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for i, t in enumerate(temp_nominali):
        r = risultati_elaborati[t]
        lines.append(f"| {i} | {t:.3f} | {r['pmean_rtd']:.4f} | {r['pstd_rtd']:.6f} | {r['pmean_log']:.2f} | {r['pstd_log']:.4f} | {r['max_error']:.4f} |")

    lines.append("")
    lines.append("## Calibration coefficients (T_ref [°C] = A [°C/LSB] * D [LSB] + B [°C])")
    lines.append("")
    lines.append(f"- A: {a:.10f} °C/LSB")
    lines.append(f"- B: {b:.6f} °C")
    lines.append(f"- u(A): {u_a:.10f} °C/LSB")
    lines.append(f"- u(B): {u_b:.6f} °C")
    lines.append(f"- cov(A,B): {cov_ab:.10f}")
    if u_a > 0 and u_b > 0:
        lines.append(f"- corr(A,B): {cov_ab / (u_a * u_b):.6f}")

    lines.append("")
    lines.append("## Uncertainty budget")
    lines.append(f"- LSB scale (informational): [{min_v}, {max_v}] °C  →  {lsb_per_c:.4f} LSB/°C")
    lines.append(f"- Raw sensor format: unsigned {adc_bits}-bit LSB")
    lines.append(f"- PT100 u_B: {_ub_pt_c:.6f} °C")
    lines.append(f"- NTC u_B:   {ub_tmp_lsb:.4f} LSB  ×  |A| = {ub_tmp_lsb * abs(a):.6f} °C")

    return "\n".join(lines) + "\n"


def plot_charts(
    a: float, b: float,
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)

    fig1, axs = plt.subplots(2, 3, figsize=(18, 10))
    axcmeanflat = axs.flatten()
    fig1.suptitle(f"Analisi Campioni (n={sample_size}) [LSB]", fontsize=16)
    for i, temp in enumerate(temp_nominali):
        ax = axcmeanflat[i]
        if temp in risultati_elaborati:
            res = risultati_elaborati[temp]
            x = res["x_axis"]
            ub_pt_band  = np.sqrt(res["std_rtd"]**2 + ub_pt_lsb**2)
            ub_tmp_band = np.sqrt(res["std_log"]**2 + ub_tmp_lsb**2)
            ax.plot(x, res["smean_rtd"], "b-o", label="RTD",    linewidth=1, markersize=2)
            ax.plot(x, res["smean_rtd"] + ub_pt_band,  "b--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_rtd"] - ub_pt_band,  "b--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_log"], "r-o", label="Sensor", linewidth=1, markersize=2)
            ax.plot(x, res["smean_log"] + ub_tmp_band, "r--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_log"] - ub_tmp_band, "r--", alpha=0.4, linewidth=1)
            ax.set_title(f"Nominale: {temp} C")
            ax.set_ylabel("LSB")
            ax.grid(True, alpha=0.3)
            if i == 0:
                ax.legend()
        else:
            ax.text(0.5, 0.5, "Dati assenti", ha="center")

    fig2, axs2 = plt.subplots(1, 2, figsize=(18, 10))
    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_lsb**2) for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali]

    axs2[0].set_title("Confronto: PT100 vs NTC [LSB]", fontsize=12)
    axs2[0].errorbar(rtd_val, rtd_val, xerr=rtd_err, yerr=rtd_err, fmt=".", color="b", ecolor="b", capsize=5, label="RTD")
    axs2[0].errorbar(rtd_val, log_val, yerr=log_err, fmt=".", color="r", ecolor="r", capsize=5, label="NTC")
    if rtd_val:
        mn = min(min(rtd_val), min(log_val))
        mx = max(max(rtd_val), max(log_val))
        axs2[0].plot([mn, mx], [mn, mx], "k:", alpha=0.5, label="Ideale")
    axs2[0].set_xlabel("RTD (PT100) [LSB]")
    axs2[0].set_ylabel("NTC [LSB]")
    axs2[0].grid(True)
    axs2[0].legend()

    errore = np.array(log_val) - np.array(rtd_val)
    uc_combined = np.array([np.sqrt(le**2 + re**2) for le, re in zip(log_err, rtd_err)])
    axs2[1].set_title("Distribuzione errore NTC − RTD [LSB]", fontsize=12)
    axs2[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs2[1].errorbar(temp_nominali, errore, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="errore ± u_c [LSB]")
    axs2[1].grid(True, alpha=0.3)
    axs2[1].set_xlabel("Temperatura nominale [°C]")
    axs2[1].set_ylabel("Errore NTC − RTD [LSB]")
    axs2[1].legend()

    fig5, axs5 = plt.subplots(1, 2, figsize=(18, 10))
    log_val_tarato = [a * risultati_elaborati[t]["pmean_log"] + b for t in temp_nominali]
    axs5[0].set_title("Curva di taratura NTC [LSB]", fontsize=12)
    axs5[0].errorbar(log_val, log_val_tarato, xerr=log_err, fmt=".", color="r", ecolor="r", capsize=5)
    axs5[0].plot(log_val, rtd_val, "b", linewidth=0.7, label="RTD function")
    axs5[0].plot(log_val, log_val_tarato, "r", linewidth=1, label="Calibration function")
    axs5[0].plot(log_val, log_val, color="grey", linestyle="--", linewidth=1, label="Readings")
    axs5[0].set_xlabel("NTC reading [LSB]")
    axs5[0].set_ylabel("LSB")
    axs5[0].grid(True)
    axs5[0].legend()

    errore_tarato = np.array(log_val_tarato) - np.array(rtd_val)
    axs5[1].set_title("Errore post-taratura NTC [LSB]", fontsize=12)
    axs5[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs5[1].errorbar(temp_nominali, errore_tarato, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="errore_tarato ± u_c [LSB]")
    axs5[1].grid(True, alpha=0.3)
    axs5[1].set_xlabel("Temperatura nominale [°C]")
    axs5[1].set_ylabel("Errore NTC calibrato − RTD [LSB]")
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
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    output_dir: Path,
    prefix: str = "calib_linear",
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
    _calib_result: Dict[str, Any] | None = None,
) -> List[Path]:
    """Produce 5 calibration charts for the linear OLS model.

    Passes ``_calib_result`` (the full dict returned by ``calibrate()``) to
    ``calib_plots.bundle_from_linear`` so that the GUM per-point budget
    (``u_budget_per_step``) is available for correct uncertainty error bars.
    When ``_calib_result`` is not provided the budget is recomputed from the
    raw statistics passed in, giving identical results.
    """
    from .calib_plots import bundle_from_linear, save_five_charts

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    # ub_pt_lsb arriving here is always in LSB (legacy key from result dict).
    # Convert to °C for the bundle builder.
    ub_pt_degc = ub_pt_lsb / lsb_per_c

    if _calib_result is not None:
        # Prefer the real result dict — it has ub_pt_degc and u_budget_per_step.
        result = _calib_result
    else:
        # Build a minimal result dict from the individual arguments.
        u_res = 0.1 / np.sqrt(12.0)
        uB_i_degC = ub_tmp_lsb / lsb_per_c
        exp_unc = []
        for t in temp_nominali:
            uA_ref = risultati_elaborati[t]["pstd_rtd"]       # [°C]
            uA_i   = risultati_elaborati[t]["pstd_log"] / lsb_per_c
            mu_E   = np.sqrt((uA_ref**2 + ub_pt_degc**2) +
                             (uA_i**2 + uB_i_degC**2 + u_res**2))
            exp_unc.append(float(2.0 * mu_E))
        result = {
            "model": "linear",
            "A": a, "B": b,
            "temp_nominali": temp_nominali,
            "risultati_elaborati": risultati_elaborati,
            "ref_temp_means": [float(risultati_elaborati[t]["pmean_rtd"]) for t in temp_nominali],
            "expanded_uncertainties": exp_unc,
            "ub_pt_degc": ub_pt_degc,
            "ub_pt_lsb":  ub_pt_lsb,
            "ub_tmp_lsb": ub_tmp_lsb,
        }

    bundle = bundle_from_linear(
        calib_result=result,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        accuracy_limit=accuracy_limit,
    )
    bundle.sample_data = risultati_elaborati
    bundle.sample_size  = sample_size

    return save_five_charts(bundle, Path(output_dir), prefix)

    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_lsb**2) for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali]

    fig2, axs2 = plt.subplots(1, 2, figsize=(18, 10))
    axs2[0].set_title("Confronto: PT100 vs NTC [LSB]", fontsize=12)
    axs2[0].errorbar(rtd_val, rtd_val, xerr=rtd_err, yerr=rtd_err, fmt=".", color="b", ecolor="b", capsize=5, label="RTD")
    axs2[0].errorbar(rtd_val, log_val, yerr=log_err, fmt=".", color="r", ecolor="r", capsize=5, label="NTC")
    if rtd_val:
        mn = min(min(rtd_val), min(log_val))
        mx = max(max(rtd_val), max(log_val))
        axs2[0].plot([mn, mx], [mn, mx], "k:", alpha=0.5, label="Ideale")
    for i, txt in enumerate(temp_nominali):
        axs2[0].annotate(f"{txt}C", (rtd_val[i], log_val[i]), xytext=(5, -5), textcoords="offset points")
    axs2[0].set_xlabel("RTD (PT100) [LSB]")
    axs2[0].set_ylabel("NTC [LSB]")
    axs2[0].grid(True)
    axs2[0].legend()

    errore = np.array(log_val) - np.array(rtd_val)
    uc_combined = np.array([np.sqrt(le**2 + re**2) for le, re in zip(log_err, rtd_err)])
    axs2[1].set_title("Distribuzione errore NTC − RTD [LSB]", fontsize=12)
    axs2[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs2[1].errorbar(temp_nominali, errore, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="errore ± u_c [LSB]")
    axs2[1].grid(True, alpha=0.3)
    axs2[1].set_xlabel("Temperatura nominale [°C]")
    axs2[1].set_ylabel("Errore NTC − RTD [LSB]")
    axs2[1].legend()
    p2 = output_dir / f"{prefix}_fig2_comparison.png"
    fig2.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    saved.append(p2)

    log_val_tarato = [a * risultati_elaborati[t]["pmean_log"] + b for t in temp_nominali]
    errore_tarato  = np.array(log_val_tarato) - np.array(rtd_val)
    fig5, axs5 = plt.subplots(1, 2, figsize=(18, 10))
    axs5[0].set_title("Curva di taratura NTC [LSB]", fontsize=12)
    axs5[0].errorbar(log_val, log_val_tarato, xerr=log_err, fmt=".", color="r", ecolor="r", capsize=5)
    axs5[0].plot(log_val, rtd_val, "b", linewidth=0.7, label="RTD function")
    axs5[0].plot(log_val, log_val_tarato, "r", linewidth=1, label="Calibration function")
    axs5[0].plot(log_val, log_val, color="grey", linestyle="--", linewidth=1, label="Readings")
    axs5[0].set_xlabel("NTC reading [LSB]")
    axs5[0].set_ylabel("LSB")
    axs5[0].grid(True)
    axs5[0].legend()
    axs5[1].set_title("Errore post-taratura NTC [LSB]", fontsize=12)
    axs5[1].axhline(0, color="k", linestyle="--", alpha=0.7)
    axs5[1].errorbar(temp_nominali, errore_tarato, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, label="errore_tarato ± u_c [LSB]")
    axs5[1].grid(True, alpha=0.3)
    axs5[1].set_xlabel("Temperatura nominale [°C]")
    axs5[1].set_ylabel("Errore NTC calibrato − RTD [LSB]")
    axs5[1].legend()
    p5 = output_dir / f"{prefix}_fig5_calibration_curve.png"
    fig5.savefig(p5, dpi=150, bbox_inches="tight")
    plt.close(fig5)
    saved.append(p5)

    return saved


def main() -> None:
    import sys
    scripts_dir = Path(__file__).resolve().parent.parent
    calib_root  = scripts_dir.parent
    models_dir  = calib_root / "models_in"

    if str(models_dir) not in sys.path:
        sys.path.insert(0, str(models_dir))

    from VAR_REF_SENSOR import SENSOR_model, VAR_extra

    sensor = SENSOR_model()
    extra  = VAR_extra()

    default_input_json  = calib_root / "test" / "data_in" / "export2_tmp126_lsb16.json"
    default_report_path = calib_root / "certificato_out" / "calibration_report_linear.md"

    adc_bits   = extra._adc_bits
    adc_max    = float((1 << adc_bits) - 1)
    lsb_min    = sensor._minPhyThreshold
    lsb_max    = sensor._maxPhyThreshold

    ub_pt_degc = extra._U_pt_c / extra._k_pt          # [°C] — no lsb_per_c needed
    ub_tmp_lsb = sensor.uB                             # [LSB] from sensor JSON

    parser = argparse.ArgumentParser(description="NTC linear calibration (GUM OLS, mixed domain) — standalone")
    parser.add_argument("--input",   type=Path, default=default_input_json)
    parser.add_argument("--report",  type=Path, default=default_report_path)
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    payload   = json.loads(args.input.read_text(encoding="utf-8"))
    lsb_scale = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}

    result = calibrate(
        payload=payload, lsb_scale_sensor_info=lsb_scale, sample_size=20,
        adc_max=adc_max, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
        verbose=args.verbose,
    )

    report = build_report(
        temp_nominali=result["temp_nominali"],
        risultati_elaborati=result["risultati_elaborati"],
        a=result["A"], b=result["B"], u_a=result["u_A"], u_b=result["u_B"],
        cov_ab=result["cov_AB"], adc_bits=adc_bits,
        lsb_scale_sensor_info=lsb_scale, adc_max=adc_max,
        ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print("\n=== Calibration result (linear OLS, GUM) ===")
    print(json.dumps({"model": result["model"], "A": result["A"], "B": result["B"],
                      "u_A": result["u_A"], "u_B": result["u_B"], "cov_AB": result["cov_AB"],
                      "expanded_uncertainties_degC": result["expanded_uncertainties"]}, indent=2))
    print(f"\nReport written to: {args.report}")

    if args.charts:
        try:
            plot_charts(
                a=result["A"], b=result["B"],
                temp_nominali=result["temp_nominali"], dati_raw=result["dati_raw"],
                risultati_elaborati=result["risultati_elaborati"], sample_size=20,
                lsb_scale_sensor_info=lsb_scale, adc_max=adc_max,
                ub_pt_lsb=result["ub_pt_lsb"], ub_tmp_lsb=ub_tmp_lsb,
            )
        except Exception as ex:
            print(f"\nCharts disabled: {ex}")


if __name__ == "__main__":
    main()
