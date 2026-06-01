"""Cubic Lagrange interpolation calibration model.

Uses exactly 4 calibration nodes. The predicted value at a sensor reading x is the
Lagrange cubic interpolant through the 4 (x_node, y_ref_node) pairs. Uncertainty is
propagated analytically via GUM: contributions from the reference node uncertainties
(linear in the Lagrange weights) and from the sensor reading uncertainty (via the
derivative of the interpolant with respect to x).

The model uncertainty (interpolation remainder) is estimated from validation residuals
when calibration data are available.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from .linear_calibration import (
    _get_data,
    get_scale_from_sensor,
    lsb16_to_phys,
    parse_step,
)

MIN_STEPS_CUBIC_INTERP = 4  # Lagrange cubic requires at least 4 steps (2 first + 2 last = nodes)


# ---------------------------------------------------------------------------
# Lagrange basis utilities
# ---------------------------------------------------------------------------

def _lagrange_weights(x: float, x_nodes: np.ndarray) -> np.ndarray:
    """Evaluate the 4 Lagrange basis polynomials at x given nodes x_nodes."""
    n = len(x_nodes)
    w = np.ones(n)
    for i in range(n):
        for j in range(n):
            if j != i:
                denom = x_nodes[i] - x_nodes[j]
                if np.isclose(denom, 0.0):
                    raise ValueError(f"Duplicate calibration nodes at indices {i} and {j}.")
                w[i] *= (x - x_nodes[j]) / denom
    return w


def _lagrange_weights_derivative(x: float, x_nodes: np.ndarray) -> np.ndarray:
    """Derivative of Lagrange basis polynomials w.r.t. x, using the identity
    ell_i'(x) = ell_i(x) * sum_{j != i} 1/(x - x_j).

    Falls back to a finite-difference approximation if x coincides with a node.
    """
    w = _lagrange_weights(x, x_nodes)
    dw = np.zeros_like(w)
    for i in range(len(x_nodes)):
        s = 0.0
        degenerate = False
        for j in range(len(x_nodes)):
            if j != i:
                diff = x - x_nodes[j]
                if np.isclose(diff, 0.0):
                    degenerate = True
                    break
                s += 1.0 / diff
        if degenerate:
            # finite-difference fallback
            eps = max(1.0, abs(x)) * 1e-6
            w_p = _lagrange_weights(x + eps, x_nodes)
            w_m = _lagrange_weights(x - eps, x_nodes)
            dw[i] = (w_p[i] - w_m[i]) / (2.0 * eps)
        else:
            dw[i] = w[i] * s
    return dw


def _predict(x: float, x_nodes: np.ndarray, y_nodes: np.ndarray) -> float:
    """Evaluate the Lagrange cubic interpolant at x."""
    return float(np.dot(_lagrange_weights(x, x_nodes), y_nodes))


def _predict_degc(
    x_lsb: float,
    x_nodes_lsb: np.ndarray,
    y_nodes_degc: np.ndarray,
    lsb_scale: Dict[str, Any] | None = None,
    adc_max: float | None = None,
) -> float:
    """Predict reference value in °C. y_nodes_degc must already be in °C."""
    return _predict(x_lsb, x_nodes_lsb, y_nodes_degc)


def _gum_uncertainty(
    x: float,
    u_x: float,
    x_nodes: np.ndarray,
    y_nodes: np.ndarray,
    u_y_nodes: np.ndarray,
) -> float:
    """GUM combined standard uncertainty of the interpolated value.

    Assumes independent reference nodes.
    Returns uncertainty in the same units as y_nodes / u_y_nodes.
    """
    w = _lagrange_weights(x, x_nodes)
    dw_dx = _lagrange_weights_derivative(x, x_nodes)
    dy_dx = float(np.dot(dw_dx, y_nodes))

    u2_ref = float(np.sum((w * u_y_nodes) ** 2))
    u2_x = (dy_dx * u_x) ** 2
    return float(np.sqrt(max(0.0, u2_ref + u2_x)))


# ---------------------------------------------------------------------------
# Pre-checks
# ---------------------------------------------------------------------------

def run_prechecks(
    payload: Dict[str, Any],
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run pre-calibration checks for the cubic Lagrange interpolation model.

    Returns a dict with keys: ok, steps_ok, n_steps, unit_check, errors, warnings.
    """
    result: Dict[str, Any] = {
        "ok": True,
        "steps_ok": False,
        "n_steps": 0,
        "unit_check": None,
        "errors": [],
        "warnings": [],
    }

    steps = [parse_step(s)[0] for s in payload.get("steps", [])]
    result["n_steps"] = len(steps)
    result["steps_ok"] = len(steps) >= MIN_STEPS_CUBIC_INTERP
    if not result["steps_ok"]:
        msg = (
            f"Cubic Lagrange interpolation requires at least {MIN_STEPS_CUBIC_INTERP} "
            f"calibration steps (got {len(steps)})."
        )
        result["errors"].append(msg)
        result["ok"] = False

    if len(steps) > MIN_STEPS_CUBIC_INTERP:
        result["warnings"].append(
            f"Cubic Lagrange interpolation uses exactly {MIN_STEPS_CUBIC_INTERP} nodes; "
            f"{len(steps) - MIN_STEPS_CUBIC_INTERP} extra step(s) will be used only for "
            "RMSE estimation (not for the interpolant nodes)."
        )

    if check_units and sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "cubic_interp")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[cubic_interp unit-check]")
        result["warnings"].extend(uc.warnings)
        if not uc.ok:
            result["errors"].extend(uc.errors)
            result["ok"] = False

    return result


