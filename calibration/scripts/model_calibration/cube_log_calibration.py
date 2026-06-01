from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from .linear_calibration import (
    _get_data,
    get_scale_from_sensor,
    lsb16_to_phys,
    parse_step,
    phys_to_lsb16,
)

_KELVIN_OFFSET = 273.15


def _degc_to_kelvin(degc: np.ndarray) -> np.ndarray:
    """Convert °C to Kelvin. Reference readings are now native °C."""
    return degc + _KELVIN_OFFSET


def _lsb_to_kelvin(lsb: np.ndarray, sensor_info: Dict[str, Any], adc_max: float) -> np.ndarray:
    """Legacy helper — only used in plot/save_charts to convert sensor LSB to °C display."""
    return lsb16_to_phys(lsb, sensor_info, adc_max) + _KELVIN_OFFSET


def _kelvin_to_lsb(tk: np.ndarray, sensor_info: Dict[str, Any], adc_max: float) -> np.ndarray:
    return phys_to_lsb16(tk - _KELVIN_OFFSET, sensor_info, adc_max)


def _build_design_row(ln_d: float) -> np.ndarray:
    return np.array([1.0, ln_d, ln_d**3])


def _fit_steinhart_hart(
    x_lsb: np.ndarray,
    y_degc: np.ndarray,       # reference temperatures [°C] — native, not synthetic LSB
    sensor_info: Dict[str, Any] | None = None,
    adc_max: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n      = len(x_lsb)
    y_k    = _degc_to_kelvin(y_degc)   # °C → K
    y_inv  = 1.0 / y_k

    X = np.zeros((n, 3))
    for i in range(n):
        if x_lsb[i] <= 0:
            raise ValueError(f"NTC LSB value non-positive at step {i}: {x_lsb[i]}")
        X[i] = _build_design_row(np.log(x_lsb[i]))

    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)

    theta = XtX_inv @ X.T @ y_inv
    return theta, XtX_inv, X, y_inv


