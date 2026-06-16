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

_POLY_DEGREE = 3
_N_COEFFS    = _POLY_DEGREE + 1


def _regressor_row(d: float) -> np.ndarray:
    return np.array([d**k for k in range(_N_COEFFS)])


def _d_regressor_row_dD(d: float) -> np.ndarray:
    return np.array([k * d**(k - 1) if k > 0 else 0.0 for k in range(_N_COEFFS)])


def _build_design_matrix(x_lsb: np.ndarray) -> np.ndarray:
    X = np.zeros((len(x_lsb), _N_COEFFS))
    for i, d in enumerate(x_lsb):
        X[i] = _regressor_row(d)
    return X


def _fit_cubic(x_lsb: np.ndarray, y_lsb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = _build_design_matrix(x_lsb)
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)
    theta = XtX_inv @ X.T @ y_lsb
    return theta, XtX_inv, X


def _gum_propagation_cubic(
    x_lsb: np.ndarray,
    y_lsb: np.ndarray,
    u_x: np.ndarray,
    u_y: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    theta, XtX_inv, X = _fit_cubic(x_lsb, y_lsb)
    cov_theta = np.zeros((_N_COEFFS, _N_COEFFS))

    for i in range(len(x_lsb)):
        d_i = x_lsb[i]
        x_i = X[i]
        g_i = _d_regressor_row_dD(d_i)

        dtheta_dy_i  = XtX_inv @ x_i
        dXty_dxi     = g_i * y_lsb[i]
        dXtX_dxi     = np.outer(x_i, g_i) + np.outer(g_i, x_i)
        dtheta_dxi   = XtX_inv @ (dXty_dxi - dXtX_dxi @ theta)

        cov_theta += np.outer(dtheta_dy_i, dtheta_dy_i) * u_y[i]**2
        cov_theta += np.outer(dtheta_dxi,  dtheta_dxi)  * u_x[i]**2

    u_theta = np.sqrt(np.maximum(0.0, np.diag(cov_theta)))
    return theta, u_theta, cov_theta


def cubic_predict(d_lsb: float, theta: np.ndarray) -> float:
    return float(np.dot(_regressor_row(d_lsb), theta))


def cubic_predict_y(d_lsb: float, theta: np.ndarray,
                        lsb_scale: Dict[str, Any] | None = None,
                        adc_max: float = 0.0) -> float:

    return cubic_predict(d_lsb, theta)   # already in physical units


def cubic_uncertainty(d_lsb: float, u_d_lsb: float, theta: np.ndarray, cov_theta: np.ndarray, lsb_per_y: float = 1.0) -> float:
    # Return u(T_cal) in °C for the cubic model at reading d_lsb.

    # The model T [°C] = a0 + a1*D + a2*D^2 + a3*D^3 is already in °C, so
    # cov_theta carries °C² units and the result is directly in °C.
    # The lsb_per_y parameter is kept for backward compatibility but ignored.

    x      = _regressor_row(d_lsb)
    g      = _d_regressor_row_dD(d_lsb)
    df_dD  = float(np.dot(g, theta))   # local sensitivity [°C/LSB]
    u2_coeff  = float(x @ cov_theta @ x)      # [°C²] — cov_theta in °C²
    u2_sensor = (df_dD * u_d_lsb)**2          # (°C/LSB)² * LSB² = °C²
    return float(np.sqrt(max(0.0, u2_coeff + u2_sensor)))   # [°C]


def run_prechecks(
    payload: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    #   ok            – True only when all checks pass
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
    result["steps_ok"] = len(temp_nominali) >= _N_COEFFS
    if not result["steps_ok"]:
        msg = f"Cubic calibration requires at least {_N_COEFFS} steps (got {len(temp_nominali)})."
        result["errors"].append(msg)
        result["ok"] = False

    if sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "cubic")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[cubic unit-check]")
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
    ub_ref_y: float | None = None,   # type-B std uncertainty of the reference [Y]
    ub_sensor_lsb: float = 0.0,    # type-B std uncertainty of the NTC ADC [LSB]
    verbose: bool = False,
    risol: float = 0.1,
    old_a: float | None = None,
    old_b: float | None = None,
    old_c: float | None = None,
    old_d: float | None = None,
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    convert_units: bool = False,
    unit_symbol: str = "°C",
    # legacy alias
    ub_pt_lsb: float | None = None,
    formula: str | None = None,
    formula_vars: Dict[str, float] | None = None,
    ufit: float | None = None,
    ufitfromJson: bool = False,  # se True, usa ufit dai residui del fit (rmse); se False, usa ufit dichiarato nel JSON sensore
    coverage_factor: float = 2.0,
) -> Dict[str, Any]:
    # Backwards-compat shim for old ub_pt_lsb callers
    if ub_pt_lsb is not None and ub_ref_y is None:
        min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
        lsb_per_y_local = adc_max / max(max_v - min_v, 1e-12)
        ub_ref_y = ub_pt_lsb / lsb_per_y_local
    if ub_ref_y is None:
        raise ValueError("calibrate() requires ub_ref_y [Y]")

    pre = run_prechecks(payload, sensor_json, ref_json, verbose)
    unit_check_result = pre["unit_check"]
    if not pre["ok"]:
        raise ValueError("\n".join(pre["errors"]))

    temp_nominali = [parse_step(s)[0] for s in payload.get("steps", [])]

    dati_raw, risultati_elaborati = _get_data(payload, temp_nominali, sample_size, lsb_scale_sensor_info, adc_max, verbose, unit_symbol)

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)   # informational

    # x [LSB], y [°C] — mixed domain
    x_lsb  = np.array([risultati_elaborati[t]["pmean_sensor"] for t in temp_nominali], dtype=float)  # LSB
    y_phys = np.array([risultati_elaborati[t]["pmean_ref"]    for t in temp_nominali], dtype=float)  # Y

    # Per-step ub_sensor_lsb via formula evaluation or single fixed value
    _ub_arr: np.ndarray
    if formula and formula_vars:
        from evaluation_formula import evaluate_formula, qs
        _ub_per_step = []
        for i, t in enumerate(temp_nominali):
            D_i = float(x_lsb[i])
            _vars_i = {**formula_vars, "d_in": qs(D_i)}
            _ub_per_step.append(float(evaluate_formula(formula, _vars_i).magnitude))
        _ub_arr = np.array(_ub_per_step, dtype=float)
        if verbose:
            _ub_mean = float(np.mean(_ub_arr))
            print(f"ub_sensor (per-step via formula): mean={_ub_mean:.4f} LSB, values={_ub_per_step}")
    else:
        _ub_arr = np.full(len(temp_nominali), ub_sensor_lsb, dtype=float)

    u_res = risol / np.sqrt(12.0)
    uc_tmp = np.array([np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + _ub_arr[i]**2) for i, t in enumerate(temp_nominali)], dtype=float)  # LSB
    uc_pt  = np.array([np.sqrt(risultati_elaborati[t]["pstd_ref"]**2    + ub_ref_y**2)   for t in temp_nominali], dtype=float)  # Y

    if verbose:
        print("\n\n --- Fine acquisizione dati (cubic) ---")

    theta, u_theta, cov_theta = _gum_propagation_cubic(x_lsb, y_phys, uc_tmp, uc_pt)
    a0, a1, a2, a3             = theta
    u_a0, u_a1, u_a2, u_a3    = u_theta

    # Regression uncertainty (RMSE) with degrees-of-freedom correction — N−4 for cubic
    y_pred = np.array([cubic_predict(float(x_lsb[i]), theta) for i in range(len(x_lsb))])
    e_fit  = y_phys - y_pred
    N_cub  = len(x_lsb)
    rmse   = float(np.sqrt(np.sum(e_fit**2) / max(1, N_cub - 4)))

    if verbose:
        print(f"\na0={a0:.10e} {unit_symbol}  a1={a1:.10e} {unit_symbol}/LSB  a2={a2:.10e} {unit_symbol}/LSB²  a3={a3:.10e} {unit_symbol}/LSB³")
        print(f"RMSE (N={N_cub}, p=4): {rmse:.6f} {unit_symbol}")

    if all(v is not None for v in [old_a, old_b, old_c, old_d]):
        old_theta = np.array([old_a, old_b, old_c, old_d], dtype=float)
        y_old     = np.array([cubic_predict(float(d), old_theta) for d in x_lsb])
        err_old   = y_old - y_phys
        if verbose:
            print(f"\nBaseline pre-fit mean error: {np.mean(err_old):.6f} {unit_symbol}")

    
    # GUM uncertainty budget per step — everything in physical unit.
    # Local sensitivity: dY/dD|_i = a1 + 2*a2*D_i + 3*a3*D_i² [{unit_symbol}/LSB]
    
    u_fitting_val = ufit if ufit is not None else rmse
    u_fitting_val = rmse
    print("ufitt ", u_fitting_val)

    expanded_uncertainties: List[float] = []
    per_step_budget: List[dict] = []

    for i, t in enumerate(temp_nominali):
        D_i   = float(x_lsb[i])
        sens_i = abs(a1 + 2.0 * a2 * D_i + 3.0 * a3 * D_i**2)  # |dT/dD| [°C/LSB]

        uA_ref    = risultati_elaborati[t]["pstd_ref"]              # u_y type-A
        uA_sensor = risultati_elaborati[t]["pstd_sensor"] * sens_i  # u_x type-A × sens

        u_ref    = np.sqrt(uA_ref**2 + ub_ref_y**2)
        ub_uso   = np.sqrt(u_fitting_val**2 + u_res**2)
        uc_sensor = np.sqrt(uA_sensor**2 + ub_uso**2)
        u_c      = np.sqrt(u_ref**2 + uc_sensor**2)
        U_exp    = coverage_factor * u_c
        u_cal    = cubic_uncertainty(D_i, uc_tmp[i], theta, cov_theta)

        expanded_uncertainties.append(float(U_exp))
        per_step_budget.append({
            "t_nominal": t, "uA_ref": uA_ref, "uA_sensor": uA_sensor,
            "uB_ref": ub_ref_y, "u_res": u_res,
            "sens_i": sens_i,
            "ub_uso": ub_uso, "u_fitting": u_fitting_val,
            "mu_T_ref": u_ref, "mu_T_i": uc_sensor, "mu_E": u_c, "U_E": U_exp,
            "u_cal_poly": u_cal, "U_cal_poly": coverage_factor * u_cal,
        })

    ref_temp_means: List[float] = [
        float(risultati_elaborati[t]["pmean_ref"])
        for t in temp_nominali
    ]

    result: Dict[str, Any] = {
        "model": "cubic",
        "theta": theta.tolist(),
        "a0": float(a0), "a1": float(a1), "a2": float(a2), "a3": float(a3),
        "u_a0": float(u_a0), "u_a1": float(u_a1), "u_a2": float(u_a2), "u_a3": float(u_a3),
        "cov_theta": cov_theta.tolist(),
        "rmse": rmse,
        "u_fitting": u_fitting_val,
        "old_a0": None if old_a is None else float(old_a),
        "old_a1": None if old_b is None else float(old_b),
        "old_a2": None if old_c is None else float(old_c),
        "old_a3": None if old_d is None else float(old_d),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "per_step_budget": per_step_budget,
        "ref_temp_means": ref_temp_means,
        "lsb_per_y": lsb_per_y,       # informational
        "ub_ref_y": ub_ref_y,      # [°C]
        "ub_sensor_lsb": ub_sensor_lsb,  # [LSB]
        "ub_sensor_lsb_per_step": _ub_arr.tolist(),
        "ub_ref_lsb": ub_ref_y * lsb_per_y,  # legacy compat
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
    theta: List[float],
    u_theta: List[float],
    cov_theta: List[List[float]],
    adc_bits: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_ref_lsb: float,
    ub_sensor_lsb: float,
    expanded_uncertainties: List[float],
    per_step_budget: List[dict],
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)
    theta_arr    = np.array(theta)

    lines: List[str] = []
    lines.append("# Calibration Report — Cubic Polynomial GUM OLS")
    lines.append("")
    lines.append("## Model: T_ref_lsb = a0 + a1·D + a2·D² + a3·D³")
    lines.append("")
    lines.append("## Per-step statistics [LSB]")
    lines.append("| step | target [°C] | pmean_ref [LSB] | pstd_ref | pmean_sensor [LSB] | pstd_sensor | max_error | mean_error |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, t in enumerate(temp_nominali):
        r = risultati_elaborati[t]
        lines.append(f"| {i} | {t:.3f} | {r['pmean_ref']:.2f} | {r['pstd_ref']:.4f} | {r['pmean_sensor']:.2f} | {r['pstd_sensor']:.4f} | {r['max_error']:.4f} | {r['mean_error']:.4f} |")

    lines.append("")
    lines.append("## Calibration coefficients")
    lines.append("| Coeff | Value | Std unc u |")
    lines.append("|---|---|---|")
    for name, val, unc in zip(["a0", "a1", "a2", "a3"], theta, u_theta):
        lines.append(f"| {name} | {val:.10e} | {unc:.6e} |")

    lines.append("")
    lines.append("## Uncertainty budget U(E) [°C, k=2]")
    lines.append(f"- LSB scale: [{min_v}, {max_v}] °C  → {lsb_per_y:.4f} LSB/°C")
    lines.append("| step [°C] | u(T_ref) [°C] | u(T_i) [°C] | u(E) [°C] | U(E) [°C] | U_poly k=2 [°C] |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for b in per_step_budget:
        lines.append(f"| {b['t_nominal']:.1f} | {b['mu_T_ref']:.6f} | {b['mu_T_i']:.6f} | {b['mu_E']:.6f} | {b['U_E']:.6f} | {b['U_cal_poly_y']:.6f} |")

    return "\n".join(lines) + "\n"


def plot_charts(
    theta: List[float],
    temp_nominali: List[float],
    dati_raw: Dict[float, Dict[str, np.ndarray]],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    sample_size: int,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_ref_lsb: float,
    ub_sensor_lsb: float,
    cov_theta: List[List[float]] | None = None,
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")

    theta_arr = np.array(theta)
    cov_arr   = np.array(cov_theta) if cov_theta is not None else np.zeros((_N_COEFFS, _N_COEFFS))
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y    = adc_max / (max_v - min_v)

    rtd_val = [risultati_elaborati[t]["pmean_ref"]    for t in temp_nominali]
    log_val = [risultati_elaborati[t]["pmean_sensor"] for t in temp_nominali]
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_ref"]**2    + ub_ref_lsb**2)    for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + ub_sensor_lsb**2) for t in temp_nominali]

    ref_c     = [lsb16_to_phys(np.array([rv]), lsb_scale_sensor_info, adc_max)[0] for rv in rtd_val]
    t_cal_c   = [cubic_predict_y(float(lv), theta_arr, lsb_scale_sensor_info, adc_max) for lv in log_val]
    u_cal_c   = [cubic_uncertainty(float(lv), le, theta_arr, cov_arr, lsb_per_y) for lv, le in zip(log_val, log_err)]
    residuals = [tc - rc for tc, rc in zip(t_cal_c, ref_c)]

    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Cubic Polynomial Calibration", fontsize=14)
    ax = axs[0]
    ax.set_title("Calibration Curve [°C]", fontsize=12)
    log_val_c = [lsb16_to_phys(np.array([lv]), lsb_scale_sensor_info, adc_max)[0] for lv in log_val]
    ax.errorbar(log_val_c, ref_c, xerr=[e/lsb_per_y for e in log_err], yerr=[e/lsb_per_y for e in rtd_err], fmt="b.", capsize=4, label="PT100 ref")
    ax.errorbar(log_val_c, t_cal_c, yerr=u_cal_c, fmt="r.", capsize=4, label="cubic model")
    for i, (x_i, y_i, t_i) in enumerate(zip(log_val_c, t_cal_c, temp_nominali)):
        ax.annotate(f"{t_i:.0f}", (x_i, y_i),
                    textcoords="offset points", xytext=(4, -8),
                    fontsize=7, alpha=0.7, color="tab:red")
    d_range = np.linspace(min(log_val)*0.99, max(log_val)*1.01, 300)
    t_smooth = [cubic_predict_y(d, theta_arr, lsb_scale_sensor_info, adc_max) for d in d_range]
    ax.plot(lsb16_to_phys(d_range, lsb_scale_sensor_info, adc_max), t_smooth, "r-", linewidth=1, label="cubic curve")
    ax.set_xlabel("NTC reading [°C equivalent]")
    ax.set_ylabel("Temperature [°C]")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2 = axs[1]
    ax2.set_title("Residuals (T_cal − T_ref) [°C]", fontsize=12)
    ax2.axhline(0, color="k", linestyle="--", alpha=0.5)
    ax2.errorbar(temp_nominali, residuals, yerr=u_cal_c, fmt="ro", capsize=5, label="residual ± u_poly")
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
    ub_ref_lsb: float,
    ub_sensor_lsb: float,
    output_dir: Path,
    cov_theta: List[List[float]] | None = None,
    prefix: str = "calib_cubic",
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
    _calib_result: Dict[str, Any] | None = None,
    coverage_factor: float = 2.0,
) -> List[Path]:
    """Produce 5 calibration charts for the cubic OLS model.

    Passes ``_calib_result`` (the full dict from ``calibrate()``) to
    ``calib_plots.bundle_from_cubic`` so that the GUM per-point budget is
    used for correct uncertainty error bars.  When ``_calib_result`` is not
    supplied the budget is recomputed from the individual args.
    """
    from .calib_plots import bundle_from_cubic, save_five_charts

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    ub_ref_y = ub_ref_lsb / lsb_per_y

    if _calib_result is not None:
        result = _calib_result
    else:
        theta_arr = np.array(theta)
        u_res = 0.1 / np.sqrt(12.0)
        uB_sensor_conv = ub_sensor_lsb / lsb_per_y
        exp_unc: List[float] = []
        budget: List[Dict[str, Any]] = []
        for t in temp_nominali:
            r = risultati_elaborati[t]
            uA_ref    = r["pstd_ref"]
            uA_sensor = r["pstd_sensor"] / lsb_per_y
            u_ref_    = float(np.sqrt(uA_ref**2 + ub_ref_y**2))
            u_sensor_ = float(np.sqrt(uA_sensor**2 + uB_sensor_conv**2 + u_res**2))
            u_c_      = float(np.sqrt(u_ref_**2 + u_sensor_**2))
            exp_unc.append(coverage_factor * u_c_)
            budget.append({
                "t_nominal": t,
                "mu_T_ref":  u_ref_,
                "mu_T_i":    u_sensor_,
                "mu_E":      u_c_,
                "U_E":       coverage_factor * u_c_,
            })
        result = {
            "model": "cubic",
            "theta": theta,
            "a0": theta[0], "a1": theta[1], "a2": theta[2], "a3": theta[3],
            "cov_theta": cov_theta or [[0.0]*4]*4,
            "temp_nominali": temp_nominali,
            "risultati_elaborati": risultati_elaborati,
            "ref_temp_means": [float(risultati_elaborati[t]["pmean_ref"]) for t in temp_nominali],
            "expanded_uncertainties": exp_unc,
            "per_step_budget": budget,
            "ub_ref_y":   ub_ref_y,
            "ub_ref_lsb":    ub_ref_lsb,
            "ub_sensor_lsb": ub_sensor_lsb,
            "lsb_per_y":     lsb_per_y,
        }

    bundle = bundle_from_cubic(
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

    adc_bits   = sensor_json.get("ranges", {}).get("elec", {}).get("adcBits", 16)
    adc_max    = float((1 << adc_bits) - 1)
    lsb_min    = float(sensor_json.get("ranges", {}).get("threshold", {}).get("min", -40.0))
    lsb_max    = float(sensor_json.get("ranges", {}).get("threshold", {}).get("max", 105.0))

    ub_ref_y   = 0.0325   # [°C]
    _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    ub_sensor_lsb = float(_lookup(_sensor_ru, "varName", "uB", {}).get("value", 0.30))                     # [LSB] from sensor JSON

    default_input  = calib_root / "test" / "data_in" / "export2_tmp126_lsb16.json"
    default_report = calib_root / "certificato_out" / "calibration_report_cubic.md"

    parser = argparse.ArgumentParser(description="NTC cubic polynomial calibration — GUM OLS, mixed domain")
    parser.add_argument("--input",   type=Path, default=default_input)
    parser.add_argument("--report",  type=Path, default=default_report)
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    payload  = json.loads(args.input.read_text(encoding="utf-8"))
    lsb_info = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}
    _lsb_per_y = adc_max / (lsb_max - lsb_min) if lsb_max != lsb_min else 452.0
    _risol_lsb = float(_lookup(_sensor_ru, "varName", "resolution", {}).get("value", 1))
    risol = _risol_lsb / _lsb_per_y

    result = calibrate(
        payload=payload, lsb_scale_sensor_info=lsb_info, sample_size=20,
        adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
        verbose=args.verbose, risol=risol,
    )

    report = build_report(
        temp_nominali=result["temp_nominali"],
        risultati_elaborati=result["risultati_elaborati"],
        theta=result["theta"],
        u_theta=[result["u_a0"], result["u_a1"], result["u_a2"], result["u_a3"]],
        cov_theta=result["cov_theta"], adc_bits=adc_bits,
        lsb_scale_sensor_info=lsb_info, adc_max=adc_max,
        ub_ref_lsb=result["ub_ref_lsb"], ub_sensor_lsb=ub_sensor_lsb,
        expanded_uncertainties=result["expanded_uncertainties"],
        per_step_budget=result["per_step_budget"],
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print("\n=== Calibration result (cubic GUM OLS) ===")
    print(json.dumps({"model": result["model"], "a0": result["a0"], "a1": result["a1"],
                      "a2": result["a2"], "a3": result["a3"],
                      "u_a0": result["u_a0"], "u_a1": result["u_a1"],
                      "u_a2": result["u_a2"], "u_a3": result["u_a3"],
                      "expanded_uncertainties_degC": result["expanded_uncertainties"]}, indent=2))
    print(f"\nReport written to: {args.report}")

    if args.charts:
        try:
            plot_charts(
                theta=result["theta"], temp_nominali=result["temp_nominali"],
                dati_raw=result["dati_raw"], risultati_elaborati=result["risultati_elaborati"],
                sample_size=20, lsb_scale_sensor_info=lsb_info,
                adc_max=adc_max, ub_ref_lsb=result["ub_ref_lsb"], ub_sensor_lsb=ub_sensor_lsb,
                cov_theta=result["cov_theta"],
            )
        except Exception as ex:
            print(f"\nCharts disabled: {ex}")


if __name__ == "__main__":
    main()