# ---------------------------------------------------------------------------
# Main calibration entry point
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
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    convert_units: bool = False,
) -> Dict[str, Any]:
    """Run cubic Lagrange interpolation calibration.

    Node strategy: first 2 steps (sorted by sensor reading) + last 2 steps.
    All N steps are evaluated against the cubic interpolant; only the 4 node
    steps have zero residual by construction.  Interior (non-node) steps are
    validation points.  RMSE is computed over all steps.

    Units
    -----
    pmean_log  : sensor mean [LSB]
    pmean_rtd  : reference mean [°C]  — already physical, not LSB-encoded
    ub_pt_lsb  : passed as °C by the orchestrator (ub_pt_degc); name kept for compat
    ub_tmp_lsb : sensor type-B uncertainty [LSB]
    """
    pre = run_prechecks(payload, sensor_json, ref_json, check_units, verbose)
    unit_check_result = pre["unit_check"]
    if not pre["ok"]:
        raise ValueError("\n".join(pre["errors"]))

    steps = [parse_step(s)[0] for s in payload.get("steps", [])]

    dati_raw, risultati = _get_data(
        payload, steps, sample_size, lsb_scale_sensor_info, adc_max, verbose
    )

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    # ── Unit constants ─────────────────────────────────────────────────────
    uB_ref_degC = ub_pt_lsb            # already °C (passed as ub_pt_degc)
    uB_i_degC   = ub_tmp_lsb / lsb_per_c
    u_res_degC  = risol_degc / np.sqrt(12.0)

    # ── Sort all steps by sensor reading (LSB) ────────────────────────────
    x_all = np.array([risultati[t]["pmean_log"] for t in steps], dtype=float)   # [LSB]
    y_all = np.array([risultati[t]["pmean_rtd"] for t in steps], dtype=float)   # [°C]
    uc_x_all = np.array(
        [np.sqrt(risultati[t]["pstd_log"] ** 2 + ub_tmp_lsb ** 2) for t in steps],
        dtype=float,
    )
    uc_y_all = np.array(
        [np.sqrt(risultati[t]["pstd_rtd"] ** 2 + uB_ref_degC ** 2) for t in steps],
        dtype=float,
    )

    sort_idx = np.argsort(x_all)
    x_all    = x_all[sort_idx]
    y_all    = y_all[sort_idx]
    uc_x_all = uc_x_all[sort_idx]
    uc_y_all = uc_y_all[sort_idx]
    steps_sorted = [steps[i] for i in sort_idx]

    # ── Node selection: first 2 + last 2 (sorted by sensor reading) ───────
    n_total = len(steps_sorted)
    node_indices = sorted(set([0, 1, n_total - 2, n_total - 1]))   # dedup in case N==4
    node_steps = [steps_sorted[i] for i in node_indices]
    x_nodes    = x_all[node_indices]    # [LSB]
    y_nodes    = y_all[node_indices]    # [°C]
    uc_x_nodes = uc_x_all[node_indices]
    uc_y_nodes = uc_y_all[node_indices]
    is_node_set = set(node_indices)

    if verbose:
        print("\n--- Cubic Lagrange interpolation (first-2 / last-2 node strategy) ---")
        for i, (ni, t) in enumerate(zip(node_indices, node_steps)):
            print(f"  node {i}: x={x_nodes[i]:.2f} LSB  y={y_nodes[i]:.4f} °C  (step {t})")
        interior = [steps_sorted[i] for i in range(n_total) if i not in is_node_set]
        if interior:
            print(f"  interior (validation): {interior}")

    # ── Per-step predictions, uncertainty, budget ─────────────────────────
    expanded_uncertainties: List[float] = []
    per_step_budget: List[Dict[str, Any]] = []
    ref_temp_means: List[float] = []
    rmse_residuals: List[float] = []

    for idx, t in enumerate(steps_sorted):
        is_node = idx in is_node_set
        x_meas  = float(x_all[idx])      # [LSB]
        y_ref   = float(y_all[idx])      # [°C]
        uc_x    = float(uc_x_all[idx])

        # y_hat in °C (y_nodes are in °C)
        y_hat         = _predict(x_meas, x_nodes, y_nodes)
        # GUM uncertainty in °C
        u_interp_degC = _gum_uncertainty(x_meas, uc_x, x_nodes, y_nodes, uc_y_nodes)

        uA_ref   = float(risultati[t]["pstd_rtd"])        # [°C]
        uA_i     = float(risultati[t]["pstd_log"]) / lsb_per_c  # LSB -> °C
        mu_T_ref = float(np.sqrt(uA_ref ** 2 + uB_ref_degC ** 2))
        mu_T_i   = float(np.sqrt(uA_i  ** 2 + uB_i_degC  ** 2 + u_res_degC ** 2))
        mu_E     = float(np.sqrt(mu_T_ref ** 2 + mu_T_i ** 2))
        U_E      = 2.0 * mu_E

        residual_degC = y_hat - y_ref    # [°C], ~0 at node steps
        rmse_residuals.append(residual_degC)
        expanded_uncertainties.append(float(U_E))
        ref_temp_means.append(y_ref)     # already °C
        per_step_budget.append({
            "t_nominal":     t,
            "is_node":       is_node,
            "uA_ref_degC":   uA_ref,
            "uA_i_degC":     uA_i,
            "uB_ref_degC":   uB_ref_degC,
            "uB_i_degC":     uB_i_degC,
            "u_res_degC":    u_res_degC,
            "mu_T_ref":      mu_T_ref,
            "mu_T_i":        mu_T_i,
            "mu_E":          mu_E,
            "U_E":           U_E,
            "u_interp_degC": u_interp_degC,
            "U_interp_degC": 2.0 * u_interp_degC,
            "residual_degC": residual_degC,
        })

    rmse_degC = float(np.sqrt(np.mean(np.array(rmse_residuals) ** 2)))

    result: Dict[str, Any] = {
        "model":                 "cubic_interp",
        "x_nodes":               x_nodes.tolist(),
        "y_nodes":               y_nodes.tolist(),
        "uc_x_nodes":            uc_x_nodes.tolist(),
        "uc_y_nodes":            uc_y_nodes.tolist(),
        "node_steps":            node_steps,
        "steps":                 steps_sorted,
        "dati_raw":              dati_raw,
        "risultati_elaborati":   risultati,
        "expanded_uncertainties": expanded_uncertainties,
        "per_step_budget":       per_step_budget,
        "ref_temp_means":        ref_temp_means,
        "rmse_degC":             rmse_degC,
        "u_H_degC":              rmse_degC,
        "n_interior":            sum(1 for b in per_step_budget if not b["is_node"]),
        "lsb_per_c":             lsb_per_c,
        "ub_pt_lsb":             ub_pt_lsb,
        "ub_tmp_lsb":            ub_tmp_lsb,
    }
    if unit_check_result is not None:
        result["unit_check"] = unit_check_result
    if convert_units and sensor_json is not None and ref_json is not None:
        from .unit_checks import convert_result
        result = convert_result(result, sensor_json, ref_json)
    return result


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    steps: List[float],
    risultati_elaborati: Dict[float, Dict[str, Any]],
    x_nodes: List[float],
    y_nodes: List[float],
    per_step_budget: List[Dict[str, Any]],
    expanded_uncertainties: List[float],
    rmse_degC: float,
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    ub_pt_lsb: float,
    ub_tmp_lsb: float,
) -> str:
    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    lines: List[str] = []
    lines.append("# Calibration Report — Cubic Lagrange Interpolation")
    lines.append("")
    lines.append(f"## Interpolation nodes ({MIN_STEPS_CUBIC_INTERP} nodes)")
    lines.append("| node | x [LSB] | y_ref [LSB] |")
    lines.append("|---:|---:|---:|")
    for i, (xn, yn) in enumerate(zip(x_nodes, y_nodes)):
        lines.append(f"| {i} | {xn:.2f} | {yn:.2f} |")

    lines.append("")
    lines.append("## Per-step statistics [LSB]")
    lines.append(
        "| step | target | pmean_ref [LSB] | pstd_ref | pmean_sensor [LSB] | pstd_sensor |"
        " max_error | mean_error |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t in steps:
        r = risultati_elaborati[t]
        lines.append(
            f"| {t:.1f} | {t:.3f} | {r['pmean_rtd']:.2f} | {r['pstd_rtd']:.4f} |"
            f" {r['pmean_log']:.2f} | {r['pstd_log']:.4f} |"
            f" {r['max_error']:.4f} | {r['mean_error']:.4f} |"
        )

    lines.append("")
    lines.append("## Uncertainty budget U(E) [°C, k=2]")
    lines.append(f"- LSB scale: [{min_v}, {max_v}] -> {lsb_per_c:.4f} LSB/unit")
    lines.append(
        "| step | u(y_ref) [°C] | u(x) [°C] | u(E) [°C] | U(E) [°C] |"
        " u_interp [°C] | residual [°C] |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for b in per_step_budget:
        lines.append(
            f"| {b['t_nominal']:.1f} | {b['mu_T_ref']:.6f} | {b['mu_T_i']:.6f} |"
            f" {b['mu_E']:.6f} | {b['U_E']:.6f} |"
            f" {b['u_interp_degC']:.6f} | {b['residual_degC']:.6f} |"
        )

    lines.append("")
    lines.append(f"## Model RMSE (interpolation remainder estimate): {rmse_degC:.6f} °C")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _build_plot_data(
    result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
) -> Dict[str, Any]:
    """Shared data preparation for plot_charts and save_charts."""
    x_nodes    = np.array(result["x_nodes"])
    y_nodes    = np.array(result["y_nodes"])
    steps      = result["steps"]
    risultati  = result["risultati_elaborati"]
    lsb_per_c  = result["lsb_per_c"]
    ub_tmp_lsb = result["ub_tmp_lsb"]

    x_range = np.linspace(x_nodes.min() * 0.99, x_nodes.max() * 1.01, 300)
    y_hat_range_c = np.array([_predict(float(xr), x_nodes, y_nodes) for xr in x_range])
    x_range_c = lsb16_to_phys(x_range, lsb_scale_sensor_info, adc_max)

    log_val = [risultati[t]["pmean_log"] for t in steps]
    rtd_val = [risultati[t]["pmean_rtd"] for t in steps]
    log_err = [np.sqrt(risultati[t]["pstd_log"] ** 2 + ub_tmp_lsb ** 2) for t in steps]
    rtd_err = [risultati[t]["pstd_rtd"] for t in steps]

    ref_c      = rtd_val
    log_c      = [lsb16_to_phys(np.array([lv]), lsb_scale_sensor_info, adc_max)[0] for lv in log_val]
    pred_c     = [_predict_degc(float(lv), x_nodes, y_nodes) for lv in log_val]
    residuals  = [pc - rc for pc, rc in zip(pred_c, ref_c)]
    u_interp_c = [b["u_interp_degC"] for b in result["per_step_budget"]]
    is_node    = [b.get("is_node", False) for b in result["per_step_budget"]]

    return dict(
        x_range_c=x_range_c, y_hat_range_c=y_hat_range_c,
        log_c=log_c, ref_c=ref_c, pred_c=pred_c,
        log_err=log_err, rtd_err=rtd_err,
        residuals=residuals, u_interp_c=u_interp_c,
        is_node=is_node, steps=steps, lsb_per_c=lsb_per_c,
    )


def _draw_on_axes(axs, pd: Dict[str, Any]) -> None:
    """Draw calibration curve + residuals onto the given pair of axes."""
    ax, ax2 = axs
    node_idx  = [i for i, n in enumerate(pd["is_node"]) if n]
    inter_idx = [i for i, n in enumerate(pd["is_node"]) if not n]
    lpc = pd["lsb_per_c"]

    ax.set_title("Calibration Curve (cubic Lagrange — first-2/last-2 nodes)")
    ax.plot(pd["x_range_c"], pd["y_hat_range_c"], "r-", linewidth=1.2, label="cubic interpolant")
    for idx_set, fmt, lbl in [(node_idx, "bs", "nodes"), (inter_idx, "g^", "interior")]:
        if idx_set:
            ax.errorbar(
                [pd["log_c"][i] for i in idx_set],
                [pd["ref_c"][i] for i in idx_set],
                xerr=[pd["log_err"][i] / lpc for i in idx_set],
                yerr=[pd["rtd_err"][i] for i in idx_set],
                fmt=fmt, capsize=4, label=lbl,
            )
    ax.set_xlabel("Sensor reading [°C equivalent]")
    ax.set_ylabel("Reference measurand [°C]")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2.set_title("Residuals (interpolated − reference)")
    ax2.axhline(0, color="k", linestyle="--", alpha=0.5)
    for idx_set, fmt, lbl in [(node_idx, "bs", "nodes"), (inter_idx, "ro", "interior")]:
        if idx_set:
            ax2.errorbar(
                [pd["steps"][i] for i in idx_set],
                [pd["residuals"][i] for i in idx_set],
                yerr=[pd["u_interp_c"][i] for i in idx_set],
                fmt=fmt, capsize=5, label=lbl,
            )
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel("Nominal step value")
    ax2.set_ylabel("Residual [°C]")
    ax2.legend()


def plot_charts(
    result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")

    pd = _build_plot_data(result, lsb_scale_sensor_info, adc_max)

    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Cubic Lagrange Interpolation Calibration", fontsize=14)
    _draw_on_axes(axs, pd)
    plt.tight_layout()
    plt.show()


def save_charts(
    result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    output_dir: Path,
    prefix: str = "calib_cubic_interp",
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
) -> List[Path]:
    from .calib_plots import bundle_from_cubic_interp, save_five_charts
    bundle = bundle_from_cubic_interp(
        calib_result=result,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        accuracy_limit=accuracy_limit,
    )
    return save_five_charts(bundle, Path(output_dir), prefix)
