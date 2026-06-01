"""
NTC_linear_calibration.py
==========================
GUM-compliant OLS linear calibration for NTC thermistors in the 16-bit LSB domain.

Public API
----------
calibrate(payload, lsb_scale_sensor_info, sample_size, adc_max,
          ub_pt_lsb, ub_tmp_lsb, verbose) -> dict
    Run the full calibration pipeline and return coefficients + per-step data.

plot_charts(...)
    Render matplotlib diagnostic charts (called when --charts is set).

Standalone usage
----------------
    python NTC_linear_calibration.py [--input PATH] [--charts] [--verbose]

Default input: export2_tmp126_lsb16.json in the same directory.
    Default parameters match the NTC/TMP126 setup in VAR_REF_SENSOR.py.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Utility helpers (kept here so this module is self-contained)
# ---------------------------------------------------------------------------


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


def phys_to_lsb16(
    values: np.ndarray, sensor_info: Dict[str, Any], adc_max: float
) -> np.ndarray:
    min_v, max_v = get_scale_from_sensor(sensor_info)
    span = max(max_v - min_v, 1e-12)
    raw = np.round((values - min_v) / span * adc_max)
    return np.clip(raw, 0.0, adc_max)


def lsb16_to_phys(
    values: np.ndarray, sensor_info: Dict[str, Any], adc_max: float
) -> np.ndarray:
    min_v, max_v = get_scale_from_sensor(sensor_info)
    span = max(max_v - min_v, 1e-12)
    return min_v + (values / adc_max) * span


# ---------------------------------------------------------------------------
# Step data extraction
# ---------------------------------------------------------------------------


def _build_index_maps_lsb(
    payload: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
) -> Tuple[Dict[int, List[float]], Dict[int, List[List[float]]]]:
    """Build per-step index maps: PT100 in LSB, NTC already in LSB."""
    ref_by_step: Dict[int, List[float]] = {}
    for sample in payload.get("reference_temperature_samples", []):
        idx = int(sample["index_step"])
        temp_c = float(sample["reading"])
        lsb_val = float(
            phys_to_lsb16(np.array([temp_c]), lsb_scale_sensor_info, adc_max)[0]
        )
        ref_by_step.setdefault(idx, []).append(lsb_val)

    sensor_by_step: Dict[int, List[List[float]]] = {}
    for frame in payload.get("sensor_raw_samples", []):
        idx = int(frame["index_step"])
        if "value" in frame:
            raw_values = frame["value"]
        elif "value_hex" in frame:
            hex_s = str(frame["value_hex"]).strip()
            raw_values = [
                int(hex_s[i : i + 4], 16)
                for i in range(0, len(hex_s), 4)
                if len(hex_s[i : i + 4]) == 4
            ]
        else:
            raw_values = []
        sensor_by_step.setdefault(idx, []).append([float(v) for v in raw_values])

    return ref_by_step, sensor_by_step


def _analyze_step_lsb(
    payload: Dict[str, Any],
    temp_nominali: List[float],
    temp_nominale: float,
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    verbose: bool,
) -> Tuple[bool, Dict[str, np.ndarray], Dict[str, Any]]:
    """Statistical analysis for one calibration step, entirely in LSB."""
    if temp_nominale not in temp_nominali:
        if verbose:
            print(f"Temperatura nominale non trovata: {temp_nominale}C")
        return False, {}, {}

    step_idx = temp_nominali.index(temp_nominale)
    ref_by_step, sensor_by_step = _build_index_maps_lsb(
        payload, lsb_scale_sensor_info, adc_max
    )

    arr_rtd_lsb = np.array(ref_by_step.get(step_idx, []), dtype=float)
    sensor_frames = sensor_by_step.get(step_idx, [])

    if arr_rtd_lsb.size == 0 or len(sensor_frames) == 0:
        if verbose:
            print(f"Dati assenti per {temp_nominale}C")
        return False, {}, {}

    buffer_log_lsb: List[float] = []
    for frame_values in sensor_frames:
        frame_arr = np.array(frame_values, dtype=float)
        if frame_arr.size == 0:
            continue
        buffer_log_lsb.append(float(np.mean(frame_arr)))

    if not buffer_log_lsb:
        if verbose:
            print(f"Nessun frame sensore valido per {temp_nominale}C")
        return False, {}, {}

    n_total = min(arr_rtd_lsb.size, len(buffer_log_lsb))
    n_campioni = n_total // sample_size
    n_keep = n_campioni * sample_size

    if n_keep == 0:
        if verbose:
            print(f"Dati insufficienti per {temp_nominale}C")
        return False, {}, {}

    arr_rtd_clean = arr_rtd_lsb[:n_keep]
    arr_log_clean = np.array(buffer_log_lsb[:n_keep], dtype=float)

    matrix_rtd = arr_rtd_clean.reshape((n_campioni, sample_size))
    matrix_log = arr_log_clean.reshape((n_campioni, sample_size))

    sample_mean_rtd = np.mean(matrix_rtd, axis=1)
    campioni_std_rtd = np.std(matrix_rtd, axis=1, ddof=1)
    sample_mean_log = np.mean(matrix_log, axis=1)
    campioni_std_log = np.std(matrix_log, axis=1, ddof=1)

    pmean_rtd = float(np.mean(sample_mean_rtd))
    pstd_rtd = (
        float(np.std(sample_mean_rtd, ddof=1) / np.sqrt(n_campioni))
        if n_campioni > 1
        else 0.0
    )
    pmean_log = float(np.mean(sample_mean_log))
    pstd_log = (
        float(np.std(sample_mean_log, ddof=1) / np.sqrt(n_campioni))
        if n_campioni > 1
        else 0.0
    )

    glob_smean_rtd = float(np.mean(arr_rtd_clean))
    glob_std_rtd = float(np.std(arr_rtd_clean, ddof=1)) if n_keep > 1 else 0.0
    glob_smean_log = float(np.mean(arr_log_clean))
    glob_std_log = float(np.std(arr_log_clean, ddof=1)) if n_keep > 1 else 0.0

    err = np.abs(matrix_log - matrix_rtd).flatten()
    max_error = float(np.max(err)) if err.size else float("nan")
    mean_error = float(np.mean(err)) if err.size else float("nan")

    if verbose:
        min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
        span = max_v - min_v
        lsb_per_c = adc_max / span
        print(f"\n--- {temp_nominale}C : {n_keep} misure ({n_campioni} campioni) ---")
        print("MEDIE DEI CAMPIONI (Valore Atteso) [LSB]:")
        print(
            f"  RTD: {pmean_rtd:.2f} +/- {pstd_rtd:.4f} LSB"
            f"  ({lsb16_to_phys(np.array([pmean_rtd]), lsb_scale_sensor_info, adc_max)[0]:.4f} °C)"
        )
        print(
            f"  Log: {pmean_log:.2f} +/- {pstd_log:.4f} LSB"
            f"  ({lsb16_to_phys(np.array([pmean_log]), lsb_scale_sensor_info, adc_max)[0]:.4f} °C)"
        )
        print("TOTALE (Singole misure) [LSB]:")
        print(f"  RTD: {glob_smean_rtd:.2f} +/- {glob_std_rtd:.4f} LSB")
        print(f"  Log: {glob_smean_log:.2f} +/- {glob_std_log:.4f} LSB")
        print(
            f"Errore massimo [LSB]: {max_error:.4f}  ({max_error / lsb_per_c:.4f} °C)"
        )
        print(
            f"Errore medio   [LSB]: {mean_error:.4f}  ({mean_error / lsb_per_c:.4f} °C)"
        )

    dati_raw_item = {"rtd": arr_rtd_clean, "log": arr_log_clean}
    risultati_item = {
        "x_axis": np.arange(n_campioni),
        "smean_rtd": sample_mean_rtd,
        "std_rtd": campioni_std_rtd,
        "smean_log": sample_mean_log,
        "std_log": campioni_std_log,
        "pmean_rtd": pmean_rtd,
        "pstd_rtd": pstd_rtd,
        "pmean_log": pmean_log,
        "pstd_log": pstd_log,
        "max_error": max_error,
        "mean_error": mean_error,
    }
    return True, dati_raw_item, risultati_item


def _get_data(
    payload: Dict[str, Any],
    temp_nominali: List[float],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    verbose: bool,
) -> Tuple[Dict[float, Dict[str, np.ndarray]], Dict[float, Dict[str, Any]]]:
    dati_raw: Dict[float, Dict[str, np.ndarray]] = {}
    risultati_elaborati: Dict[float, Dict[str, Any]] = {}

    for t in temp_nominali:
        ok, raw_item, result_item = _analyze_step_lsb(
            payload,
            temp_nominali,
            t,
            sample_size,
            lsb_scale_sensor_info,
            adc_max,
            verbose,
        )
        if ok:
            dati_raw[t] = raw_item
            risultati_elaborati[t] = result_item

    return dati_raw, risultati_elaborati


# ---------------------------------------------------------------------------
# GUM uncertainty propagation for OLS coefficients
# ---------------------------------------------------------------------------


def _calcola_incertezze_gum(
    x: np.ndarray,
    y: np.ndarray,
    u_x: np.ndarray,
    u_y: np.ndarray,
) -> Tuple[float, float, float, float, float]:
    """
    GUM uncertainty propagation for OLS coefficients A and B.

    All inputs are in LSB units:
      x   – sensor (NTC) population means [LSB]
      y   – reference (PT100) population means converted to LSB
      u_x – combined uncertainty on x [LSB]
      u_y – combined uncertainty on y [LSB]

    Returns: A (dimensionless), B [LSB], u(A), u(B) [LSB], cov(A,B) [LSB].
    """
    n = len(x)
    # if n < 4:
    #     raise ValueError("Servono almeno 4 punti per la propagazione GUM")

    x_mean = np.mean(x)
    y_mean = np.mean(y)

    d_den = np.sum((x - x_mean) ** 2)
    if np.isclose(d_den, 0.0):
        raise ValueError("Denominatore nullo nel calcolo GUM")

    n_num = np.sum((x - x_mean) * (y - y_mean))
    a = n_num / d_den
    b = y_mean - a * x_mean

    dA_dx = np.zeros(n)
    dA_dy = np.zeros(n)
    dB_dx = np.zeros(n)
    dB_dy = np.zeros(n)

    for i in range(n):
        dA_dy[i] = (x[i] - x_mean) / d_den
        dA_dx[i] = ((y[i] - y_mean) - 2 * a * (x[i] - x_mean)) / d_den
        dB_dy[i] = (1 / n) - x_mean * dA_dy[i]
        dB_dx[i] = -(a / n) - x_mean * dA_dx[i]

    u_a2 = np.sum((dA_dx * u_x) ** 2 + (dA_dy * u_y) ** 2)
    u_b2 = np.sum((dB_dx * u_x) ** 2 + (dB_dy * u_y) ** 2)
    cov_ab = np.sum((dA_dx * dB_dx * u_x**2) + (dA_dy * dB_dy * u_y**2))

    u_a = np.sqrt(max(0.0, u_a2))
    u_b = np.sqrt(max(0.0, u_b2))

    return float(a), float(b), float(u_a), float(u_b), float(cov_ab)


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def calibrate(
    payload: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    sample_size: int,
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    verbose: bool,
    risol_degc: float = 0.1,
    old_a: float | None = None,
    old_b: float | None = None,
) -> Dict[str, Any]:
    """
    Run the full GUM OLS linear calibration pipeline in the LSB domain.

    Parameters
    ----------
    payload              : JSON payload with sensor_raw_samples & reference_temperature_samples
    lsb_scale_sensor_info: dict with minPhysVal / maxPhysVal for LSB ↔ °C conversion
    sample_size          : number of raw readings per block
    adc_max              : ADC full-scale value (e.g. 65535 for 16-bit)
    ub_pt_lsb            : type-B standard uncertainty of the PT100 reference [LSB]
    ub_tmp_lsb           : type-B standard uncertainty of the NTC ADC [LSB]
    verbose              : print detailed per-step diagnostics
    risol_degc           : resolution of the instrument under calibration [°C] (default 0.1)

    Returns
    -------
    dict with keys:
        A, B, u_A, u_B, cov_AB       – calibration coefficients (GUM OLS)
        temp_nominali                 – list of nominal temperatures [°C]
        dati_raw                      – per-step raw arrays {temp: {rtd, log}}
        risultati_elaborati           – per-step statistics {temp: dict}
        expanded_uncertainties        – list[float] U_exp [°C] at each step
        ref_temp_means                – list[float] mean PT100 temp [°C] at each step
    """
    temp_nominali = [parse_step(s)[0] for s in payload.get("steps", [])]
    if len(temp_nominali) < 2:
        raise ValueError("Servono almeno 4 step di calibrazione")

    dati_raw, risultati_elaborati = _get_data(
        payload,
        temp_nominali,
        sample_size,
        lsb_scale_sensor_info,
        adc_max,
        verbose,
    )

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    if verbose:
        print("\n\n --- Fine acquisizione dati ---")
        print(
            f"\nErrore massimo [LSB]: "
            f"{max(risultati_elaborati[t]['max_error'] for t in temp_nominali):.3f}"
            f"  ({max(risultati_elaborati[t]['max_error'] for t in temp_nominali) / lsb_per_c:.4f} °C)"
        )
        print(
            f"Errore medio [LSB]: "
            f"{np.mean([risultati_elaborati[t]['mean_error'] for t in temp_nominali]):.3f}"
        )
        print(
            f"Massima deviazione standard NTC [LSB]: "
            f"{max(risultati_elaborati[t]['pstd_log'] for t in temp_nominali):.3f}"
        )
        print(
            f"Massima deviazione standard RTD [LSB]: "
            f"{max(risultati_elaborati[t]['pstd_rtd'] for t in temp_nominali):.3f}"
        )

    # Population means in LSB
    x = np.array(
        [risultati_elaborati[t]["pmean_log"] for t in temp_nominali], dtype=float
    )
    y = np.array(
        [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali], dtype=float
    )

    if old_a is not None and old_b is not None:
        y_old = old_a * x + old_b
        err_old_lsb = y_old - y
        err_old_degC = err_old_lsb / lsb_per_c
        if verbose:
            print("\n--- Baseline pre-fit error (old A/B) ---")
            print(f"old A = {old_a:.10f}  old B = {old_b:.10f} LSB")
            print(
                f"signed mean error: {np.mean(err_old_lsb):.6f} LSB "
                f"({np.mean(err_old_degC):.6f} °C)"
            )
            print(
                f"max abs error: {np.max(np.abs(err_old_lsb)):.6f} LSB "
                f"({np.max(np.abs(err_old_degC)):.6f} °C)"
            )
            for i, t in enumerate(temp_nominali):
                print(
                    f"  step {t:>6.1f}°C -> old_err={err_old_lsb[i]:+.6f} LSB "
                    f"({err_old_degC[i]:+.6f} °C)"
                )

    # Combined uncertainties in LSB: sqrt(type_A² + type_B²)
    uc_tmp = np.array(
        [
            np.sqrt(risultati_elaborati[t]["pstd_log"] ** 2 + ub_tmp_lsb**2)
            for t in temp_nominali
        ],
        dtype=float,
    )
    uc_pt = np.array(
        [
            np.sqrt(risultati_elaborati[t]["pstd_rtd"] ** 2 + ub_pt_lsb**2)
            for t in temp_nominali
        ],
        dtype=float,
    )

    if verbose:
        print("\n--- Combined uncertainties [LSB] ---")
        for i, t in enumerate(temp_nominali):
            print(f"  {t}°C  uc_sensor={uc_tmp[i]:.4f} LSB   uc_ref={uc_pt[i]:.4f} LSB")

    a, b, u_a, u_b, cov_ab = _calcola_incertezze_gum(x, y, uc_tmp, uc_pt)

    if verbose:
        print("\nCalibration params (GUM) — LSB domain:")
        print(f"A = {a:.10f}  (dimensionless)")
        print(f"B = {b:.10f} LSB  ({b / lsb_per_c:.6f} °C)")
        print(f"u(A) = {u_a:.10f}")
        print(f"u(B) = {u_b:.10f} LSB  ({u_b / lsb_per_c:.6f} °C)")
        print(f"cov(A,B) = {cov_ab:.10f}")

    # Expanded uncertainty U(E) at each step [°C]  (k=2)
    # Uses Type A and Type B components directly (not OLS coefficient uncertainties).
    #
    #   u(T_ref) = sqrt( uA_ref^2 + uB_ref^2 )
    #   u(T_i)   = sqrt( uA_i^2 + uB_i^2 + (RISOL/sqrt(12))^2 )
    #   u(E)     = sqrt( u(T_ref)^2 + u(T_i)^2 )
    #   U(E)     = 2 * u(E)
    RISOL_degC = risol_degc
    uB_ref_degC = ub_pt_lsb / lsb_per_c
    uB_i_degC = ub_tmp_lsb / lsb_per_c
    u_res_degC = RISOL_degC / np.sqrt(12.0)

    expanded_uncertainties: List[float] = []
    per_step_u_budget: List[Tuple[float, float, float, float, float, float, float]] = []
    for t in temp_nominali:
        uA_ref_degC = risultati_elaborati[t]["pstd_rtd"] / lsb_per_c
        uA_i_degC = risultati_elaborati[t]["pstd_log"] / lsb_per_c

        mu_T_ref = np.sqrt(uA_ref_degC**2 + uB_ref_degC**2)
        mu_T_i = np.sqrt(uA_i_degC**2 + uB_i_degC**2 + u_res_degC**2)
        mu_E = np.sqrt(mu_T_ref**2 + mu_T_i**2)
        U_E = 2.0 * mu_E

        expanded_uncertainties.append(float(U_E))
        per_step_u_budget.append(
            (t, uA_ref_degC, uA_i_degC, mu_T_ref, mu_T_i, mu_E, U_E)
        )

    if verbose:
        print("\n--- Uncertainty budget U(E) [degC] ---")
        print(f"  uB_ref     = {uB_ref_degC:.10f} degC  (PT100, type B)")
        print(f"  uB_i       = {uB_i_degC:.10f} degC  (NTC, type B)")
        print(f"  RISOL      = {RISOL_degC} degC  (instrument resolution)")
        print(f"  u_res      = {u_res_degC:.10f} degC  (resolution / sqrt(12))")
        print("  Formulae:")
        print("    u(T_ref) = sqrt(uA_ref^2 + uB_ref^2)")
        print("    u(T_i)   = sqrt(uA_i^2 + uB_i^2 + u_res^2)")
        print("    u(E)     = sqrt(u(T_ref)^2 + u(T_i)^2)")
        print("    U(E)     = 2 * u(E)")
        print()
        col_h = (
            f"  {'Step':>8}  {'T_ref/degC':>12}  {'uA_ref':>10}  {'uA_i':>10}"
            f"  {'u(T_ref)':>10}  {'u(T_i)':>10}  {'u(E)':>10}  {'U(E)':>10}"
        )
        print(col_h)
        print(
            f"  {'-'*8}  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}"
        )
        for t, uA_ref_degC, uA_i_degC, mu_T_ref, mu_T_i, mu_E, U_E in per_step_u_budget:
            ref_t_c = lsb16_to_phys(
                np.array([risultati_elaborati[t]["pmean_rtd"]]),
                lsb_scale_sensor_info,
                adc_max,
            )[0]
            print(
                f"  {t:>8.1f}  {ref_t_c:>12.4f}  "
                f"{uA_ref_degC:>10.6f}  {uA_i_degC:>10.6f}  "
                f"{mu_T_ref:>10.6f}  {mu_T_i:>10.6f}  "
                f"{mu_E:>10.6f}  {U_E:>10.6f}"
            )

    # PT100 mean temperature [°C] at each step (for certificate table)
    ref_temp_means: List[float] = [
        float(
            lsb16_to_phys(
                np.array([risultati_elaborati[t]["pmean_rtd"]]),
                lsb_scale_sensor_info,
                adc_max,
            )[0]
        )
        for t in temp_nominali
    ]

    return {
        "A": float(a),
        "B": float(b),
        "u_A": float(u_a),
        "u_B": float(u_b),
        "cov_AB": float(cov_ab),
        "old_A": None if old_a is None else float(old_a),
        "old_B": None if old_b is None else float(old_b),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "ref_temp_means": ref_temp_means,
        "lsb_per_c": lsb_per_c,
        "ub_pt_lsb": ub_pt_lsb,
        "ub_tmp_lsb": ub_tmp_lsb,
    }


# ---------------------------------------------------------------------------
# Text report builder
# ---------------------------------------------------------------------------


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
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    span = max_v - min_v
    lsb_per_c = adc_max / span

    lines: List[str] = []
    lines.append("# Calibration Report (LSB domain) — Linear OLS")
    lines.append("")
    lines.append("## Per-step statistics [all values in LSB]")
    lines.append("")
    lines.append(
        "| step | target [C] | pmean_ref [LSB] | pstd_ref [LSB]"
        " | pmean_sensor [LSB] | pstd_sensor [LSB] | max_error [LSB] | mean_error [LSB] |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")

    for i, t in enumerate(temp_nominali):
        r = risultati_elaborati[t]
        lines.append(
            f"| {i} | {t:.3f} | {r['pmean_rtd']:.2f} | {r['pstd_rtd']:.4f}"
            f" | {r['pmean_log']:.2f} | {r['pstd_log']:.4f}"
            f" | {r['max_error']:.4f} | {r['mean_error']:.4f} |"
        )

    lines.append("")
    lines.append("## Calibration coefficients (T_ref_lsb = A * T_sensor_lsb + B)")
    lines.append("")
    lines.append(f"- A: {a:.10f}  (dimensionless)")
    lines.append(f"- B: {b:.6f} LSB  ({b / lsb_per_c:.6f} °C)")
    lines.append(f"- u(A): {u_a:.10f}")
    lines.append(f"- u(B): {u_b:.6f} LSB  ({u_b / lsb_per_c:.6f} °C)")
    lines.append(f"- cov(A,B): {cov_ab:.10f}")
    if u_a > 0 and u_b > 0:
        lines.append(f"- corr(A,B): {cov_ab / (u_a * u_b):.6f}")

    lines.append("")
    lines.append("## Uncertainty budget")
    lines.append("")
    lines.append(f"- LSB scale: [{min_v}, {max_v}] °C  →  {lsb_per_c:.4f} LSB/°C")
    lines.append(f"- Raw sensor format: unsigned {adc_bits}-bit LSB")
    lines.append(f"- PT100 ub [LSB]: {ub_pt_lsb:.4f}  ({ub_pt_lsb / lsb_per_c:.6f} °C)")
    lines.append(f"- NTC ub [LSB]: {ub_tmp_lsb:.4f}  ({ub_tmp_lsb / lsb_per_c:.6f} °C)")

    u_quant_lsb = 1.0 / (2.0 * np.sqrt(3.0))
    lines.append("")
    lines.append("## Expanded uncertainty at each step (k=2) [LSB and °C]")
    lines.append(
        f"  (u_quant = {u_quant_lsb:.5f} LSB = {u_quant_lsb / lsb_per_c:.6f} °C)"
    )
    lines.append("")
    lines.append(
        "| step [C] | sensor_lsb | uc_total [LSB] | U_expanded [LSB] | U_expanded [°C] |"
    )
    lines.append("|---:|---:|---:|---:|---:|")
    for t in temp_nominali:
        r = risultati_elaborati[t]
        x_lsb = r["pmean_log"]
        uc2 = (
            (x_lsb * u_a) ** 2 + (a * u_quant_lsb) ** 2 + u_b**2 + 2.0 * x_lsb * cov_ab
        )
        uc = np.sqrt(max(0.0, uc2))
        u_exp = 2.0 * uc
        lines.append(
            f"| {t:.1f} | {x_lsb:.1f} | {uc:.4f} | {u_exp:.4f} | {u_exp / lsb_per_c:.6f} |"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Chart rendering (optional — requires matplotlib)
# ---------------------------------------------------------------------------


def plot_charts(
    a: float,
    b: float,
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
) -> None:
    """Render matplotlib diagnostic charts for the linear calibration."""
    import importlib

    plt = importlib.import_module("matplotlib.pyplot")

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    # --- Fig 1: per-step sample means ---
    fig1, axs = plt.subplots(2, 3, figsize=(18, 10))
    axcmeanflat = axs.flatten()
    fig1.suptitle(f"Analisi Campioni (n={sample_size}) [LSB]", fontsize=16)

    for i, temp in enumerate(temp_nominali):
        ax = axcmeanflat[i]
        if temp in risultati_elaborati:
            res = risultati_elaborati[temp]
            x = res["x_axis"]
            ub_pt_band = np.sqrt(res["std_rtd"] ** 2 + ub_pt_lsb**2)
            ub_tmp_band = np.sqrt(res["std_log"] ** 2 + ub_tmp_lsb**2)

            ax.plot(x, res["smean_rtd"], "b-o", label="RTD", linewidth=1, markersize=2)
            ax.plot(x, res["smean_rtd"] + ub_pt_band, "b--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_rtd"] - ub_pt_band, "b--", alpha=0.4, linewidth=1)
            ax.plot(
                x, res["smean_log"], "r-o", label="Sensor", linewidth=1, markersize=2
            )
            ax.plot(x, res["smean_log"] + ub_tmp_band, "r--", alpha=0.4, linewidth=1)
            ax.plot(x, res["smean_log"] - ub_tmp_band, "r--", alpha=0.4, linewidth=1)
            ax.set_title(f"Nominale: {temp} C")
            ax.set_ylabel("LSB")
            ax.grid(True, alpha=0.3)
            if i == 0:
                ax.legend()
        else:
            ax.text(0.5, 0.5, "Dati assenti", ha="center")

    # --- Fig 2: comparison and error distribution ---
    fig2, axs2 = plt.subplots(1, 2, figsize=(18, 10))
    axs2[0].set_title("Confronto: PT100 vs NTC [LSB] (Valori Attesi)", fontsize=12)

    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [
        np.sqrt(risultati_elaborati[t]["pstd_rtd"] ** 2 + ub_pt_lsb**2)
        for t in temp_nominali
    ]
    log_err = [
        np.sqrt(risultati_elaborati[t]["pstd_log"] ** 2 + ub_tmp_lsb**2)
        for t in temp_nominali
    ]

    axs2[0].errorbar(
        rtd_val,
        rtd_val,
        xerr=rtd_err,
        yerr=rtd_err,
        fmt=".",
        color="b",
        ecolor="b",
        linewidth=1,
        capsize=5,
        label="Incertezza RTD [LSB]",
    )
    axs2[0].errorbar(
        rtd_val,
        log_val,
        yerr=log_err,
        fmt=".",
        color="r",
        ecolor="r",
        linewidth=1,
        capsize=5,
        label="Incertezza NTC [LSB]",
    )
    axs2[0].plot(rtd_val, rtd_val, "b--", linewidth=0.7)
    axs2[0].plot(rtd_val, log_val, "r--", linewidth=0.7)
    if rtd_val:
        mn = min(min(rtd_val), min(log_val))
        mx = max(max(rtd_val), max(log_val))
        axs2[0].plot([mn, mx], [mn, mx], "k:", alpha=0.5, label="Ideale")
    for i, txt in enumerate(temp_nominali):
        axs2[0].annotate(
            f"{txt}C",
            (rtd_val[i], log_val[i]),
            xytext=(5, -5),
            textcoords="offset points",
        )
    axs2[0].set_xlabel("RTD (PT100) [LSB]")
    axs2[0].set_ylabel("NTC [LSB]")
    axs2[0].grid(True)
    axs2[0].legend()

    axs2[1].set_title("Distribuzione dell'errore NTC − RTD [LSB]", fontsize=12)
    errore = np.array(log_val) - np.array(rtd_val)
    uc_combined = np.array([np.sqrt(le**2 + re**2) for le, re in zip(log_err, rtd_err)])
    axs2[1].axhline(0, color="k", linestyle="--", alpha=0.7, linewidth=1)
    axs2[1].errorbar(
        temp_nominali,
        errore,
        yerr=uc_combined,
        fmt="ro",
        ecolor="r",
        capsize=5,
        linewidth=1.5,
        markersize=5,
        label="errore ± u_c(NTC⊕RTD) [LSB]",
    )
    axs2[1].errorbar(
        temp_nominali,
        errore,
        yerr=log_err,
        fmt=".",
        color="tomato",
        ecolor="tomato",
        capsize=3,
        linewidth=1,
        markersize=1,
        alpha=0.5,
        label="± u_c NTC sola [LSB]",
    )
    axs2[1].errorbar(
        temp_nominali,
        [0.0] * len(temp_nominali),
        yerr=rtd_err,
        fmt=".",
        color="b",
        ecolor="b",
        capsize=3,
        linewidth=1,
        markersize=1,
        label="± u_c RTD sola (@ zero) [LSB]",
    )
    axs2[1].grid(True, which="major", linestyle="-", alpha=0.3)
    axs2[1].grid(True, which="minor", linestyle=":", alpha=0.3)
    axs2[1].minorticks_on()
    axs2[1].set_xlabel("Temperatura nominale [°C]")
    axs2[1].set_ylabel("Errore NTC − RTD [LSB]")
    axs2[1].legend()

    # --- Fig 3 & 4: histograms at mid-range step ---
    hist_temp = (
        50.0 if 50.0 in risultati_elaborati else temp_nominali[len(temp_nominali) // 2]
    )
    fig3, ax3 = plt.subplots(figsize=(10, 6))
    ax3.hist(risultati_elaborati[hist_temp]["smean_rtd"])
    ax3.set_xlabel("Temperatura Media [LSB]")
    ax3.set_ylabel("Frequenza")
    ax3.set_title(f"Distribuzione Medie RTD a {hist_temp:g} C [LSB]")
    ax3.grid(True, alpha=0.3)

    fig4, ax4 = plt.subplots(figsize=(10, 6))
    ax4.hist(dati_raw[hist_temp]["rtd"].flatten(), bins=20)
    ax4.set_xlabel("LSB")
    ax4.set_ylabel("Frequenza")
    ax4.set_title(f"Distribuzione RTD a {hist_temp:g} C [LSB]")
    ax4.grid(True, alpha=0.3)

    # --- Fig 5: calibration curve and post-calibration error ---
    fig5, axs5 = plt.subplots(1, 2, figsize=(18, 10))
    axs5[0].set_title("Curva di taratura NTC [LSB]", fontsize=12)

    log_val_tarato = [
        a * risultati_elaborati[t]["pmean_log"] + b for t in temp_nominali
    ]
    axs5[0].errorbar(
        log_val, log_val_tarato, xerr=log_err, fmt=".", color="r", ecolor="r", capsize=5
    )
    axs5[0].errorbar(
        log_val, log_val_tarato, yerr=rtd_err, fmt=".", color="r", ecolor="r", capsize=5
    )
    axs5[0].plot(log_val, rtd_val, "b", linewidth=0.7, label="RTD function")
    axs5[0].plot(
        log_val, log_val_tarato, "r", linewidth=1, label="Calibration function"
    )
    axs5[0].plot(
        log_val, log_val, color="grey", linestyle="--", linewidth=1, label="Readings"
    )
    axs5[0].set_xlabel("NTC reading [LSB]")
    axs5[0].set_ylabel("LSB")
    axs5[0].grid(True)
    axs5[0].legend()

    axs5[1].set_title("Distribuzione errore post-taratura NTC [LSB]", fontsize=12)
    errore_tarato = np.array(log_val_tarato) - np.array(rtd_val)
    uc_combined_cal = np.array(
        [np.sqrt(le**2 + re**2) for le, re in zip(log_err, rtd_err)]
    )
    axs5[1].axhline(0, color="k", linestyle="--", alpha=0.7, linewidth=1)
    axs5[1].errorbar(
        temp_nominali,
        errore_tarato,
        yerr=uc_combined_cal,
        fmt="ro",
        ecolor="r",
        capsize=5,
        linewidth=1.5,
        markersize=5,
        label="errore_tarato ± u_c(NTC⊕RTD) [LSB]",
    )
    axs5[1].errorbar(
        temp_nominali,
        errore_tarato,
        yerr=log_err,
        fmt=".",
        color="tomato",
        ecolor="tomato",
        capsize=3,
        linewidth=1,
        markersize=1,
        alpha=0.5,
        label="± u_c NTC sola [LSB]",
    )
    axs5[1].errorbar(
        temp_nominali,
        [0.0] * len(temp_nominali),
        yerr=rtd_err,
        fmt=".",
        color="b",
        ecolor="b",
        capsize=3,
        linewidth=1,
        markersize=1,
        label="± u_c RTD sola (@ zero) [LSB]",
    )
    axs5[1].grid(True, which="major", linestyle="-", alpha=0.3)
    axs5[1].grid(True, which="minor", linestyle=":", alpha=0.3)
    axs5[1].minorticks_on()
    axs5[1].set_xlabel("Temperatura nominale [°C]")
    axs5[1].set_ylabel("Errore NTC calibrato − RTD [LSB]")
    axs5[1].legend()

    # --- Fig 6: cross-temperature correlations ---
    fig6, axs6 = plt.subplots(2, 3, figsize=(15, 10))
    axs6_flat = axs6.flatten()
    fig6.suptitle("Correlazioni NTC/RTD tra temperature diverse [LSB]", fontsize=16)

    np.random.seed(42)
    temp_indices = list(range(len(temp_nominali)))
    tmp_pairs = [
        tuple(
            temp_nominali[i]
            for i in np.random.choice(temp_indices, size=2, replace=False)
        )
        for _ in range(3)
    ]
    pt_pairs = [
        tuple(
            temp_nominali[i]
            for i in np.random.choice(temp_indices, size=2, replace=False)
        )
        for _ in range(3)
    ]

    for i, (t1, t2) in enumerate(tmp_pairs):
        ax = axs6_flat[i]
        x_data = risultati_elaborati[t1]["smean_log"]
        y_data = risultati_elaborati[t2]["smean_log"]
        min_len = min(len(x_data), len(y_data))
        ax.plot(
            x_data[:min_len],
            y_data[:min_len],
            alpha=0.6,
            marker="o",
            color="blue",
            linestyle="",
            markersize=4,
        )
        ax.set_xlabel(f"NTC @ {t1}C [LSB]")
        ax.set_ylabel(f"NTC @ {t2}C [LSB]")
        ax.set_title(f"NTC: {t1}C vs {t2}C")
        ax.grid(True, alpha=0.3)

    for i, (t3, t4) in enumerate(pt_pairs):
        ax = axs6_flat[i + 3]
        x_data = risultati_elaborati[t3]["smean_rtd"]
        y_data = risultati_elaborati[t4]["smean_rtd"]
        min_len = min(len(x_data), len(y_data))
        ax.plot(
            x_data[:min_len],
            y_data[:min_len],
            alpha=0.6,
            marker="s",
            color="red",
            linestyle="",
            markersize=4,
        )
        ax.set_xlabel(f"PT100 @ {t3}C [LSB]")
        ax.set_ylabel(f"PT100 @ {t4}C [LSB]")
        ax.set_title(f"PT100: {t3}C vs {t4}C")
        ax.grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0.03, 1, 0.96])
    plt.show()


def save_charts(
    a: float,
    b: float,
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    output_dir: Path,
    prefix: str = "calib",
) -> List[Path]:
    """
    Save the same diagnostic figures as plot_charts() as PNG files into output_dir.

    Returns the list of paths written.
    """
    import importlib

    plt = importlib.import_module("matplotlib.pyplot")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    saved: List[Path] = []

    # ── Fig 1: per-step sample means ──
    fig1, axs = plt.subplots(2, 3, figsize=(18, 10))
    axcmeanflat = axs.flatten()
    fig1.suptitle(f"Analisi Campioni (n={sample_size}) [LSB]", fontsize=16)
    for i, temp in enumerate(temp_nominali):
        ax = axcmeanflat[i]
        if temp in risultati_elaborati:
            res = risultati_elaborati[temp]
            x = res["x_axis"]
            ub_pt_band  = np.sqrt(res["std_rtd"] ** 2 + ub_pt_lsb**2)
            ub_tmp_band = np.sqrt(res["std_log"] ** 2 + ub_tmp_lsb**2)
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
    p1 = output_dir / f"{prefix}_fig1_sample_means.png"
    fig1.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    saved.append(p1)

    # ── Fig 2: comparison and error distribution ──
    fig2, axs2 = plt.subplots(1, 2, figsize=(18, 10))
    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_lsb**2) for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali]

    axs2[0].set_title("Confronto: PT100 vs NTC [LSB] (Valori Attesi)", fontsize=12)
    axs2[0].errorbar(rtd_val, rtd_val, xerr=rtd_err, yerr=rtd_err, fmt=".", color="b", ecolor="b", linewidth=1, capsize=5, label="Incertezza RTD [LSB]")
    axs2[0].errorbar(rtd_val, log_val, yerr=log_err, fmt=".", color="r", ecolor="r", linewidth=1, capsize=5, label="Incertezza NTC [LSB]")
    axs2[0].plot(rtd_val, rtd_val, "b--", linewidth=0.7)
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
    axs2[1].set_title("Distribuzione dell'errore NTC − RTD [LSB]", fontsize=12)
    axs2[1].axhline(0, color="k", linestyle="--", alpha=0.7, linewidth=1)
    axs2[1].errorbar(temp_nominali, errore, yerr=uc_combined, fmt="ro", ecolor="r", capsize=5, linewidth=1.5, markersize=5, label="errore ± u_c [LSB]")
    axs2[1].grid(True, alpha=0.3)
    axs2[1].set_xlabel("Temperatura nominale [°C]")
    axs2[1].set_ylabel("Errore NTC − RTD [LSB]")
    axs2[1].legend()
    p2 = output_dir / f"{prefix}_fig2_comparison.png"
    fig2.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    saved.append(p2)

    # ── Fig 3 & 4: histograms ──
    hist_temp = 50.0 if 50.0 in risultati_elaborati else temp_nominali[len(temp_nominali) // 2]

    fig3, ax3 = plt.subplots(figsize=(10, 6))
    ax3.hist(risultati_elaborati[hist_temp]["smean_rtd"])
    ax3.set_xlabel("Temperatura Media [LSB]")
    ax3.set_ylabel("Frequenza")
    ax3.set_title(f"Distribuzione Medie RTD a {hist_temp:g} C [LSB]")
    ax3.grid(True, alpha=0.3)
    p3 = output_dir / f"{prefix}_fig3_hist_rtd.png"
    fig3.savefig(p3, dpi=150, bbox_inches="tight")
    plt.close(fig3)
    saved.append(p3)

    fig4, ax4 = plt.subplots(figsize=(10, 6))
    ax4.hist(dati_raw[hist_temp]["rtd"].flatten(), bins=20)
    ax4.set_xlabel("LSB")
    ax4.set_ylabel("Frequenza")
    ax4.set_title(f"Distribuzione RTD a {hist_temp:g} C [LSB]")
    ax4.grid(True, alpha=0.3)
    p4 = output_dir / f"{prefix}_fig4_hist_rtd_raw.png"
    fig4.savefig(p4, dpi=150, bbox_inches="tight")
    plt.close(fig4)
    saved.append(p4)

    # ── Fig 5: calibration curve and post-calibration error ──
    log_val_tarato = [a * risultati_elaborati[t]["pmean_log"] + b for t in temp_nominali]
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
    errore_tarato = np.array(log_val_tarato) - np.array(rtd_val)
    uc_combined_cal = np.array([np.sqrt(le**2 + re**2) for le, re in zip(log_err, rtd_err)])
    axs5[1].set_title("Distribuzione errore post-taratura NTC [LSB]", fontsize=12)
    axs5[1].axhline(0, color="k", linestyle="--", alpha=0.7, linewidth=1)
    axs5[1].errorbar(temp_nominali, errore_tarato, yerr=uc_combined_cal, fmt="ro", ecolor="r", capsize=5, linewidth=1.5, markersize=5, label="errore_tarato ± u_c [LSB]")
    axs5[1].grid(True, alpha=0.3)
    axs5[1].set_xlabel("Temperatura nominale [°C]")
    axs5[1].set_ylabel("Errore NTC calibrato − RTD [LSB]")
    axs5[1].legend()
    p5 = output_dir / f"{prefix}_fig5_calibration_curve.png"
    fig5.savefig(p5, dpi=150, bbox_inches="tight")
    plt.close(fig5)
    saved.append(p5)

    # ── Fig 6: cross-temperature correlations ──
    fig6, axs6 = plt.subplots(2, 3, figsize=(15, 10))
    axs6_flat = axs6.flatten()
    fig6.suptitle("Correlazioni NTC/RTD tra temperature diverse [LSB]", fontsize=16)
    np.random.seed(42)
    temp_indices = list(range(len(temp_nominali)))
    tmp_pairs = [tuple(temp_nominali[i] for i in np.random.choice(temp_indices, size=2, replace=False)) for _ in range(3)]
    pt_pairs  = [tuple(temp_nominali[i] for i in np.random.choice(temp_indices, size=2, replace=False)) for _ in range(3)]
    for i, (t1, t2) in enumerate(tmp_pairs):
        ax = axs6_flat[i]
        x_data = risultati_elaborati[t1]["smean_log"]
        y_data = risultati_elaborati[t2]["smean_log"]
        ml = min(len(x_data), len(y_data))
        ax.plot(x_data[:ml], y_data[:ml], alpha=0.6, marker="o", color="blue", linestyle="", markersize=4)
        ax.set_xlabel(f"NTC @ {t1}C [LSB]")
        ax.set_ylabel(f"NTC @ {t2}C [LSB]")
        ax.set_title(f"NTC: {t1}C vs {t2}C")
        ax.grid(True, alpha=0.3)
    for i, (t3, t4) in enumerate(pt_pairs):
        ax = axs6_flat[i + 3]
        x_data = risultati_elaborati[t3]["smean_rtd"]
        y_data = risultati_elaborati[t4]["smean_rtd"]
        ml = min(len(x_data), len(y_data))
        ax.plot(x_data[:ml], y_data[:ml], alpha=0.6, marker="s", color="red", linestyle="", markersize=4)
        ax.set_xlabel(f"PT100 @ {t3}C [LSB]")
        ax.set_ylabel(f"PT100 @ {t4}C [LSB]")
        ax.set_title(f"PT100: {t3}C vs {t4}C")
        ax.grid(True, alpha=0.3)
    fig6.tight_layout(rect=[0, 0.03, 1, 0.96])
    p6 = output_dir / f"{prefix}_fig6_correlations.png"
    fig6.savefig(p6, dpi=150, bbox_inches="tight")
    plt.close(fig6)
    saved.append(p6)

    return saved


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Run linear NTC calibration stand-alone.

    Reads export2_tmp126_lsb16.json (or --input path), applies default NTC/TMP126
    parameters from VAR_REF_SENSOR.py, prints the calibration result, and optionally shows
    charts (--charts).
    """
    base_dir = Path(__file__).resolve().parent

    from VAR_REF_SENSOR import SENSOR_model, VAR_extra

    sensor = SENSOR_model()
    extra = VAR_extra()

    default_input_json = base_dir / "export2_tmp126_lsb16.json"
    default_report_path = base_dir / "calibration_report.md"

    adc_bits = extra._adc_bits
    adc_max = float((1 << adc_bits) - 1)  # 65535

    lsb_min_temp_c = sensor._minPhyThreshold
    lsb_max_temp_c = sensor._maxPhyThreshold
    lsb_span_c = lsb_max_temp_c - lsb_min_temp_c
    lsb_per_c = adc_max / lsb_span_c

    U_pt_c = extra._U_pt_c
    k_pt = extra._k_pt
    ub_pt_c = U_pt_c / k_pt
    ub_pt_lsb = ub_pt_c * lsb_per_c

    d_tmp126_c = extra._d_tmp126_c
    ub_tmp_c = d_tmp126_c / np.sqrt(3.0)
    ub_tmp_lsb = ub_tmp_c * lsb_per_c

    sample_size = 20

    parser = argparse.ArgumentParser(
        description="NTC linear calibration (GUM OLS, LSB domain) — standalone test"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=default_input_json,
        help="Path to LSB16 JSON input",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=default_report_path,
        help="Path for output calibration report markdown",
    )
    parser.add_argument(
        "--charts",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Show matplotlib charts",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Detailed output",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))

    lsb_scale_sensor_info = {
        "minPhysVal": lsb_min_temp_c,
        "maxPhysVal": lsb_max_temp_c,
    }

    if args.verbose:
        print(f"Input JSON (LSB16): {args.input}")
        print(
            f"LSB scale: [{lsb_min_temp_c}, {lsb_max_temp_c}] °C  ({lsb_per_c:.4f} LSB/°C)"
        )
        print(f"ub_pt  = {ub_pt_c:.6f} °C  =  {ub_pt_lsb:.4f} LSB")
        print(f"ub_tmp = {ub_tmp_c:.6f} °C  =  {ub_tmp_lsb:.4f} LSB")

    result = calibrate(
        payload=payload,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        sample_size=sample_size,
        adc_max=adc_max,
        ub_pt_lsb=ub_pt_lsb,
        ub_tmp_lsb=ub_tmp_lsb,
        verbose=args.verbose,
    )

    report = build_report(
        temp_nominali=result["temp_nominali"],
        risultati_elaborati=result["risultati_elaborati"],
        a=result["A"],
        b=result["B"],
        u_a=result["u_A"],
        u_b=result["u_B"],
        cov_ab=result["cov_AB"],
        adc_bits=adc_bits,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        adc_max=adc_max,
        ub_pt_lsb=ub_pt_lsb,
        ub_tmp_lsb=ub_tmp_lsb,
    )
    args.report.write_text(report, encoding="utf-8")

    print("\n=== Calibration result (linear OLS, GUM) ===")
    print(
        json.dumps(
            {
                "A": result["A"],
                "B": result["B"],
                "u_A": result["u_A"],
                "u_B": result["u_B"],
                "cov_AB": result["cov_AB"],
                "expanded_uncertainties_degC": result["expanded_uncertainties"],
            },
            indent=2,
        )
    )
    print(f"\nReport written to: {args.report}")

    if args.charts:
        try:
            plot_charts(
                a=result["A"],
                b=result["B"],
                temp_nominali=result["temp_nominali"],
                dati_raw=result["dati_raw"],
                risultati_elaborati=result["risultati_elaborati"],
                sample_size=sample_size,
                lsb_scale_sensor_info=lsb_scale_sensor_info,
                adc_max=adc_max,
                ub_pt_lsb=ub_pt_lsb,
                ub_tmp_lsb=ub_tmp_lsb,
            )
        except Exception as ex:
            print(f"\nCharts disabled due to error: {ex}")


if __name__ == "__main__":
    main()
