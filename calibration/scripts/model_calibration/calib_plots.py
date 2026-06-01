"""calib_plots.py — unified calibration chart generator.

Produces exactly 5 standardised PNG figures for any calibration model,
at 600 dpi (4× the legacy 150 dpi) so that images are fully zoomable.

Every error bar in every figure uses the **real per-point GUM combined
standard uncertainty** extracted from the calibration budget, not a single
scalar type-B value.  Specifically:

  u_ref_degc[i]     = mu_T_ref[i] = sqrt(uA_ref[i]² + uB_ref²)   [°C]
  u_sensor_lsb[i]   = sqrt(uA_i_lsb[i]² + uB_sensor_lsb²)        [LSB]
  u_sensor_degc[i]  = mu_T_i[i]  = sqrt(uA_i[i]² + uB_i² + u_res²) [°C]
  u_E[i]            = U_E[i] = 2·mu_E[i] = 2·sqrt(u_ref²+u_sensor²) [°C]

These are carried as per-point lists in PlotBundle and used consistently
in every figure.

Figures
-------
fig1  Sample block-mean time-series per step.
      Reference bands = ±mu_T_ref[i] (combined ref uncertainty).
      Sensor bands    = ±u_sensor_lsb[i] (combined sensor uncertainty in LSB).

fig2  Raw scatter (pre-calibration).
      X error bars: ±u_sensor_lsb[i] [LSB].
      Y error bars: ±mu_T_ref[i] [°C].

fig3  Calibration curve.
      Reference point error bars: x = ±u_sensor_lsb[i], y = ±mu_T_ref[i].
      Calibrated prediction markers (no extra bars — residual shown in fig5).

fig4  Pre-calibration error (as-found) with ±U_E[i] bars.

fig5  Post-calibration residuals (as-left) with ±U_E[i] bars.
      Distinguishes node steps from interior validation steps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

DPI       = 600
FIG_W_1x2 = 24.0
FIG_H_1x2 = 10.0
FIG_W_2x3 = 24.0
FIG_H_2x3 = 14.0


# ---------------------------------------------------------------------------
# PlotBundle dataclass
# ---------------------------------------------------------------------------

@dataclass
class PlotBundle:
    """Model-agnostic container for all data needed to draw the five charts.

    All uncertainty lists are per-point combined standard uncertainties
    derived from the GUM budget stored in the calibration result dict.

    steps           : nominal step values, sorted by sensor reading
    ref_means       : mean reference value per step [°C]
    sensor_means    : mean sensor reading per step [LSB]
    u_ref_degc      : combined std unc of reference per step [°C]  = mu_T_ref
    u_sensor_lsb    : combined std unc of sensor per step [LSB]
    u_sensor_degc   : combined std unc of sensor per step [°C]     = mu_T_i
    u_E             : expanded uncertainty U(E) = 2·mu_E per step [°C]
    me_pre          : pre-calibration signed error per step [°C]
    me_post         : post-calibration residual per step [°C]
    t_sensor_pre    : raw sensor reading converted to [°C] per step
    t_sensor_post   : calibrated sensor prediction per step [°C]
    model_x_lsb     : dense sensor grid for model curve [LSB]
    model_y_degc    : model output on dense grid [°C]
    lsb_per_c       : LSB/°C conversion factor
    lsb_min, lsb_max: physical range endpoints [°C]
    adc_max         : maximum ADC count
    unit_symbol     : physical unit symbol for axis labels (e.g. "°C")
    sensor_label    : display name of sensor under test
    ref_label       : display name of reference instrument
    model_label     : short model description for titles
    is_node         : per-step flag — True for interpolation nodes
    sample_data     : risultati_elaborati dict for fig1 (block-mean time series)
    sample_size     : block size shown in fig1 title
    accuracy_limit  : sensor declared accuracy [°C] for limit bands (optional)
    """
    steps:          List[float]
    ref_means:      List[float]        # [°C]
    sensor_means:   List[float]        # [LSB]
    u_ref_degc:     List[float]        # [°C]  per-point combined ref unc
    u_sensor_lsb:   List[float]        # [LSB] per-point combined sensor unc
    u_sensor_degc:  List[float]        # [°C]  per-point combined sensor unc
    u_E:            List[float]        # [°C]  expanded uncertainty
    me_pre:         List[float]        # [°C]
    me_post:        List[float]        # [°C]
    t_sensor_pre:   List[float]        # [°C]
    t_sensor_post:  List[float]        # [°C]
    model_x_lsb:    List[float]
    model_y_degc:   List[float]
    lsb_per_c:      float
    lsb_min:        float
    lsb_max:        float
    adc_max:        float
    unit_symbol:    str = "°C"
    sensor_label:   str = "Sensor"
    ref_label:      str = "Reference"
    model_label:    str = "Calibration model"
    is_node:        Optional[List[bool]] = None
    sample_data:    Optional[Dict[float, Any]] = None
    sample_size:    int = 20
    accuracy_limit: Optional[float] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _phys_label(unit: str) -> str:
    if unit in ("°C", "K", "°F"):
        return f"Temperature [{unit}]"
    return f"Measurand [{unit}]"


def _add_sensor_secondary_axis(ax, lsb_min: float, lsb_per_c: float, unit: str,
                                position: str = "top"):
    ax2 = ax.secondary_xaxis(
        position,
        functions=(
            lambda lsb: lsb_min + lsb / lsb_per_c,
            lambda phys: (phys - lsb_min) * lsb_per_c,
        ),
    )
    ax2.set_xlabel(f"Sensor reading [{unit}]", fontsize=9)
    ax2.tick_params(labelsize=8)
    return ax2


def _step_color(is_node: Optional[List[bool]], idx: int) -> str:
    if is_node is None:
        return "tab:red"
    return "tab:blue" if is_node[idx] else "tab:orange"


def _step_marker(is_node: Optional[List[bool]], idx: int) -> str:
    if is_node is None:
        return "o"
    return "s" if is_node[idx] else "^"


def _annotate(ax, x_vals, y_vals, steps, fontsize=7):
    for xi, yi, t in zip(x_vals, y_vals, steps):
        ax.annotate(f"{t:.0f}", (xi, yi),
                    textcoords="offset points", xytext=(4, 4),
                    fontsize=fontsize, alpha=0.75)


# ---------------------------------------------------------------------------
# Figure 1 — Sample block-mean time-series per step
# ---------------------------------------------------------------------------

def _local_sens_lsb_per_unit(bundle: PlotBundle) -> List[float]:
    """Per-step local sensitivity dLSB/d°C estimated by central finite differences
    on the calibration step means.

    Uses (sensor[i+1] - sensor[i-1]) / (ref[i+1] - ref[i-1]) for interior steps,
    and the one-sided difference for the two endpoints.

    This is the physically correct scale factor for aligning the LSB axis with
    the °C axis at each individual step — it accounts for sensor nonlinearity.
    Falls back to the global lsb_per_c only when fewer than 2 steps are available.
    """
    sm = bundle.sensor_means  # [LSB]
    rm = bundle.ref_means     # [°C]
    n  = len(sm)
    if n < 2:
        return [bundle.lsb_per_c] * n

    sens = []
    for i in range(n):
        if i == 0:
            i1, i2 = 0, 1
        elif i == n - 1:
            i1, i2 = n - 2, n - 1
        else:
            i1, i2 = i - 1, i + 1
        dT = rm[i2] - rm[i1]
        dL = sm[i2] - sm[i1]
        # Guard against degenerate steps (same ref value)
        if abs(dT) > 1e-9:
            sens.append(abs(dL / dT))
        else:
            sens.append(bundle.lsb_per_c)
    return sens


def _fig1_sample_timeseries(bundle: PlotBundle, plt):
    """Per-step block-mean time-series.

    Both axes span the same physical window [°C]:
      - Left axis  : reference [°C]
      - Right axis : sensor [LSB]  with tick labels showing BOTH LSB and °C
        (e.g. "15982 LSB  /  0.26 °C")

    The °C equivalent on the sensor tick labels is computed using the
    LOCAL sensitivity dLSB/d°C at that step (central finite differences),
    not the global affine lsb_per_c.

    Scale alignment: half-span in °C = max(ref_halfspan, sensor_halfspan)
    where sensor_halfspan is derived via the local sensitivity, then
    converted back to LSB for the sensor axis limits.
    """
    if not bundle.sample_data:
        return None
    steps = [t for t in bundle.steps if t in bundle.sample_data]
    if not steps:
        return None

    from matplotlib.ticker import FuncFormatter

    step_to_idx  = {t: i for i, t in enumerate(bundle.steps)}
    local_sens   = _local_sens_lsb_per_unit(bundle)  # [LSB/°C] per step
    # For tick label formatting we need a per-step reference point (ref_mean[i])
    # and local_sens[i] so we can write:  T ≈ ref_mean[i] + (LSB - sen_mean[i]) / local_sens[i]

    ncols = min(3, len(steps))
    nrows = math.ceil(len(steps) / ncols)
    fig, axs = plt.subplots(nrows, ncols,
                             figsize=(FIG_W_2x3, max(FIG_H_2x3 * nrows / 2, 8)),
                             dpi=DPI, squeeze=False)
    fig.suptitle(
        f"Sample block-means per calibration step  (block size = {bundle.sample_size})\n"
        f"Left axis: {bundle.ref_label} [{bundle.unit_symbol}]  |  "
        f"Right axis: {bundle.sensor_label} [LSB  /  {bundle.unit_symbol} (local sensitivity)]\n"
        f"Shaded bands = combined standard uncertainty u_c (type-A + type-B, per point)\n"
        f"Both axes share the same physical window width [±{bundle.unit_symbol}]",
        fontsize=10, y=1.02,
    )

    for idx, t in enumerate(steps):
        row, col = divmod(idx, ncols)
        ax  = axs[row][col]
        sd  = bundle.sample_data[t]
        x   = sd["x_axis"]

        bi             = step_to_idx.get(t, idx)
        u_ref_i        = bundle.u_ref_degc[bi]    # [°C]
        u_sensor_lsb_i = bundle.u_sensor_lsb[bi]  # [LSB]
        sens_i         = local_sens[bi]            # [LSB/°C] — local at this step

        smean_rtd = np.asarray(sd["smean_rtd"])   # [°C]
        smean_log = np.asarray(sd["smean_log"])   # [LSB]

        # ── Shared physical half-span ──────────────────────────────────────
        # Both spans expressed in °C using the LOCAL sensitivity for the sensor.
        # No global lsb_per_c used here.
        ref_pp    = smean_rtd.max() - smean_rtd.min()   # [°C]
        ref_half  = ref_pp / 2.0 + u_ref_i              # [°C]

        sen_pp_lsb  = smean_log.max() - smean_log.min() # [LSB]
        # Convert to °C using local sensitivity (LSB/°C → °C = LSB / sens_i)
        sen_half_degc = sen_pp_lsb / (2.0 * sens_i) + u_sensor_lsb_i / sens_i  # [°C]

        # Winning window: same physical width on both axes, 20 % margin
        half_degc = max(ref_half, sen_half_degc, 1e-9) * 1.2   # [°C]
        half_lsb  = half_degc * sens_i                          # [LSB] for sensor axis

        ref_centre     = (smean_rtd.max() + smean_rtd.min()) / 2.0   # [°C]
        sen_centre_lsb = (smean_log.max() + smean_log.min()) / 2.0   # [LSB]
        # Reference °C value at the sensor centre (for tick label offset)
        ref_at_sen_centre = bundle.ref_means[bi]  # [°C]

        # ── Left axis — reference [°C] ─────────────────────────────────────
        ax.plot(x, smean_rtd, "b-o", linewidth=0.8, markersize=2,
                label=f"{bundle.ref_label} [{bundle.unit_symbol}]")
        ax.fill_between(x, smean_rtd - u_ref_i, smean_rtd + u_ref_i,
                        alpha=0.18, color="tab:blue")
        ax.set_ylim(ref_centre - half_degc, ref_centre + half_degc)
        ax.set_ylabel(f"Ref [{bundle.unit_symbol}]", fontsize=8, color="tab:blue")
        ax.tick_params(axis="y", labelcolor="tab:blue", labelsize=7)

        # ── Right axis — sensor [LSB] with °C labels ───────────────────────
        ax2 = ax.twinx()
        ax2.plot(x, smean_log, "r-o", linewidth=0.8, markersize=2,
                 label=f"{bundle.sensor_label} [LSB]")
        ax2.fill_between(x, smean_log - u_sensor_lsb_i, smean_log + u_sensor_lsb_i,
                         alpha=0.12, color="tab:red")
        ax2.set_ylim(sen_centre_lsb - half_lsb, sen_centre_lsb + half_lsb)

        # Tick labels: "NNNNN LSB  /  XX.XX °C"
        # °C approximated as: ref_at_sen_centre + (v - sen_centre_lsb) / sens_i
        _ref0  = ref_at_sen_centre
        _sen0  = sen_centre_lsb
        _sens  = sens_i
        _unit  = bundle.unit_symbol
        ax2.yaxis.set_major_formatter(FuncFormatter(
            lambda v, _, r0=_ref0, s0=_sen0, si=_sens, u=_unit:
                f"{v:.0f} LSB\n{r0 + (v - s0) / si:.3f} {u}"
        ))
        ax2.set_ylabel(f"Sensor [LSB  |  {bundle.unit_symbol}]", fontsize=8, color="tab:red")
        ax2.tick_params(axis="y", labelcolor="tab:red", labelsize=7)

        winner = "ref" if ref_half >= sen_half_degc else "sensor"
        u_sen_degc_i = u_sensor_lsb_i / sens_i
        ax.set_title(
            f"Step {t:.1f} {bundle.unit_symbol}  —  "
            f"local sens = {sens_i:.1f} LSB/{bundle.unit_symbol}\n"
            f"u_ref={u_ref_i:.4f} {bundle.unit_symbol}   "
            f"u_sen={u_sensor_lsb_i:.2f} LSB = {u_sen_degc_i:.4f} {bundle.unit_symbol}\n"
            f"window ±{half_degc:.4f} {bundle.unit_symbol}  (driven by {winner})",
            fontsize=7,
        )
        ax.set_xlabel("Block index", fontsize=8)
        ax.grid(True, alpha=0.25)
        if idx == 0:
            ax.legend(fontsize=7, loc="upper left")
            ax2.legend(fontsize=7, loc="upper right")

    for idx in range(len(steps), nrows * ncols):
        row, col = divmod(idx, ncols)
        axs[row][col].set_visible(False)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Figure 2 — Raw scatter (pre-calibration)
# ---------------------------------------------------------------------------

def _fig2_raw_scatter(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"Raw scatter — pre-calibration\n"
        f"X: {bundle.sensor_label} [LSB / {bundle.unit_symbol}]"
        f"   Y: {bundle.ref_label} [{bundle.unit_symbol}]\n"
        f"Error bars = combined std unc u_c per point (type-A + type-B)",
        fontsize=10,
    )

    x_lsb  = np.array(bundle.sensor_means)
    y_ref  = np.array(bundle.ref_means)
    u_x    = np.array(bundle.u_sensor_lsb)   # [LSB] per-point GUM
    u_y    = np.array(bundle.u_ref_degc)      # [°C]  per-point GUM

    for i, (xi, yi, uxi, uyi, t) in enumerate(zip(x_lsb, y_ref, u_x, u_y, bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        ax.errorbar(xi, yi, xerr=uxi, yerr=uyi,
                    fmt=marker, color=color, ecolor=color,
                    capsize=4, markersize=7, linewidth=1.0)
        ax.annotate(f"{t:.0f}", (xi, yi),
                    textcoords="offset points", xytext=(5, 3), fontsize=7, alpha=0.8)

    # Identity conversion line
    x_line = np.linspace(x_lsb.min() * 0.98, x_lsb.max() * 1.02, 300)
    ax.plot(x_line, bundle.lsb_min + x_line / bundle.lsb_per_c,
            "k--", linewidth=0.9, alpha=0.45, label="Identity (LSB→°C)")

    ax.set_xlabel(f"{bundle.sensor_label} mean [LSB]", fontsize=10)
    ax.set_ylabel(f"{bundle.ref_label} mean [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    _add_sensor_secondary_axis(ax, bundle.lsb_min, bundle.lsb_per_c, bundle.unit_symbol)

    if bundle.is_node is not None:
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Patch(facecolor="tab:blue",   label="Interpolation node"),
            Patch(facecolor="tab:orange", label="Interior (validation)"),
        ], fontsize=8)
    else:
        ax.legend(fontsize=8)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Figure 3 — Calibration curve
# ---------------------------------------------------------------------------

def _fig3_calibration_curve(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"Calibration curve — {bundle.model_label}\n"
        f"X: {bundle.sensor_label} [LSB / {bundle.unit_symbol}]"
        f"   Y: {bundle.ref_label} [{bundle.unit_symbol}]\n"
        f"Reference error bars = u_c per point (type-A + type-B)",
        fontsize=10,
    )

    # Dense model curve
    ax.plot(bundle.model_x_lsb, bundle.model_y_degc,
            "r-", linewidth=1.4, zorder=3, label=f"Model: {bundle.model_label}")

    # Identity line
    x_line = np.linspace(min(bundle.model_x_lsb) * 0.98, max(bundle.model_x_lsb) * 1.02, 300)
    ax.plot(x_line, bundle.lsb_min + np.array(x_line) / bundle.lsb_per_c,
            "k--", linewidth=0.8, alpha=0.45, label="Identity (LSB→°C)")

    x_lsb = np.array(bundle.sensor_means)
    y_ref  = np.array(bundle.ref_means)
    u_x    = np.array(bundle.u_sensor_lsb)  # [LSB]
    u_y    = np.array(bundle.u_ref_degc)    # [°C]

    # Reference points with per-point error bars
    ax.errorbar(x_lsb, y_ref, xerr=u_x, yerr=u_y,
                fmt="b.", capsize=4, markersize=8, zorder=4,
                label=f"Reference ± u_c")

    # Calibrated predictions (colour by node/interior)
    for i in range(len(bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        ax.plot(x_lsb[i], bundle.t_sensor_post[i],
                marker=marker, color=color, markersize=7, zorder=5,
                linestyle="none")

    ax.plot([], [], marker="o", color="tab:red", linestyle="none",
            markersize=7, label="Calibrated prediction")

    _annotate(ax, x_lsb, y_ref, bundle.steps)

    ax.set_xlabel(f"{bundle.sensor_label} reading [LSB]", fontsize=10)
    ax.set_ylabel(_phys_label(bundle.unit_symbol), fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    _add_sensor_secondary_axis(ax, bundle.lsb_min, bundle.lsb_per_c, bundle.unit_symbol)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Figure 4 — Pre-calibration error (as-found)
# ---------------------------------------------------------------------------

def _fig4_pre_error(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"Pre-calibration error (as-found)\n"
        f"M_e_pre = {bundle.sensor_label} (raw) − {bundle.ref_label}  [{bundle.unit_symbol}]\n"
        f"Error bars = expanded uncertainty U(E) = 2·u_c per point",
        fontsize=10,
    )

    x      = np.array(bundle.ref_means)
    me_pre = np.array(bundle.me_pre)
    u_E    = np.array(bundle.u_E)          # per-point U(E) = 2·mu_E

    ax.axhline(0, color="k", linewidth=0.9, linestyle="--", alpha=0.6)

    for i, (xi, ei, ui, t) in enumerate(zip(x, me_pre, u_E, bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        ax.errorbar(xi, ei, yerr=ui,
                    fmt=marker, color=color, ecolor=color,
                    capsize=5, markersize=8, linewidth=1.2)
        ax.annotate(
            f"{t:.0f}\n(U={ui:.3f})",
            (xi, ei), textcoords="offset points", xytext=(5, 4),
            fontsize=6, alpha=0.8,
        )

    if len(me_pre) > 0:
        rmse = float(np.sqrt(np.mean(me_pre ** 2)))
        ax.axhline( rmse, color="tab:orange", linewidth=1.0, linestyle=":",
                    label=f"RMSE = {rmse:.4f} {bundle.unit_symbol}")
        ax.axhline(-rmse, color="tab:orange", linewidth=1.0, linestyle=":")

    if bundle.accuracy_limit is not None:
        lim = bundle.accuracy_limit
        ax.axhspan(-lim, lim, alpha=0.07, color="green")
        ax.axhline( lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6,
                    label=f"Accuracy limit ±{lim:.3f} {bundle.unit_symbol}")
        ax.axhline(-lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6)

    ax.set_xlabel(f"{bundle.ref_label} [{bundle.unit_symbol}]", fontsize=10)
    ax.set_ylabel(f"M_e_pre [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Figure 5 — Post-calibration residuals (as-left)
# ---------------------------------------------------------------------------

def _fig5_post_residuals(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"Post-calibration residuals (as-left)\n"
        f"M_e_post = {bundle.sensor_label} (calibrated) − {bundle.ref_label}  [{bundle.unit_symbol}]\n"
        f"Error bars = expanded uncertainty U(E) = 2·u_c per point",
        fontsize=10,
    )

    x       = np.array(bundle.ref_means)
    me_post = np.array(bundle.me_post)
    u_E     = np.array(bundle.u_E)

    ax.axhline(0, color="k", linewidth=0.9, linestyle="--", alpha=0.6)

    for i, (xi, ei, ui, t) in enumerate(zip(x, me_post, u_E, bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        ax.errorbar(xi, ei, yerr=ui,
                    fmt=marker, color=color, ecolor=color,
                    capsize=5, markersize=8, linewidth=1.2)
        ax.annotate(
            f"{t:.0f}\n(U={ui:.3f})",
            (xi, ei), textcoords="offset points", xytext=(5, 4),
            fontsize=6, alpha=0.8,
        )

    # RMSE over interior steps only for interpolation models
    interior_mask = np.ones(len(me_post), dtype=bool)
    if bundle.is_node is not None:
        interior_mask = np.array([not n for n in bundle.is_node])
    interior_res = me_post[interior_mask]

    if len(interior_res) > 0:
        rmse = float(np.sqrt(np.mean(interior_res ** 2)))
        suffix = " (interior)" if bundle.is_node is not None else ""
        ax.axhline( rmse, color="tab:purple", linewidth=1.0, linestyle=":",
                    label=f"RMSE{suffix} = {rmse:.4f} {bundle.unit_symbol}")
        ax.axhline(-rmse, color="tab:purple", linewidth=1.0, linestyle=":")
    elif len(me_post) > 0:
        rmse = float(np.sqrt(np.mean(me_post ** 2)))
        ax.axhline( rmse, color="tab:purple", linewidth=1.0, linestyle=":",
                    label=f"RMSE = {rmse:.4f} {bundle.unit_symbol}")
        ax.axhline(-rmse, color="tab:purple", linewidth=1.0, linestyle=":")

    if bundle.accuracy_limit is not None:
        lim = bundle.accuracy_limit
        ax.axhspan(-lim, lim, alpha=0.07, color="green")
        ax.axhline( lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6,
                    label=f"Accuracy limit ±{lim:.3f} {bundle.unit_symbol}")
        ax.axhline(-lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6)

    if bundle.is_node is not None:
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Patch(facecolor="tab:blue",   label="Node (residual = 0 by construction)"),
            Patch(facecolor="tab:orange", label="Interior (true interpolation error)"),
        ], fontsize=8)
    else:
        ax.legend(fontsize=8)

    ax.set_xlabel(f"{bundle.ref_label} [{bundle.unit_symbol}]", fontsize=10)
    ax.set_ylabel(f"M_e_post [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def save_five_charts(bundle: PlotBundle, output_dir: Path, prefix: str) -> List[Path]:
    """Generate and save all five standard calibration charts at 600 dpi."""
    import importlib
    import matplotlib
    matplotlib.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    for draw_fn, suffix in [
        (_fig1_sample_timeseries, "fig1_sample_timeseries"),
        (_fig2_raw_scatter,       "fig2_raw_scatter"),
        (_fig3_calibration_curve, "fig3_calibration_curve"),
        (_fig4_pre_error,         "fig4_pre_error"),
        (_fig5_post_residuals,    "fig5_post_residuals"),
    ]:
        try:
            fig = draw_fn(bundle, plt)
            if fig is None:
                continue
            p = output_dir / f"{prefix}_{suffix}.png"
            fig.savefig(p, dpi=DPI, bbox_inches="tight")
            plt.close(fig)
            saved.append(p)
        except Exception as exc:
            import warnings
            warnings.warn(f"calib_plots: {suffix} skipped — {exc}", stacklevel=2)
            try:
                plt.close("all")
            except Exception:
                pass

    return saved


# ---------------------------------------------------------------------------
# Internal: extract per-point GUM uncertainties from budget
# ---------------------------------------------------------------------------

def _extract_unc_from_budget(
    steps: List[float],
    risultati: Dict[float, Any],
    budget: List[Dict[str, Any]],
    lsb_per_c: float,
    ub_pt_degc: float,
    ub_sensor_lsb: float,
    u_res_degc: float = 0.0,
) -> tuple:
    """Return (u_ref_degc, u_sensor_lsb_list, u_sensor_degc, u_E) per-point lists.

    Priority:
      1. Interpolation model budget keys: mu_T_ref, mu_T_i, U_E
      2. Linear OLS budget keys:         u_T_ref_degC, u_T_i_degC, U_exp_degC
      3. Fallback: recompute from raw pstd_* stats + ub_pt_degc + ub_sensor_lsb

    ``ub_pt_degc`` must already be in °C (not LSB). The caller is responsible
    for converting before calling this function.
    """
    # Index budget by step nominal value — support both key name styles
    budget_by_step: Dict[float, Dict] = {}
    for b in (budget or []):
        key = b.get("t_nominal", b.get("t_nom_degC"))
        if key is not None:
            budget_by_step[float(key)] = b

    u_ref_degc    = []
    u_sensor_lsb_ = []
    u_sensor_degc = []
    u_E_list      = []

    for t in steps:
        b = budget_by_step.get(float(t))
        r = risultati.get(t, {})

        if b and "mu_T_ref" in b:
            # Interpolation model budget (linear_interp, cubic_interp)
            u_ref    = float(b["mu_T_ref"])
            u_si_deg = float(b.get("mu_T_i", 0.0))
            u_E      = float(b.get("U_E", 2.0 * math.sqrt(u_ref**2 + u_si_deg**2)))
        elif b and "u_T_ref_degC" in b:
            # Linear OLS budget (u_budget_per_step)
            u_ref    = float(b["u_T_ref_degC"])
            u_si_deg = float(b.get("u_T_i_degC", 0.0))
            u_E      = float(b.get("U_exp_degC", 2.0 * math.sqrt(u_ref**2 + u_si_deg**2)))
        else:
            # Fallback: recompute from raw type-A stats
            # ub_pt_degc is guaranteed °C here
            uA_ref   = float(r.get("pstd_rtd", 0.0))   # [°C]
            uA_i_lsb = float(r.get("pstd_log", 0.0))   # [LSB]
            uA_i_deg = uA_i_lsb / lsb_per_c
            u_ref    = math.sqrt(uA_ref**2 + ub_pt_degc**2)
            u_si_deg = math.sqrt(uA_i_deg**2 + (ub_sensor_lsb / lsb_per_c)**2 + u_res_degc**2)
            u_E      = 2.0 * math.sqrt(u_ref**2 + u_si_deg**2)

        # Sensor uncertainty in LSB domain (for x-error bars)
        uA_i_lsb_raw = float(r.get("pstd_log", 0.0))
        u_si_lsb     = math.sqrt(uA_i_lsb_raw**2 + ub_sensor_lsb**2)

        u_ref_degc.append(u_ref)
        u_sensor_lsb_.append(u_si_lsb)
        u_sensor_degc.append(u_si_deg)
        u_E_list.append(u_E)

    return u_ref_degc, u_sensor_lsb_, u_sensor_degc, u_E_list


# ---------------------------------------------------------------------------
# Bundle builders
# ---------------------------------------------------------------------------

def bundle_from_linear(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    """Build a PlotBundle from a linear OLS calibration result dict.

    Uncertainty resolution priority
    --------------------------------
    1. ``u_budget_per_step`` — full GUM budget stored by ``calibrate()``,
       keys ``u_T_ref_degC``, ``u_T_i_degC``, ``U_exp_degC``.
    2. Fallback: recompute from ``pstd_*`` + ``ub_pt_degc``.

    The result dict may contain either ``ub_pt_degc`` (°C, preferred) or
    ``ub_pt_lsb`` (LSB, legacy).  Both are handled correctly here.
    """
    from .linear_calibration import get_scale_from_sensor

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    A = calib_result["A"]
    B = calib_result["B"]
    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    sensor_means = [risultati[t]["pmean_log"] for t in steps]

    # Resolve ub_pt in °C — prefer the explicit °C key, fall back to LSB key / lsb_per_c
    if "ub_pt_degc" in calib_result:
        ub_pt_degc = float(calib_result["ub_pt_degc"])
    elif "ub_pt_lsb" in calib_result:
        ub_pt_degc = float(calib_result["ub_pt_lsb"]) / lsb_per_c
    else:
        ub_pt_degc = 0.0

    ub_sensor_lsb = float(calib_result.get("ub_tmp_lsb", 0.0))

    # Use the real per-point GUM budget when available
    budget = calib_result.get("u_budget_per_step", [])
    u_res  = 0.1 / math.sqrt(12.0)

    u_ref_degc, u_sensor_lsb_, u_sensor_degc, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_c, ub_pt_degc, ub_sensor_lsb, u_res,
    )

    # Use expanded_uncertainties from result when budget is absent (they should match)
    if not budget and calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    t_sensor_pre  = [min_v + lsb / lsb_per_c for lsb in sensor_means]
    t_sensor_post = [A * lsb + B for lsb in sensor_means]
    me_pre  = [p - r for p, r in zip(t_sensor_pre, ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    y_dense = (A * x_dense + B).tolist()

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_degc=u_ref_degc,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_degc=u_sensor_degc,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y_degc=y_dense,
        lsb_per_c=lsb_per_c,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Linear OLS  y = A·x + B",
        is_node=None,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
    )


def bundle_from_linear_interp(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    """Build a PlotBundle from a linear_interp calibration result dict."""
    from .linear_calibration import get_scale_from_sensor
    from .linear_interp_calibration import _predict

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    x_nodes   = np.array(calib_result["x_nodes"])
    y_nodes   = np.array(calib_result["y_nodes"])
    steps     = calib_result["steps"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    sensor_means = [risultati[t]["pmean_log"] for t in steps]
    budget    = calib_result.get("per_step_budget", [])

    ub_pt_degc    = calib_result.get("ub_pt_lsb", 0.0)
    ub_sensor_lsb = calib_result.get("ub_tmp_lsb", 0.0)

    u_ref_degc, u_sensor_lsb_, u_sensor_degc, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_c, ub_pt_degc, ub_sensor_lsb,
    )

    budget_by_step = {b["t_nominal"]: b for b in budget}
    t_sensor_pre  = [min_v + lsb / lsb_per_c for lsb in sensor_means]
    t_sensor_post = [ref_means[i] + budget_by_step[t]["residual_degC"] for i, t in enumerate(steps)]
    me_pre  = [p - r for p, r in zip(t_sensor_pre, ref_means)]
    me_post = [budget_by_step[t]["residual_degC"] for t in steps]
    is_node = [budget_by_step[t]["is_node"] for t in steps]

    x_dense = np.linspace(x_nodes.min() * 0.99, x_nodes.max() * 1.01, 500)
    y_dense = [_predict(float(xi), x_nodes, y_nodes) for xi in x_dense]

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_degc=u_ref_degc,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_degc=u_sensor_degc,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y_degc=y_dense,
        lsb_per_c=lsb_per_c,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Linear Lagrange interpolation (first/last nodes)",
        is_node=is_node,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
    )


def bundle_from_cubic_interp(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    """Build a PlotBundle from a cubic_interp calibration result dict."""
    from .linear_calibration import get_scale_from_sensor
    from .cubic_interp_calibration import _predict

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    x_nodes   = np.array(calib_result["x_nodes"])
    y_nodes   = np.array(calib_result["y_nodes"])
    steps     = calib_result["steps"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    sensor_means = [risultati[t]["pmean_log"] for t in steps]
    budget    = calib_result.get("per_step_budget", [])

    ub_pt_degc    = calib_result.get("ub_pt_lsb", 0.0)
    ub_sensor_lsb = calib_result.get("ub_tmp_lsb", 0.0)

    u_ref_degc, u_sensor_lsb_, u_sensor_degc, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_c, ub_pt_degc, ub_sensor_lsb,
    )

    budget_by_step = {b["t_nominal"]: b for b in budget}
    t_sensor_pre  = [min_v + lsb / lsb_per_c for lsb in sensor_means]
    t_sensor_post = [ref_means[i] + budget_by_step[t]["residual_degC"] for i, t in enumerate(steps)]
    me_pre  = [p - r for p, r in zip(t_sensor_pre, ref_means)]
    me_post = [budget_by_step[t]["residual_degC"] for t in steps]
    is_node = [budget_by_step[t]["is_node"] for t in steps]

    x_dense = np.linspace(x_nodes.min() * 0.99, x_nodes.max() * 1.01, 500)
    y_dense = [_predict(float(xi), x_nodes, y_nodes) for xi in x_dense]

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_degc=u_ref_degc,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_degc=u_sensor_degc,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y_degc=y_dense,
        lsb_per_c=lsb_per_c,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Cubic Lagrange interpolation (first-2/last-2 nodes)",
        is_node=is_node,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
    )


def bundle_from_cubic(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    """Build a PlotBundle from a cubic OLS calibration result dict.

    Uses the ``per_step_budget`` entries (keys ``mu_T_ref``, ``mu_T_i``,
    ``U_E``) for all per-point uncertainties.  The post-calibration residual
    (me_post) is ``cubic_predict(pmean_log) − ref_mean``, i.e. the true
    fitting residual at each calibration point.
    """
    from .linear_calibration import get_scale_from_sensor
    from .cubic_calibration import cubic_predict_degc

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_c = adc_max / (max_v - min_v)

    theta     = calib_result["theta"]
    theta_arr = np.array(theta)
    cov_arr   = np.array(calib_result.get("cov_theta",
                         [[0.0]*4]*4))

    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]       # [°C]
    sensor_means = [risultati[t]["pmean_log"] for t in steps]  # [LSB]

    # Per-point uncertainties — prefer budget, fall back to compute
    ub_pt_degc    = calib_result.get("ub_pt_degc",
                    calib_result.get("ub_pt_lsb", 0.0) / lsb_per_c)
    ub_sensor_lsb = calib_result.get("ub_tmp_lsb", 0.0)
    budget        = calib_result.get("per_step_budget", [])

    u_ref_degc, u_sensor_lsb_, u_sensor_degc, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_c, ub_pt_degc, ub_sensor_lsb,
    )

    # Override u_E with the stored expanded_uncertainties when available —
    # they already account for the full GUM budget exactly as computed.
    if calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    # Pre-calibration: raw sensor reading converted via identity LSB→°C
    t_sensor_pre = [min_v + lsb / lsb_per_c for lsb in sensor_means]

    # Post-calibration: evaluate cubic model at each step
    t_sensor_post = [
        cubic_predict_degc(float(lsb), theta_arr, lsb_scale_sensor_info, adc_max)
        for lsb in sensor_means
    ]

    me_pre  = [p - r for p, r in zip(t_sensor_pre,  ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    # Dense model curve in LSB → °C
    x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    y_dense = [
        cubic_predict_degc(float(xi), theta_arr, lsb_scale_sensor_info, adc_max)
        for xi in x_dense
    ]

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_degc=u_ref_degc,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_degc=u_sensor_degc,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y_degc=y_dense,
        lsb_per_c=lsb_per_c,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Cubic OLS  y = a₀ + a₁·x + a₂·x² + a₃·x³",
        is_node=None,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
    )
