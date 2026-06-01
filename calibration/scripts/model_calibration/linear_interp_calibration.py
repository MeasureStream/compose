"""Linear interpolation calibration model — first/last node strategy.

When N >= 2 calibration steps are provided the model always uses exactly
**two nodes**: the step with the lowest sensor reading (first) and the step
with the highest sensor reading (last), after sorting by measured sensor mean.

    y_hat(x) = (1 - lambda) * y_node0 + lambda * y_node1
    lambda    = (x - x_node0) / (x_node1 - x_node0)

All N steps (including the two nodes) are then evaluated against this line:
  - At the two node steps the residual is zero by construction.
  - At every interior step the residual is the distance between the measured
    reference value and the linearly interpolated prediction — this is the
    true interpolation error and is the main diagnostic for the model.

GUM uncertainty is propagated at every step from:
  (i)  the two reference node uncertainties, weighted by (1-lambda) and lambda;
  (ii) the sensor reading uncertainty at the evaluation point, scaled by the
       slope dy/dx;
  (iii) optionally the node sensor-position uncertainties (treat_nodes_as_exact).

Result keys
-----------
x_nodes        : [x_node0, x_node1]  (2 elements)
y_nodes        : [y_node0, y_node1]  (2 elements)
node_steps     : [t_first, t_last]   (2 nominal temperatures / step values)
steps          : all N steps sorted by sensor reading
per_step_budget: one entry per step; entry["is_node"] = True for the 2 nodes
residual_degC  : signed error  (y_hat - y_ref); zero at nodes, nonzero interior
rmse_degC      : RMSE computed over the N-2 interior (non-node) steps only;
                 if N==2 there are no interior steps, rmse is NaN.

Public API
----------
calibrate(), build_report(), plot_charts(), save_charts(), run_prechecks()
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from .linear_calibration import (
    _get_data,
    get_scale_from_sensor,
    lsb16_to_phys,
    parse_step,
)

MIN_STEPS_LINEAR_INTERP = 2


# ---------------------------------------------------------------------------
# Core math helpers (unchanged — used by tests too)
# ---------------------------------------------------------------------------

def _find_interval(x: float, x_nodes: np.ndarray) -> int:
    """Return index i such that x_nodes[i] <= x <= x_nodes[i+1].
    Clamps when x is out of range. Nodes must be sorted ascending."""
    n = len(x_nodes)
    if n < 2:
        raise ValueError("At least 2 nodes required for linear interpolation.")
    for i in range(n - 1):
        if x <= x_nodes[i + 1]:
            return i
    return n - 2


def _lambda_weight(x: float, x1: float, x2: float) -> float:
    """Barycentric weight lambda = (x - x1) / (x2 - x1)."""
    denom = x2 - x1
    if np.isclose(denom, 0.0):
        raise ValueError(f"Duplicate adjacent calibration nodes: x1={x1}, x2={x2}.")
    return (x - x1) / denom


def _predict(x: float, x_nodes: np.ndarray, y_nodes: np.ndarray) -> float:
    """Evaluate the (piecewise) linear interpolant at x."""
    i = _find_interval(x, x_nodes)
    lam = _lambda_weight(x, float(x_nodes[i]), float(x_nodes[i + 1]))
    return float((1.0 - lam) * y_nodes[i] + lam * y_nodes[i + 1])


def _predict_degc(
    x_lsb: float,
    x_nodes_lsb: np.ndarray,
    y_nodes_degc: np.ndarray,
    lsb_scale: Dict[str, Any] | None = None,
    adc_max: float | None = None,
) -> float:
    """Predict reference value in °C. y_nodes_degc must already be in °C."""
    return _predict(x_lsb, x_nodes_lsb, y_nodes_degc)


def _sensitivity_dx(x: float, x_nodes: np.ndarray, y_nodes: np.ndarray) -> float:
    """dy_hat/dx = (y2 - y1) / (x2 - x1) for the active interval."""
    i = _find_interval(x, x_nodes)
    x1, x2 = float(x_nodes[i]), float(x_nodes[i + 1])
    y1, y2 = float(y_nodes[i]), float(y_nodes[i + 1])
    denom = x2 - x1
    return 0.0 if np.isclose(denom, 0.0) else (y2 - y1) / denom


def _gum_uncertainty(
    x: float,
    u_x: float,
    x_nodes: np.ndarray,
    y_nodes: np.ndarray,
    u_y_nodes: np.ndarray,
    u_x_nodes: np.ndarray | None = None,
) -> float:
    """GUM combined standard uncertainty of the linearly interpolated value.

    Parameters
    ----------
    x, u_x       : sensor reading and its standard uncertainty
    x_nodes       : node sensor values (sorted ascending)
    y_nodes       : node reference values
    u_y_nodes     : standard uncertainties of y_nodes
    u_x_nodes     : standard uncertainties of x_nodes positions (None = treat as exact)

    Returns uncertainty in the same units as y_nodes.
    """
    i = _find_interval(x, x_nodes)
    x1, x2 = float(x_nodes[i]), float(x_nodes[i + 1])
    y1, y2 = float(y_nodes[i]), float(y_nodes[i + 1])
    u_y1, u_y2 = float(u_y_nodes[i]), float(u_y_nodes[i + 1])
    lam = _lambda_weight(x, x1, x2)
    w1, w2 = 1.0 - lam, lam
    dy_dx = _sensitivity_dx(x, x_nodes, y_nodes)
    dspan = x2 - x1

    u2 = (w1 * u_y1) ** 2 + (w2 * u_y2) ** 2   # from reference node uncertainties
    u2 += (dy_dx * u_x) ** 2                      # from sensor reading uncertainty

    if u_x_nodes is not None:
        u_x1 = float(u_x_nodes[i])
        u_x2 = float(u_x_nodes[i + 1])
        dy_dx1 = -(x - x2) * (y2 - y1) / dspan ** 2
        dy_dx2 =  (x - x1) * (y2 - y1) / dspan ** 2
        u2 += (dy_dx1 * u_x1) ** 2 + (dy_dx2 * u_x2) ** 2

    return float(np.sqrt(max(0.0, u2)))


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
    """Run pre-calibration checks for the linear interpolation model."""
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
    result["steps_ok"] = len(steps) >= MIN_STEPS_LINEAR_INTERP
    if not result["steps_ok"]:
        msg = (
            f"Linear interpolation requires at least {MIN_STEPS_LINEAR_INTERP} "
            f"calibration steps (got {len(steps)})."
        )
        result["errors"].append(msg)
        result["ok"] = False

    if check_units and sensor_json is not None and ref_json is not None:
        from .unit_checks import check_dsi
        uc = check_dsi(sensor_json, ref_json, "linear_interp")
        result["unit_check"] = uc
        if verbose:
            uc.print_report("[linear_interp unit-check]")
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
    treat_nodes_as_exact: bool = True,
    sensor_json: Dict[str, Any] | None = None,
    ref_json: Dict[str, Any] | None = None,
    check_units: bool = False,
    convert_units: bool = False,
) -> Dict[str, Any]:
    """Run linear interpolation calibration using first and last steps as nodes.

    Strategy
    --------
    1. Sort all N steps by measured sensor mean (x).
    2. Use step[0] (smallest x) and step[-1] (largest x) as the two interpolation
       nodes — the line is fully determined.
    3. Evaluate y_hat and GUM uncertainty at *every* step (nodes + interior).
    4. At node steps: residual = 0 by construction.
       At interior steps: residual = y_hat - y_ref  (the real interpolation error).
    5. RMSE is computed over interior steps only (nodes excluded).

    Parameters
    ----------
    treat_nodes_as_exact : bool
        When True (default) the node position uncertainties are not included in the
        GUM budget — equivalent to treating x_node0 and x_node1 as known exactly.
        Set to False to account for their type-A uncertainty too.
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

    # ── Build arrays for all steps, sorted by sensor reading ──────────────
    # pmean_log  : sensor mean [LSB]
    # pmean_rtd  : reference mean [°C]  — already in physical units, not LSB
    # pstd_rtd   : reference std  [°C]
    # ub_pt_lsb  : passed as °C by the orchestrator (ub_pt_degc); name kept for compat
    # ub_tmp_lsb : sensor type-B uncertainty [LSB]
    uB_ref_degC = ub_pt_lsb           # already °C
    uB_i_degC   = ub_tmp_lsb / lsb_per_c
    u_res_degC  = risol_degc / np.sqrt(12.0)

    x_all = np.array([risultati[t]["pmean_log"] for t in steps], dtype=float)   # [LSB]
    y_all = np.array([risultati[t]["pmean_rtd"] for t in steps], dtype=float)   # [°C]
    # Uncertainty of x (sensor reading) [LSB]
    uc_x_all = np.array(
        [np.sqrt(risultati[t]["pstd_log"] ** 2 + ub_tmp_lsb ** 2) for t in steps],
        dtype=float,
    )
    # Uncertainty of y (reference value) [°C]
    uc_y_all = np.array(
        [np.sqrt(risultati[t]["pstd_rtd"] ** 2 + uB_ref_degC ** 2) for t in steps],
        dtype=float,
    )

    sort_idx = np.argsort(x_all)
    x_all     = x_all[sort_idx]
    y_all     = y_all[sort_idx]
    uc_x_all  = uc_x_all[sort_idx]
    uc_y_all  = uc_y_all[sort_idx]
    steps_sorted = [steps[i] for i in sort_idx]

    # ── Two-node line: first and last ──────────────────────────────────────
    x_nodes    = np.array([x_all[0],    x_all[-1]],    dtype=float)   # [LSB]
    y_nodes    = np.array([y_all[0],    y_all[-1]],    dtype=float)   # [°C]
    uc_x_nodes = np.array([uc_x_all[0], uc_x_all[-1]], dtype=float)
    uc_y_nodes = np.array([uc_y_all[0], uc_y_all[-1]], dtype=float)
    node_steps = [steps_sorted[0], steps_sorted[-1]]

    if verbose:
        print("\n--- Linear interpolation (first/last node strategy) ---")
        print(f"  node 0 (first): x={x_nodes[0]:.2f} LSB  y={y_nodes[0]:.4f} °C  (step {node_steps[0]})")
        print(f"  node 1 (last) : x={x_nodes[1]:.2f} LSB  y={y_nodes[1]:.4f} °C  (step {node_steps[1]})")
        if len(steps_sorted) > 2:
            interior = steps_sorted[1:-1]
            print(f"  interior steps (validation only): {interior}")

    u_x_nodes_arg = None if treat_nodes_as_exact else uc_x_nodes

    expanded_uncertainties: List[float] = []
    per_step_budget: List[Dict[str, Any]] = []
    ref_temp_means: List[float] = []
    interior_residuals: List[float] = []

    for idx, t in enumerate(steps_sorted):
        is_node = (idx == 0) or (idx == len(steps_sorted) - 1)

        x_meas   = float(x_all[idx])    # [LSB]
        y_ref    = float(y_all[idx])    # [°C]
        uc_x_meas = float(uc_x_all[idx])

        # y_hat is in °C because y_nodes are in °C
        y_hat        = _predict(x_meas, x_nodes, y_nodes)
        # GUM uncertainty in °C (y domain)
        u_interp_degC = _gum_uncertainty(
            x_meas, uc_x_meas, x_nodes, y_nodes, uc_y_nodes, u_x_nodes_arg
        )

        # Reference type-A: pstd_rtd already in °C
        uA_ref   = float(risultati[t]["pstd_rtd"])
        # Sensor type-A: pstd_log in LSB → °C
        uA_i     = float(risultati[t]["pstd_log"]) / lsb_per_c
        mu_T_ref = float(np.sqrt(uA_ref ** 2 + uB_ref_degC ** 2))
        mu_T_i   = float(np.sqrt(uA_i  ** 2 + uB_i_degC  ** 2 + u_res_degC ** 2))
        mu_E     = float(np.sqrt(mu_T_ref ** 2 + mu_T_i ** 2))
        U_E      = 2.0 * mu_E

        residual_degC = y_hat - y_ref   # [°C], zero at nodes by construction
        lam           = _lambda_weight(x_meas, float(x_nodes[0]), float(x_nodes[1]))

        if not is_node:
            interior_residuals.append(residual_degC)

        expanded_uncertainties.append(float(U_E))
        ref_temp_means.append(y_ref)   # already °C
        per_step_budget.append({
            "t_nominal":       t,
            "is_node":         is_node,
            "lambda":          lam,
            "uA_ref_degC":     uA_ref,
            "uA_i_degC":       uA_i,
            "uB_ref_degC":     uB_ref_degC,
            "uB_i_degC":       uB_i_degC,
            "u_res_degC":      u_res_degC,
            "mu_T_ref":        mu_T_ref,
            "mu_T_i":          mu_T_i,
            "mu_E":            mu_E,
            "U_E":             U_E,
            "u_interp_degC":   u_interp_degC,
            "U_interp_degC":   2.0 * u_interp_degC,
            "residual_degC":   residual_degC,
        })

    # RMSE over interior steps only; NaN when only 2 steps exist
    if interior_residuals:
        rmse_degC = float(np.sqrt(np.mean(np.array(interior_residuals) ** 2)))
    else:
        rmse_degC = float("nan")

    result: Dict[str, Any] = {
        "model":                "linear_interp",
        # interpolation line (2 nodes)
        "x_nodes":              x_nodes.tolist(),
        "y_nodes":              y_nodes.tolist(),
        "uc_x_nodes":           uc_x_nodes.tolist(),
        "uc_y_nodes":           uc_y_nodes.tolist(),
        "node_steps":           node_steps,
        # all steps (sorted by sensor reading)
        "steps":                steps_sorted,
        "dati_raw":             dati_raw,
        "risultati_elaborati":  risultati,
        "expanded_uncertainties": expanded_uncertainties,
        "per_step_budget":      per_step_budget,
        "ref_temp_means":       ref_temp_means,
        # model quality metrics
        "rmse_degC":            rmse_degC,   # interior points only
        "u_H_degC":             rmse_degC,
        "n_interior":           len(interior_residuals),
        # bookkeeping
        "lsb_per_c":            lsb_per_c,
        "ub_pt_lsb":            ub_pt_lsb,
        "ub_tmp_lsb":           ub_tmp_lsb,
        "treat_nodes_as_exact": treat_nodes_as_exact,
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
    lines.append("# Calibration Report — Linear Interpolation (first/last node)")
    lines.append("")
    lines.append("## Interpolation nodes (2 nodes — first and last calibration step)")
    lines.append("| node | role | x [LSB] | y_ref [LSB] |")
    lines.append("|---:|---|---:|---:|")
    roles = ["first (node 0)", "last  (node 1)"]
    for i, (xn, yn, role) in enumerate(zip(x_nodes, y_nodes, roles)):
        lines.append(f"| {i} | {role} | {xn:.2f} | {yn:.2f} |")

    lines.append("")
    lines.append("## Per-step statistics [LSB]")
    lines.append(
        "| step | role | pmean_ref [LSB] | pstd_ref | pmean_sensor [LSB] | pstd_sensor |"
        " max_err | mean_err |"
    )
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|")
    for b, t in zip(per_step_budget, steps):
        r    = risultati_elaborati[t]
        role = "node" if b["is_node"] else "interior"
        lines.append(
            f"| {t:.1f} | {role} | {r['pmean_rtd']:.2f} | {r['pstd_rtd']:.4f} |"
            f" {r['pmean_log']:.2f} | {r['pstd_log']:.4f} |"
            f" {r['max_error']:.4f} | {r['mean_error']:.4f} |"
        )

    lines.append("")
    lines.append("## Uncertainty budget and residuals")
    lines.append(f"- LSB scale: [{min_v}, {max_v}] -> {lsb_per_c:.4f} LSB/unit")
    lines.append(
        "| step | role | lambda | u(y_ref) | u(x) | u(E) | U(E) | u_interp | residual |"
    )
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for b in per_step_budget:
        role = "node" if b["is_node"] else "interior"
        lines.append(
            f"| {b['t_nominal']:.1f} | {role} | {b['lambda']:.4f} |"
            f" {b['mu_T_ref']:.6f} | {b['mu_T_i']:.6f} |"
            f" {b['mu_E']:.6f} | {b['U_E']:.6f} |"
            f" {b['u_interp_degC']:.6f} | {b['residual_degC']:.6f} |"
        )

    lines.append("")
    rmse_str = f"{rmse_degC:.6f}" if not (rmse_degC != rmse_degC) else "N/A (no interior steps)"
    lines.append(f"## Model RMSE (interior steps only): {rmse_str}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _build_plot_data(result, lsb_scale_sensor_info, adc_max):
    """Shared helper for plot_charts / save_charts."""
    x_nodes  = np.array(result["x_nodes"])
    y_nodes  = np.array(result["y_nodes"])
    steps    = result["steps"]
    risultati = result["risultati_elaborati"]
    lsb_per_c = result["lsb_per_c"]
    ub_pt_lsb = result["ub_pt_lsb"]
    ub_tmp_lsb = result["ub_tmp_lsb"]

    # Dense line for plotting
    # x_nodes: [LSB],  y_nodes: [°C]
    x_range = np.linspace(x_nodes[0] * 0.99, x_nodes[1] * 1.01, 500)
    y_hat_range_c = np.array([_predict(float(xr), x_nodes, y_nodes) for xr in x_range])  # [°C]
    x_range_c = lsb16_to_phys(x_range, lsb_scale_sensor_info, adc_max)                   # [°C]

    log_val = [risultati[t]["pmean_log"] for t in steps]           # [LSB]
    rtd_val = [risultati[t]["pmean_rtd"] for t in steps]           # [°C]
    log_err = [np.sqrt(risultati[t]["pstd_log"] ** 2 + ub_tmp_lsb ** 2) for t in steps]  # [LSB]
    rtd_err = [risultati[t]["pstd_rtd"] for t in steps]            # [°C] — no ub_pt here (just scatter)

    ref_c  = rtd_val                                                                       # [°C]
    log_c  = [lsb16_to_phys(np.array([lv]), lsb_scale_sensor_info, adc_max)[0] for lv in log_val]  # [°C]
    pred_c = [_predict_degc(float(lv), x_nodes, y_nodes) for lv in log_val]              # [°C]

    residuals    = [pc - rc for pc, rc in zip(pred_c, ref_c)]
    u_interp_c   = [b["u_interp_degC"] for b in result["per_step_budget"]]
    is_node_mask = [b["is_node"]        for b in result["per_step_budget"]]

    return dict(
        x_range_c=x_range_c, y_hat_range_c=y_hat_range_c,
        log_c=log_c, ref_c=ref_c, pred_c=pred_c,
        log_err=log_err, rtd_err=rtd_err,
        residuals=residuals, u_interp_c=u_interp_c,
        is_node_mask=is_node_mask, steps=steps, lsb_per_c=lsb_per_c,
    )


def _draw_axes(axs, pd, result):
    """Draw calibration curve and residuals axes. pd = plot data dict."""
    ax, ax2 = axs

    ax.set_title("Calibration Curve (linear interpolation — first/last nodes)")
    ax.plot(pd["x_range_c"], pd["y_hat_range_c"], "r-", linewidth=1.2, label="interpolant")

    # Separate node and interior points for distinct markers
    node_idx  = [i for i, n in enumerate(pd["is_node_mask"]) if n]
    inter_idx = [i for i, n in enumerate(pd["is_node_mask"]) if not n]

    for idx_set, fmt, label in [
        (node_idx,  "bs", "nodes (first/last)"),
        (inter_idx, "g^", "interior (validation)"),
    ]:
        if idx_set:
            ax.errorbar(
                [pd["log_c"][i] for i in idx_set],
                [pd["ref_c"][i] for i in idx_set],
                xerr=[pd["log_err"][i] / pd["lsb_per_c"] for i in idx_set],   # LSB -> °C
                yerr=[pd["rtd_err"][i] for i in idx_set],                      # already °C
                fmt=fmt, capsize=4, label=label,
            )

    ax.set_xlabel("Sensor reading [physical unit]")
    ax.set_ylabel("Reference measurand [physical unit]")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2.set_title("Residuals: interpolated − reference\n(nodes = 0 by construction)")
    ax2.axhline(0, color="k", linestyle="--", alpha=0.5)
    for idx_set, fmt, label in [
        (node_idx,  "bs", "nodes"),
        (inter_idx, "ro", "interior"),
    ]:
        if idx_set:
            ax2.errorbar(
                [pd["steps"][i] for i in idx_set],
                [pd["residuals"][i] for i in idx_set],
                yerr=[pd["u_interp_c"][i] for i in idx_set],
                fmt=fmt, capsize=5, label=label,
            )
    rmse = result.get("rmse_degC", float("nan"))
    if rmse == rmse:  # not NaN
        ax2.axhline( rmse, color="orange", linestyle=":", linewidth=1, label=f"+RMSE {rmse:.4f}")
        ax2.axhline(-rmse, color="orange", linestyle=":", linewidth=1, label=f"−RMSE {rmse:.4f}")
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel("Nominal step value")
    ax2.set_ylabel("Residual [physical unit]")
    ax2.legend()


def plot_charts(
    result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")

    pd_  = _build_plot_data(result, lsb_scale_sensor_info, adc_max)
    fig, axs = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Linear Interpolation Calibration (first/last nodes)", fontsize=14)
    _draw_axes(axs, pd_, result)
    plt.tight_layout()
    plt.show()


def save_charts(
    result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    output_dir: Path,
    prefix: str = "calib_linear_interp",
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: float | None = None,
) -> List[Path]:
    from .calib_plots import bundle_from_linear_interp, save_five_charts
    bundle = bundle_from_linear_interp(
        calib_result=result,
        lsb_scale_sensor_info=lsb_scale_sensor_info,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        accuracy_limit=accuracy_limit,
    )
    return save_five_charts(bundle, Path(output_dir), prefix)