def _gum_propagation_cube_log(
    x_lsb: np.ndarray,
    y_degc: np.ndarray,    # reference temperatures [°C] — native
    u_x: np.ndarray,       # combined sensor uncertainty [LSB]
    u_y: np.ndarray,       # combined reference uncertainty [°C]
    sensor_info: Dict[str, Any] | None = None,
    adc_max: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(x_lsb)
    theta, XtX_inv, X, y_inv = _fit_steinhart_hart(x_lsb, y_degc)

    y_k = _degc_to_kelvin(y_degc)   # [K]

    cov_theta = np.zeros((3, 3))

    for i in range(n):
        ln_d_i = np.log(x_lsb[i])
        x_i    = X[i]

        # d(1/T_K)/d(T_degC) = -1 / T_K²   [K⁻¹/°C ≡ K⁻¹/K since ΔT is equal]
        # u_y[i] is in °C, which equals ΔK, so the product is dimensionally correct.
        dy_inv_i_dy_degc   = -1.0 / y_k[i]**2
        dtheta_dy_degc_i   = XtX_inv @ x_i * dy_inv_i_dy_degc

        dx_i_dx_lsb        = np.array([0.0, 1.0 / x_lsb[i], 3.0 * ln_d_i**2 / x_lsb[i]])
        dXty_dx_lsb_i      = dx_i_dx_lsb * y_inv[i]
        dXtX_dx_lsb_i      = np.outer(x_i, dx_i_dx_lsb) + np.outer(dx_i_dx_lsb, x_i)
        dtheta_dx_lsb_i    = XtX_inv @ (dXty_dx_lsb_i - dXtX_dx_lsb_i @ theta)

        cov_theta += np.outer(dtheta_dy_degc_i, dtheta_dy_degc_i) * u_y[i]**2
        cov_theta += np.outer(dtheta_dx_lsb_i,  dtheta_dx_lsb_i)  * u_x[i]**2

    u_theta = np.sqrt(np.maximum(0.0, np.diag(cov_theta)))
    return theta, u_theta, cov_theta


def steinhart_hart_predict(d_lsb: float, theta: np.ndarray) -> float:
    if d_lsb <= 0:
        raise ValueError(f"Non-positive NTC LSB value: {d_lsb}")
    ln_d  = np.log(d_lsb)
    inv_t = theta[0] + theta[1] * ln_d + theta[2] * ln_d**3
    if inv_t <= 0:
        raise ValueError(f"Steinhart-Hart returned non-positive 1/T = {inv_t} for D = {d_lsb} LSB.")
    return 1.0 / inv_t


def steinhart_hart_predict_degc(d_lsb: float, theta: np.ndarray) -> float:
    return steinhart_hart_predict(d_lsb, theta) - _KELVIN_OFFSET


def steinhart_hart_uncertainty(d_lsb: float, u_d_lsb: float, theta: np.ndarray, cov_theta: np.ndarray) -> float:
    if d_lsb <= 0:
        return float("nan")
    ln_d = np.log(d_lsb)
    g    = theta[0] + theta[1] * ln_d + theta[2] * ln_d**3
    if g <= 0:
        return float("nan")
    f    = 1.0 / g
    f2   = f**2

    dg_dC = np.array([1.0, ln_d, ln_d**3])
    df_dC = -f2 * dg_dC
    u2_coeff = df_dC @ cov_theta @ df_dC

    dg_dD    = (theta[1] + 3.0 * theta[2] * ln_d**2) / d_lsb
    df_dD    = -f2 * dg_dD
    u2_sensor = (df_dD * u_d_lsb)**2

    return float(np.sqrt(max(0.0, u2_coeff + u2_sensor)))


MIN_STEPS_CUBE_LOG = 3


def run_prechecks(
    payload: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run all pre-calibration checks for the cube-log (Steinhart-Hart) model.

    Checks performed:
      1. Node count — payload must have at least MIN_STEPS_CUBE_LOG (3) steps.
      2. Unit check — pint-based dimensional analysis on sensor/ref JSON (optional).

    Returns a dict with:
      ok            – True only when all checks pass
      steps_ok      – bool
      n_steps       – int, number of steps found in payload
      unit_check    – UnitCheckResult or None when check_units is False
      errors        – list of error strings (blocking issues)
      warnings      – list of warning strings (non-blocking)
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
    result["steps_ok"] = len(temp_nominali) >= MIN_STEPS_CUBE_LOG
    if not result["steps_ok"]:
        msg = f"Steinhart-Hart requires at least {MIN_STEPS_CUBE_LOG} calibration steps (got {len(temp_nominali)})."
        result["errors"].append(msg)
        result["ok"] = False

    if check_units and sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "cube-log")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[cube-log unit-check]")
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
    ub_pt_degc: float | None = None,   # type-B std uncertainty of the reference [°C]
    ub_tmp_lsb: float = 0.0,           # type-B std uncertainty of the NTC ADC [LSB]
    verbose: bool = False,
    risol_degc: float = 0.1,
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    convert_units: bool = False,
    # legacy alias
    ub_pt_lsb: float | None = None,
) -> Dict[str, Any]:
    # Backwards-compat shim
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
    lsb_per_c    = adc_max / (max_v - min_v)   # informational

    # x [LSB], y [°C] — mixed domain
    x_lsb  = np.array([risultati_elaborati[t]["pmean_log"] for t in temp_nominali], dtype=float)  # LSB
    y_degc = np.array([risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali], dtype=float)  # °C

    u_res_degC = risol_degc / np.sqrt(12.0)
    uc_tmp = np.array([np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali], dtype=float)   # LSB
    uc_pt  = np.array([np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_degc**2)  for t in temp_nominali], dtype=float)  # °C

    if verbose:
        print("\n\n --- Fine acquisizione dati (cube-log) ---")

    theta, u_theta, cov_theta = _gum_propagation_cube_log(x_lsb, y_degc, uc_tmp, uc_pt)
    C0, C1, C3          = theta
    u_C0, u_C1, u_C3   = u_theta

    if verbose:
        print(f"\nC0={C0:.10e}  C1={C1:.10e}  C3={C3:.10e}  [K⁻¹]")

    # ------------------------------------------------------------------
    # GUM uncertainty budget per step — everything in °C.
    # Local sensitivity: dT/dD|_i [K/LSB] from Steinhart-Hart:
    #   T_K = 1/g,  g = C0 + C1*ln(D) + C3*(ln(D))^3
    #   dT/dD = -T_K^2 * (C1/D + 3*C3*(ln(D))^2/D)   [K/LSB ≡ °C/LSB]
    # ------------------------------------------------------------------
    expanded_uncertainties: List[float] = []
    per_step_budget: List[dict] = []

    y_k = _degc_to_kelvin(y_degc)   # [K] for sensitivity computation

    for i, t in enumerate(temp_nominali):
        D_i    = float(x_lsb[i])
        ln_D_i = np.log(D_i)
        T_K_i  = float(steinhart_hart_predict(D_i, theta))   # [K]
        # |dT/dD| [K/LSB = °C/LSB]
        sens_i = abs(T_K_i**2 * (C1 / D_i + 3.0 * C3 * ln_D_i**2 / D_i))

        uA_ref    = risultati_elaborati[t]["pstd_rtd"]          # °C (already)
        uA_i      = risultati_elaborati[t]["pstd_log"] * sens_i  # LSB * °C/LSB = °C
        uB_i_degC = ub_tmp_lsb * sens_i                         # LSB * °C/LSB = °C

        mu_T_ref = np.sqrt(uA_ref**2 + ub_pt_degc**2)
        mu_T_i   = np.sqrt(uA_i**2 + uB_i_degC**2 + u_res_degC**2)
        mu_E     = np.sqrt(mu_T_ref**2 + mu_T_i**2)
        U_E      = 2.0 * mu_E
        u_sh_k   = steinhart_hart_uncertainty(D_i, uc_tmp[i], theta, cov_theta)

        expanded_uncertainties.append(float(U_E))
        per_step_budget.append({
            "t_nominal": t, "uA_ref_degC": uA_ref, "uA_i_degC": uA_i,
            "uB_ref_degC": ub_pt_degc, "uB_i_degC": uB_i_degC, "u_res_degC": u_res_degC,
            "sens_i_degc_per_lsb": sens_i,
            "mu_T_ref": mu_T_ref, "mu_T_i": mu_T_i, "mu_E": mu_E, "U_E": U_E,
            "u_SH_K": u_sh_k, "U_SH_K": 2.0 * u_sh_k,
        })

    # ref_temp_means: already in °C (pmean_rtd is now °C)
    ref_temp_means: List[float] = [
        float(risultati_elaborati[t]["pmean_rtd"])
        for t in temp_nominali
    ]

    result: Dict[str, Any] = {
        "model": "cube-log",
        "C0": float(C0), "C1": float(C1), "C3": float(C3),
        "u_C0": float(u_C0), "u_C1": float(u_C1), "u_C3": float(u_C3),
        "cov_theta": cov_theta.tolist(),
        "theta": theta.tolist(),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "per_step_budget": per_step_budget,
        "ref_temp_means": ref_temp_means,
        "lsb_per_c": lsb_per_c,       # informational
        "ub_pt_degc": ub_pt_degc,     # [°C]
        "ub_tmp_lsb": ub_tmp_lsb,     # [LSB]
        "ub_pt_lsb": ub_pt_degc * lsb_per_c,   # legacy compat
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
    C0: float, C1: float, C3: float,
    u_C0: float, u_C1: float, u_C3: float,
    cov_theta: List[List[float]],
    adc_bits: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    expanded_uncertainties: List[float],
    per_step_budget: List[dict],
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)
    theta        = np.array([C0, C1, C3])

    lines: List[str] = []
    lines.append("# Calibration Report — Cubic-Log (Steinhart-Hart) GUM OLS")
    lines.append("")
    lines.append("## Model: 1/T [K⁻¹] = C0 + C1·ln(D) + C3·(ln(D))³")
    lines.append("")
    lines.append("## Per-step statistics [LSB]")
    lines.append("| step | target [°C] | pmean_ref [LSB] | pstd_ref | pmean_sensor [LSB] | pstd_sensor | max_error | mean_error |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, t in enumerate(temp_nominali):
        r = risultati_elaborati[t]
        lines.append(f"| {i} | {t:.3f} | {r['pmean_rtd']:.2f} | {r['pstd_rtd']:.4f} | {r['pmean_log']:.2f} | {r['pstd_log']:.4f} | {r['max_error']:.4f} | {r['mean_error']:.4f} |")

    lines.append("")
    lines.append("## Calibration coefficients")
    lines.append("| Coeff | Value [K⁻¹] | Std unc u [K⁻¹] |")
    lines.append("|---|---|---|")
    for name, val, unc in zip(["C0", "C1", "C3"], [C0, C1, C3], [u_C0, u_C1, u_C3]):
        lines.append(f"| {name} | {val:.10e} | {unc:.6e} |")

    lines.append("")
    lines.append("## Uncertainty budget U(E) [°C, k=2]")
    lines.append(f"- LSB scale: [{min_v}, {max_v}] °C  → {lsb_per_c:.4f} LSB/°C")
    lines.append("| step [°C] | u(T_ref) [°C] | u(T_i) [°C] | u(E) [°C] | U(E) [°C] | U_SH k=2 [K] |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for b in per_step_budget:
        lines.append(f"| {b['t_nominal']:.1f} | {b['mu_T_ref']:.6f} | {b['mu_T_i']:.6f} | {b['mu_E']:.6f} | {b['U_E']:.6f} | {b['U_SH_K']:.6f} |")

    return "\n".join(lines) + "\n"


def plot_charts(
    theta: List[float],
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    cov_theta: List[List[float]] | None = None,
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")

    theta_arr = np.array(theta)
    cov_arr   = np.array(cov_theta) if cov_theta is not None else None
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)

    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_lsb**2) for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali]

    t_cal_c = []
    u_sh_c  = []
    for lv, le in zip(log_val, log_err):
        try:
            t_cal_c.append(steinhart_hart_predict_degc(float(lv), theta_arr))
        except ValueError:
            t_cal_c.append(float("nan"))
        u_sh_c.append(steinhart_hart_uncertainty(float(lv), le, theta_arr, cov_arr) if cov_arr is not None else 0.0)

    ref_c     = [lsb16_to_phys(np.array([rv]), lsb_scale_sensor_info, adc_max)[0] for rv in rtd_val]
    residuals = [tc - rc for tc, rc in zip(t_cal_c, ref_c)]

    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Cube-Log (Steinhart-Hart) Calibration", fontsize=14)
    ax = axs[0]
    ax.set_title("Calibration Curve", fontsize=12)
    ax.errorbar(log_val, ref_c, xerr=log_err, yerr=[e/lsb_per_c for e in rtd_err], fmt="b.", capsize=4, label="PT100 ref")
    ax.errorbar(log_val, t_cal_c, xerr=log_err, yerr=u_sh_c, fmt="r.", capsize=4, label="S-H model")
    d_range = np.linspace(min(log_val)*0.99, max(log_val)*1.01, 300)
    t_smooth = []
    for d in d_range:
        try:
            t_smooth.append(steinhart_hart_predict_degc(d, theta_arr))
        except ValueError:
            t_smooth.append(float("nan"))
    ax.plot(d_range, t_smooth, "r-", linewidth=1, label="S-H curve")
    ax.set_xlabel("NTC reading [LSB]")
    ax.set_ylabel("Temperature [°C]")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2 = axs[1]
    ax2.set_title("Residuals (T_cal − T_ref) [°C]", fontsize=12)
    ax2.axhline(0, color="k", linestyle="--", alpha=0.5)
    ax2.errorbar(temp_nominali, residuals, yerr=u_sh_c, fmt="ro", capsize=5, label="residual ± u_SH")
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel("Nominal temperature [°C]")
    ax2.set_ylabel("Residual [°C]")
    ax2.legend()
    plt.tight_layout()
    plt.show()


