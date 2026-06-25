from __future__ import annotations

import argparse
import json
from math import log
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

from evaluation_formula import evaluate_formula, qs, build_formula_variables

_N_COEFFS = 3  # a, b, c


def _regressor_row(ln_r: float) -> np.ndarray:
    return np.array([1.0, ln_r, ln_r**3])


def _d_regressor_row_dlnR(ln_r: float) -> np.ndarray:
    return np.array([0.0, 1.0, 3.0 * ln_r**2])


def _d_regressor_row_dR(R: float) -> np.ndarray:
    ln_r = log(R)
    d_dlnR = _d_regressor_row_dlnR(ln_r)
    return d_dlnR / R


def _build_design_matrix(ln_r_arr: np.ndarray) -> np.ndarray:
    X = np.zeros((len(ln_r_arr), _N_COEFFS))
    for i, v in enumerate(ln_r_arr):
        X[i] = _regressor_row(v)
    return X


def _fit_steinhart(ln_r_arr: np.ndarray, y_inv: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = _build_design_matrix(ln_r_arr)
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)
    theta = XtX_inv @ X.T @ y_inv
    return theta, XtX_inv, X


def _gum_propagation_steinhart(
    ln_r_arr: np.ndarray,
    R_arr: np.ndarray,
    D_arr: np.ndarray,
    y_inv: np.ndarray,
    T_K_arr: np.ndarray,
    u_D: np.ndarray,
    u_y: np.ndarray,
    r_divider: float = 100000.0,
    adc_max: float = 65535.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    theta, XtX_inv, X = _fit_steinhart(ln_r_arr, y_inv)
    cov_theta = np.zeros((_N_COEFFS, _N_COEFFS))

    for i in range(len(ln_r_arr)):
        x_i = X[i]
        R_i = R_arr[i]
        D_i = D_arr[i]

        dR_dD = r_divider * adc_max / max((adc_max - D_i)**2, 1e-12)
        dx_dD = _d_regressor_row_dR(R_i) * dR_dD

        dtheta_dy_i = XtX_inv @ x_i

        dXty_dxi = dx_dD * y_inv[i]
        dXtX_dxi = np.outer(x_i, dx_dD) + np.outer(dx_dD, x_i)
        dtheta_dxi = XtX_inv @ (dXty_dxi - dXtX_dxi @ theta)

        cov_theta += np.outer(dtheta_dy_i, dtheta_dy_i) * u_y[i]**2
        cov_theta += np.outer(dtheta_dxi,  dtheta_dxi)  * u_D[i]**2 * dR_dD**2

    u_theta = np.sqrt(np.maximum(0.0, np.diag(cov_theta)))
    return theta, u_theta, cov_theta


def steinhart_predict_sh(R: float, theta: np.ndarray) -> float:
    ln_r = log(R)
    inv_T = float(np.dot(_regressor_row(ln_r), theta))
    return 1.0 / inv_T - 273.15 if inv_T != 0.0 else float("nan")


def steinhart_uncertainty(R: float, D: float, u_D_lsb: float,
                          theta: np.ndarray, cov_theta: np.ndarray,
                          r_divider: float = 100000.0,
                          adc_max: float = 65535.0) -> float:
    ln_r = log(R)
    x = _regressor_row(ln_r)
    inv_T = float(np.dot(x, theta))
    if abs(inv_T) < 1e-12:
        return float("inf")
    T_K = 1.0 / inv_T

    dx_dlnR = _d_regressor_row_dlnR(ln_r)
    dx_dR = dx_dlnR / R
    dR_dD = r_divider * adc_max / max((adc_max - D)**2, 1e-12)

    dg_dtheta = x
    dg_dR = float(np.dot(dx_dlnR, theta)) / R

    df_dtheta = -T_K**2 * dg_dtheta
    df_dR = -T_K**2 * dg_dR

    df_dD = df_dR * dR_dD

    u2_coeff = float(df_dtheta @ cov_theta @ df_dtheta)
    u2_sensor = (df_dD * u_D_lsb)**2

    return float(np.sqrt(max(0.0, u2_coeff + u2_sensor)))


def run_prechecks(
    payload: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    verbose: bool = False,
) -> Dict[str, Any]:
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
        msg = f"Steinhart-Hart calibration requires at least {_N_COEFFS} steps (got {len(temp_nominali)})."
        result["errors"].append(msg)
        result["ok"] = False

    if sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "steinhart")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[steinhart unit-check]")
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
    ub_ref_y: float | None = None,
    ub_sensor_lsb: float = 0.0,
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
    ub_pt_lsb: float | None = None,
    formula: str | None = None,
    formula_vars: Dict[str, float] | None = None,
    ufit: float | None = None,
    ufitfromJson: bool = False,
    coverage_factor: float = 2.0,
) -> Dict[str, Any]:
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
    lsb_per_y    = adc_max / (max_v - min_v)

    x_lsb  = np.array([risultati_elaborati[t]["pmean_sensor"] for t in temp_nominali], dtype=float)
    y_phys = np.array([risultati_elaborati[t]["pmean_ref"]    for t in temp_nominali], dtype=float)

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

    # The orchestrator (analisi_calib_data.main) applies the sensor's
    # preprocessingFormula to the raw LSB samples *before* invoking the
    # fit, so x_lsb is already in the model's native X-domain (e.g.
    # resistance in ohm for Steinhart). We just work on x_lsb.
    R_arr = x_lsb.copy()

    # Hardware constants are read from the sensor JSON when present, so
    # the steinhart_uncertainty helper can still propagate the sensor
    # uncertainty through the LSB→R chain (it takes u_D_lsb as input).
    _pp_consts = (sensor_json or {}).get("metrology", {}).get("preprocessingFormulaConstants", {}) or {}
    r_divider = float(_pp_consts.get("rDivider", 100000.0))

    ln_r_arr = np.log(R_arr)
    T_K_arr  = y_phys + 273.15
    y_inv    = 1.0 / T_K_arr

    uc_tmp = np.array([np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + _ub_arr[i]**2) for i, t in enumerate(temp_nominali)], dtype=float)
    uc_pt  = np.array([np.sqrt(risultati_elaborati[t]["pstd_ref"]**2    + ub_ref_y**2)   for t in temp_nominali], dtype=float)

    if verbose:
        print("\n\n --- Fine acquisizione dati (steinhart) ---")

    theta, u_theta, cov_theta = _gum_propagation_steinhart(
        ln_r_arr, R_arr, x_lsb, y_inv, T_K_arr, uc_tmp, uc_pt,
        r_divider=r_divider, adc_max=adc_max,
    )
    a, b, c              = theta
    u_a, u_b, u_c_coeff  = u_theta

    y_pred = np.array([steinhart_predict_sh(float(R_arr[i]), theta) for i in range(len(R_arr))])
    e_fit  = y_phys - y_pred
    N_s    = len(x_lsb)
    rmse   = float(np.sqrt(np.sum(e_fit**2) / max(1, N_s - 3)))

    if verbose:
        print(f"\na={a:.10e} K⁻¹  b={b:.10e} K⁻¹  c={c:.10e} K⁻¹")
        print(f"RMSE (N={N_s}, p=3): {rmse:.6f} {unit_symbol}")

    if all(v is not None for v in [old_a, old_b, old_c]):
        old_theta = np.array([old_a, old_b, old_c], dtype=float)
        y_old     = np.array([steinhart_predict_sh(float(R_arr[i]), old_theta) for i in range(len(R_arr))])
        err_old   = y_old - y_phys
        if verbose:
            print(f"\nBaseline pre-fit mean error: {np.mean(err_old):.6f} {unit_symbol}")

    u_fitting_val = ufit if (ufit is not None and ufit > 0 and ufitfromJson) else rmse

    expanded_uncertainties: List[float] = []
    per_step_budget: List[dict] = []

    for i, t in enumerate(temp_nominali):
        D_i   = float(x_lsb[i])
        R_i   = float(R_arr[i])

        uA_ref    = risultati_elaborati[t]["pstd_ref"]
        uA_sensor = risultati_elaborati[t]["pstd_sensor"]
        uB_sensor = _ub_arr[i]

        u_ref    = np.sqrt(uA_ref**2 + ub_ref_y**2)
        u_meas   = np.sqrt(uA_sensor**2 + uB_sensor**2 + u_res**2)
        u_sensor = np.sqrt(u_meas**2 + u_fitting_val**2)
        u_c      = np.sqrt(u_ref**2 + u_sensor**2)
        U_exp    = coverage_factor * u_c
        u_cal    = steinhart_uncertainty(R_i, D_i, uc_tmp[i], theta, cov_theta,
                                          r_divider=r_divider, adc_max=adc_max)

        expanded_uncertainties.append(float(U_exp))
        per_step_budget.append({
            "t_nominal": t, "uA_ref": uA_ref, "uA_sensor": uA_sensor,
            "uB_ref": ub_ref_y, "uB_sensor": uB_sensor, "u_res": u_res,
            "R_i": R_i, "D_i": D_i,
            "u_meas": u_meas, "u_fitting": u_fitting_val,
            "mu_T_ref": u_ref, "mu_T_i": u_sensor, "mu_E": u_c, "U_E": U_exp,
            "u_cal_poly": u_cal, "U_cal_poly": coverage_factor * u_cal,
        })

    ref_temp_means: List[float] = [
        float(risultati_elaborati[t]["pmean_ref"])
        for t in temp_nominali
    ]

    result: Dict[str, Any] = {
        "model": "steinhart",
        "theta": theta.tolist(),
        "a": float(a), "b": float(b), "c": float(c),
        "u_a": float(u_a), "u_b": float(u_b), "u_c": float(u_c_coeff),
        "cov_theta": cov_theta.tolist(),
        "rmse": rmse,
        "u_fitting": u_fitting_val,
        "old_a": None if old_a is None else float(old_a),
        "old_b": None if old_b is None else float(old_b),
        "old_c": None if old_c is None else float(old_c),
        "temp_nominali": temp_nominali,
        "dati_raw": dati_raw,
        "risultati_elaborati": risultati_elaborati,
        "expanded_uncertainties": expanded_uncertainties,
        "per_step_budget": per_step_budget,
        "ref_temp_means": ref_temp_means,
        "lsb_per_y": lsb_per_y,
        "ub_ref_y": ub_ref_y,
        "ub_sensor_lsb": ub_sensor_lsb,
        "ub_sensor_lsb_per_step": _ub_arr.tolist(),
        "ub_ref_lsb": ub_ref_y * lsb_per_y,
        "R_arr": R_arr.tolist(),
        "ln_R_arr": ln_r_arr.tolist(),
        "T_K_arr": T_K_arr.tolist(),
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
    prefix: str = "calib_stei",
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
    _calib_result: Dict[str, Any] | None = None,
    coverage_factor: float = 2.0,
) -> List[Path]:
    from .calib_plots import bundle_from_steinhart, save_five_charts

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
            "model": "steinhart",
            "theta": theta,
            "a": theta[0], "b": theta[1], "c": theta[2],
            "cov_theta": cov_theta or [[0.0]*3]*3,
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

    bundle = bundle_from_steinhart(
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
    rtd_err = [np.sqrt(risultati_elaborati[t]["pstd_ref"]**2  + ub_ref_lsb**2)    for t in temp_nominali]
    log_err = [np.sqrt(risultati_elaborati[t]["pstd_sensor"]**2 + ub_sensor_lsb**2) for t in temp_nominali]

    R_arr = np.array([log_val_i for log_val_i in log_val])
    t_cal_c = [steinhart_predict_sh(float(R_arr[i]), theta_arr) for i in range(len(R_arr))]
    u_cal_c = [steinhart_uncertainty(float(R_arr[i]), float(log_val[i]), float(log_err[i]),
                                      theta_arr, cov_arr) for i in range(len(R_arr))]
    ref_c   = rtd_val
    residuals = [tc - rc for tc, rc in zip(t_cal_c, ref_c)]

    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Steinhart-Hart Calibration", fontsize=14)
    ax = axs[0]
    ax.set_title("Calibration Curve [°C]", fontsize=12)
    log_val_c = [lsb16_to_phys(np.array([lv]), lsb_scale_sensor_info, adc_max)[0] for lv in log_val]
    ax.errorbar(log_val_c, ref_c, xerr=[e/lsb_per_y for e in log_err], yerr=[e/lsb_per_y for e in rtd_err], fmt="b.", capsize=4, label="PT100 ref")
    ax.errorbar(log_val_c, t_cal_c, yerr=u_cal_c, fmt="r.", capsize=4, label="Steinhart-Hart model")
    for i, (x_i, y_i, t_i) in enumerate(zip(log_val_c, t_cal_c, temp_nominali)):
        ax.annotate(f"{t_i:.0f}", (x_i, y_i),
                    textcoords="offset points", xytext=(4, -8),
                    fontsize=7, alpha=0.7, color="tab:red")
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


def main() -> None:
    import sys
    scripts_dir = Path(__file__).resolve().parent.parent
    calib_root  = scripts_dir.parent
    models_dir  = calib_root / "models_in"

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from calib_utils import _lookup

    sensor_json = json.loads((models_dir / "sensors" / "ntc_temperature_steinhart.json").read_text(encoding="utf-8"))

    adc_bits   = sensor_json.get("ranges", {}).get("elec", {}).get("adcBits", 16)
    adc_max    = float((1 << adc_bits) - 1)
    lsb_min    = float(sensor_json.get("ranges", {}).get("threshold", {}).get("min", -40.0))
    lsb_max    = float(sensor_json.get("ranges", {}).get("threshold", {}).get("max", 105.0))

    ub_ref_y   = 0.0325
    _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    ub_sensor_lsb = float(_lookup(_sensor_ru, "varName", "uB", {}).get("value", 0.30))

    default_input  = calib_root / "test" / "data_in" / "export2_tmp126_lsb16.json"

    parser = argparse.ArgumentParser(description="NTC Steinhart-Hart calibration — GUM OLS, resistance domain")
    parser.add_argument("--input",   type=Path, default=default_input)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()

    payload  = json.loads(args.input.read_text(encoding="utf-8"))
    lsb_info = {"minPhysVal": lsb_min, "maxPhysVal": lsb_max}
    _lsb_per_y = adc_max / (lsb_max - lsb_min) if lsb_max != lsb_min else 452.0
    _risol_lsb = float(_lookup(_sensor_ru, "varName", "resolution", {}).get("value", 1))
    risol = _risol_lsb / _lsb_per_y

    metrology = sensor_json.get("metrology", {})
    pp_formula = metrology.get("preprocessingFormula")
    pp_consts = metrology.get("preprocessingFormulaConstants", {})

    result = calibrate(
        payload=payload, lsb_scale_sensor_info=lsb_info, sample_size=20,
        adc_max=adc_max, ub_ref_y=ub_ref_y, ub_sensor_lsb=ub_sensor_lsb,
        verbose=args.verbose, risol=risol,
        preprocessing_formula=pp_formula,
        preprocessing_vars=dict(pp_consts) if pp_consts else None,
    )

    print("\n=== Calibration result (Steinhart-Hart GUM OLS) ===")
    print(json.dumps({"model": result["model"], "a": result["a"], "b": result["b"],
                      "c": result["c"],
                      "u_a": result["u_a"], "u_b": result["u_b"], "u_c": result["u_c"],
                      "expanded_uncertainties_degC": result["expanded_uncertainties"]}, indent=2))

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