def save_charts(
    theta: List[float],
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
    output_dir: Path,
    cov_theta: List[List[float]] | None = None,
    prefix: str = "calib_cube_log",
) -> List[Path]:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")

    theta_arr = np.array(theta)
    cov_arr   = np.array(cov_theta) if cov_theta is not None else None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c    = adc_max / (max_v - min_v)

    rtd_val = [risultati_elaborati[t]["pmean_rtd"] for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_log"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_rtd"]**2 + ub_pt_lsb**2) for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_log"]**2 + ub_tmp_lsb**2) for t in temp_nominali]

    t_cal_c = []
    u_sh_c  = []
    for lv, le in zip(log_val, log_err):
        try:
            t_cal_c.append(steinhart_hart_predict_degc(float(lv), theta_arr))
        except ValueError:
            t_cal_c.append(float("nan"))
        u_sh_c.append(steinhart_hart_uncertainty(float(lv), le, theta_arr, cov_arr) if cov_arr is not None else 0.0)

    ref_c     = [lsb16_to_phys(np.array([rv]), lsb_scale_sensor_info, adc_max)[0] for rv in rtd_val]
    residuals = [tc - rc for tc, rc in zip(t_cal_c, ref_c)]

    saved: List[Path] = []

    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Cube-Log (Steinhart-Hart) Calibration", fontsize=14)
    ax = axs[0]
    ax.set_title("Calibration Curve", fontsize=12)
    ax.errorbar(log_val, ref_c, xerr=log_err, yerr=[e/lsb_per_c for e in rtd_err], fmt="b.", capsize=4, label="PT100 ref")
    ax.errorbar(log_val, t_cal_c, xerr=log_err, yerr=u_sh_c, fmt="r.", capsize=4, label="S-H model")
    d_range = np.linspace(min(log_val)*0.99, max(log_val)*1.01, 300)
    t_smooth = []
    for d in d_range:
        try:
            t_smooth.append(steinhart_hart_predict_degc(d, theta_arr))
        except ValueError:
            t_smooth.append(float("nan"))
    ax.plot(d_range, t_smooth, "r-", linewidth=1, label="S-H curve")
    ax.set_xlabel("NTC reading [LSB]")
    ax.set_ylabel("Temperature [°C]")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2 = axs[1]
    ax2.set_title("Residuals (T_cal − T_ref) [°C]", fontsize=12)
    ax2.axhline(0, color="k", linestyle="--", alpha=0.5)
    ax2.errorbar(temp_nominali, residuals, yerr=u_sh_c, fmt="ro", capsize=5, label="residual ± u_SH")
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel("Nominal temperature [°C]")
    ax2.set_ylabel("Residual [°C]")
    ax2.legend()
    p1 = output_dir / f"{prefix}_fig1_calibration.png"
    fig.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig)
    saved.append(p1)

    n_plots = min(len(temp_nominali), 6)
    fig2, axs2 = plt.subplots(2, 3, figsize=(18, 10))
    axf = axs2.flatten()
    fig2.suptitle(f"Analisi Campioni (n={sample_size}) [LSB] — Cube-Log", fontsize=14)
    for i, temp in enumerate(temp_nominali[:n_plots]):
        ax = axf[i]
        res = risultati_elaborati.get(temp, {})
        if res:
            xax      = res["x_axis"]
            ub_pt_b  = np.sqrt(res["std_rtd"]**2 + ub_pt_lsb**2)
            ub_tmp_b = np.sqrt(res["std_log"]**2 + ub_tmp_lsb**2)
            ax.plot(xax, res["smean_rtd"], "b-o", label="RTD",    linewidth=1, markersize=2)
            ax.fill_between(xax, res["smean_rtd"] - ub_pt_b,  res["smean_rtd"] + ub_pt_b,  alpha=0.15, color="b")
            ax.plot(xax, res["smean_log"], "r-o", label="Sensor", linewidth=1, markersize=2)
            ax.fill_between(xax, res["smean_log"] - ub_tmp_b, res["smean_log"] + ub_tmp_b, alpha=0.15, color="r")
            ax.set_title(f"Nominal: {temp} °C")
            ax.set_ylabel("LSB")
            ax.grid(True, alpha=0.3)
            if i == 0:
                ax.legend()
        else:
            ax.text(0.5, 0.5, "No data", ha="center", transform=ax.transAxes)
    for j in range(n_plots, 6):
        axf[j].set_visible(False)
    p2 = output_dir / f"{prefix}_fig2_sample_means.png"
    fig2.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    saved.append(p2)

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

    default_input  = calib_root / "test" / "data_in" / "export2_tmp126_lsb16.json"
    default_report = calib_root / "certificato_out" / "calibration_report_cube_log.md"

    adc_bits   = extra._adc_bits
    adc_max    = float((1 << adc_bits) - 1)
    lsb_min    = sensor._minPhyThreshold
    lsb_max    = sensor._maxPhyThreshold

    ub_pt_degc = extra._U_pt_c / extra._k_pt   # [°C]
    ub_tmp_lsb = sensor.uB                      # [LSB] from sensor JSON

    parser = argparse.ArgumentParser(description="NTC cube-log (Steinhart-Hart) calibration — GUM OLS, mixed domain")
    parser.add_argument("--input",   type=Path, default=default_input)
    parser.add_argument("--report",  type=Path, default=default_report)
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    payload  = json.loads(args.input.read_text(encoding="utf-8"))
    lsb_info = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}

    result = calibrate(
        payload=payload, lsb_scale_sensor_info=lsb_info, sample_size=20,
        adc_max=adc_max, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
        verbose=args.verbose, risol_degc=sensor.resolution_degC,
    )

    report = build_report(
        temp_nominali=result["temp_nominali"],
        risultati_elaborati=result["risultati_elaborati"],
        C0=result["C0"], C1=result["C1"], C3=result["C3"],
        u_C0=result["u_C0"], u_C1=result["u_C1"], u_C3=result["u_C3"],
        cov_theta=result["cov_theta"], adc_bits=adc_bits,
        lsb_scale_sensor_info=lsb_info, adc_max=adc_max,
        ub_pt_lsb=result["ub_pt_lsb"], ub_tmp_lsb=ub_tmp_lsb,
        expanded_uncertainties=result["expanded_uncertainties"],
        per_step_budget=result["per_step_budget"],
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print("\n=== Calibration result (cube-log, Steinhart-Hart, GUM OLS) ===")
    print(json.dumps({"model": result["model"], "C0": result["C0"], "C1": result["C1"],
                      "C3": result["C3"], "u_C0": result["u_C0"], "u_C1": result["u_C1"],
                      "u_C3": result["u_C3"],
                      "expanded_uncertainties_degC": result["expanded_uncertainties"]}, indent=2))
    print(f"\nReport written to: {args.report}")

    if args.charts:
        try:
            plot_charts(
                theta=result["theta"], temp_nominali=result["temp_nominali"],
                dati_raw=result["dati_raw"], risultati_elaborati=result["risultati_elaborati"],
                sample_size=20, lsb_scale_sensor_info=lsb_info,
                adc_max=adc_max, ub_pt_lsb=ub_pt_lsb, ub_tmp_lsb=ub_tmp_lsb,
                cov_theta=result["cov_theta"],
            )
        except Exception as ex:
            print(f"\nCharts disabled: {ex}")


if __name__ == "__main__":
    main()
