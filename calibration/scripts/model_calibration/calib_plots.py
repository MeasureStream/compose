"""
fig1  Sample block-mean time-series per step.
      Reference bands = ±mu_T_ref[i] (combined ref uncertainty).
      Sensor bands    = ±u_sensor_lsb[i] (combined sensor uncertainty in LSB).
      Right axis: plain LSB (no per-step °C translation).

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


DPI       = 100
FIG_W_1x2 = 24.0
FIG_H_1x2 = 10.0
FIG_W_2x3 = 24.0
FIG_H_2x3 = 14.0



# PlotBundle dataclass


@dataclass
class PlotBundle:
    """

    steps           : nominal step values, sorted by sensor reading
    ref_means       : mean reference value per step [°C]
    sensor_means    : mean sensor reading per step [LSB]
    u_ref_y         : combined std unc of reference per step [°C]  = mu_T_ref
    u_sensor_lsb    : combined std unc of sensor per step [LSB]
    u_sensor_y      : combined std unc of sensor per step [°C]     = mu_T_i
    u_E             : expanded uncertainty U(E) = 2·mu_E per step [°C]
    me_pre          : pre-calibration signed error per step [°C]
    me_post         : post-calibration residual per step [°C]
    t_sensor_pre    : raw sensor reading converted to [°C] per step
    t_sensor_post   : calibrated sensor prediction per step [°C]
    model_x_lsb     : dense sensor grid for model curve [LSB]
    model_y         : model output on dense grid [°C]
    lsb_per_y       : LSB/°C conversion factor
    lsb_min, lsb_max: physical range endpoints [LSB]
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
    sensor_means:   List[float]        # [LSB] (or post-preprocess domain, e.g. Ω)
    u_ref_y:        List[float]        # [°C]  per-point combined ref unc
    u_sensor_lsb:   List[float]        # [LSB] per-point combined sensor unc
    u_sensor_y:     List[float]        # [°C]  per-point combined sensor unc
    u_E:            List[float]        # [°C]  expanded uncertainty
    me_pre:         List[float]        # [°C]
    me_post:        List[float]        # [°C]
    t_sensor_pre:   List[float]        # [°C]
    t_sensor_post:  List[float]        # [°C]
    model_x_lsb:    List[float]
    model_y:        List[float]
    lsb_per_y:      float
    lsb_min:        float
    lsb_max:        float
    adc_max:        float
    unit_symbol:    str = "°C"
    measurand_label: str = "Temperature"
    sensor_label:   str = "Sensor"
    ref_label:      str = "Reference"
    model_label:    str = "Calibration model"
    is_node:        Optional[List[bool]] = None
    sample_data:    Optional[Dict[float, Any]] = None
    sample_size:    int = 20
    accuracy_limit: Optional[float] = None
    # Unit of the sensor reading on the X-axis. "LSB" (default) keeps the
    # legacy °C↔LSB secondary axis. Any other value (e.g. "Ω") disables
    # the secondary axis and uses x_unit_symbol as the X label.
    x_unit:         str = "LSB"
    x_unit_symbol:  str = "LSB"
    # Run-unique seed string (e.g. "20260703T143022") stamped on every
    # image as a bottom-right watermark so images and logs can be
    # correlated to the same run.
    run_seed:       str = ""
    # As-found (old-coefficient) dense model curve, for fig3. Populated
    # only when old coefficients are available; empty otherwise.
    model_x_pre:    Optional[List[float]] = None
    model_y_pre:    Optional[List[float]] = None
    # True when a parameter adjustment was actually applied this run
    # (i.e. the new coefficients differ from — and will replace — the
    # old ones). False for a first-run without a prior calibration, or
    # when the sensor was found in tolerance and no adjustment was made.
    adjustment_done: bool = True



# Internal helpers


def _phys_label(measurand: str, unit: str) -> str:
    return f"{measurand} [{unit}]"


def _add_sensor_secondary_axis(ax, lsb_min: float, lsb_per_y: float, unit: str,
                                position: str = "top"):
    ax2 = ax.secondary_xaxis(
        position,
        functions=(
            lambda lsb: lsb_min + lsb / lsb_per_y,
            lambda phys: (phys - lsb_min) * lsb_per_y,
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



# Figure 1 — Sample block-mean time-series per step


def _fig1_sample_timeseries(bundle: PlotBundle, plt):
    """Per-step block-mean time-series.

    Left axis  : reference [unit_symbol]
    Right axis : sensor [LSB]  (plain LSB labels, no per-step °C translation)

    Scale alignment: both axes share a window sized to the wider of the
    reference and sensor half-spans, converted back to LSB for the sensor
    axis using the global lsb_per_y.
    """
    if not bundle.sample_data:
        return None
    steps = [t for t in bundle.steps if t in bundle.sample_data]
    if not steps:
        return None

    step_to_idx = {t: i for i, t in enumerate(bundle.steps)}

    ncols = min(3, len(steps))
    nrows = math.ceil(len(steps) / ncols)
    fig, axs = plt.subplots(nrows, ncols,
                             figsize=(FIG_W_2x3, max(FIG_H_2x3 * nrows / 2, 8)),
                             dpi=DPI, squeeze=False)
    fig.suptitle(
        f"How steady were the readings at each temperature step?\n"
        f"Left: {bundle.ref_label} [{bundle.unit_symbol}]   |   "
        f"Right: {bundle.sensor_label} [{bundle.x_unit_symbol}]\n"
        f"Shaded band = measurement uncertainty at each point",
        fontsize=10, y=1.02,
    )

    for idx, t in enumerate(steps):
        row, col = divmod(idx, ncols)
        ax  = axs[row][col]
        sd  = bundle.sample_data[t]
        x   = sd["x_axis"]

        bi             = step_to_idx.get(t, idx)
        u_ref_i        = bundle.u_ref_y[bi]      # [°C]
        u_sensor_lsb_i = bundle.u_sensor_lsb[bi] # [LSB] (or preprocessed unit)
        _x_unit        = bundle.x_unit_symbol

        smean_rtd = np.asarray(sd["smean_ref"])     # Y [°C]
        smean_log = np.asarray(sd["smean_sensor"]) # X [LSB or preprocessed unit]

        # ── Shared physical half-span ──────────────────────────────────────
        # When the X-domain is LSB we can convert the sensor span to °C using
        # the global lsb_per_y and align the two axes. After preprocessing
        # (e.g. Steinhart) the sensor span is in ohm — there's no fixed °C↔Ω
        # ratio, so each axis uses its own span.
        ref_pp    = smean_rtd.max() - smean_rtd.min()   # [°C]
        ref_half  = ref_pp / 2.0 + u_ref_i              # [°C]
        ref_centre = (smean_rtd.max() + smean_rtd.min()) / 2.0
        sen_pp    = smean_log.max() - smean_log.min()   # [LSB] or [Ω]
        sen_centre = (smean_log.max() + smean_log.min()) / 2.0

        if bundle.x_unit == "LSB":
            sen_half_y = sen_pp / (2.0 * bundle.lsb_per_y) + u_sensor_lsb_i / bundle.lsb_per_y  # [°C]
            half_y     = max(ref_half, sen_half_y, 1e-9) * 1.2   # [°C]
            half_x     = half_y * bundle.lsb_per_y               # [LSB] for sensor axis
        else:
            half_y     = ref_half * 1.2
            half_x     = (sen_pp / 2.0 + u_sensor_lsb_i) * 1.2

        # ── Left axis — reference [°C] ─────────────────────────────────────
        ax.plot(x, smean_rtd, "b-o", linewidth=0.8, markersize=2,
                label=f"{bundle.ref_label} [{bundle.unit_symbol}]")
        ax.fill_between(x, smean_rtd - u_ref_i, smean_rtd + u_ref_i,
                        alpha=0.18, color="tab:blue")
        ax.set_ylim(ref_centre - half_y, ref_centre + half_y)
        ax.set_ylabel(f"Ref [{bundle.unit_symbol}]", fontsize=8, color="tab:blue")
        ax.tick_params(axis="y", labelcolor="tab:blue", labelsize=7)

        # ── Right axis — sensor [X_unit] ───────────────────────────────────
        ax2 = ax.twinx()
        ax2.plot(x, smean_log, "r-o", linewidth=0.8, markersize=2,
                 label=f"{bundle.sensor_label} [{_x_unit}]")
        ax2.fill_between(x, smean_log - u_sensor_lsb_i, smean_log + u_sensor_lsb_i,
                         alpha=0.12, color="tab:red")
        ax2.set_ylim(sen_centre - half_x, sen_centre + half_x)
        ax2.set_ylabel(f"Sensor [{_x_unit}]", fontsize=8, color="tab:red")
        ax2.tick_params(axis="y", labelcolor="tab:red", labelsize=7)

        ax.set_title(
            f"Step {t:.1f} {bundle.unit_symbol}  —  "
            f"u_ref={u_ref_i:.4f} {bundle.unit_symbol}   "
            f"u_sen={u_sensor_lsb_i:.2f} {_x_unit}\n"
            f"window ±{half_y:.4f} {bundle.unit_symbol}",
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



# Figure 2 — Raw scatter (pre-calibration)


def _fig2_raw_scatter(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    _x_unit = bundle.x_unit_symbol
    fig.suptitle(
        f"Sensor vs. reference, before calibration\n"
        f"X: {bundle.sensor_label} [{_x_unit} / {bundle.unit_symbol}]"
        f"   Y: {bundle.ref_label} [{bundle.unit_symbol}]\n"
        f"Error bars = measurement uncertainty at each point",
        fontsize=10,
    )

    x_lsb  = np.array(bundle.sensor_means)
    y_ref  = np.array(bundle.ref_means)
    u_x    = np.array(bundle.u_sensor_lsb)   # [LSB] per-point GUM
    u_y    = np.array(bundle.u_ref_y)        # [°C]  per-point GUM

    for i, (xi, yi, uxi, uyi, t) in enumerate(zip(x_lsb, y_ref, u_x, u_y, bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        ax.errorbar(xi, yi, yerr=uyi,
                    fmt=marker, color=color, ecolor=color,
                    capsize=4, markersize=7, linewidth=1.0)
        ax.annotate(f"{t:.0f}", (xi, yi),
                    textcoords="offset points", xytext=(5, 3), fontsize=7, alpha=0.8)

    ax.set_xlabel(f"{bundle.sensor_label} mean [{_x_unit}]", fontsize=10)
    ax.set_ylabel(f"{bundle.ref_label} mean [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    if bundle.x_unit == "LSB":
        _add_sensor_secondary_axis(ax, bundle.lsb_min, bundle.lsb_per_y, bundle.unit_symbol)

    if bundle.is_node is not None:
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Patch(facecolor="tab:blue",   label="Interpolation node"),
            Patch(facecolor="tab:orange", label="Interior (validation)"),
        ], fontsize=8)
    # No legend when no node distinction — the raw scatter needs no key,
    # and the identity line is intentionally omitted (visual clutter).

    fig.tight_layout()
    return fig



# Figure 3 — Calibration curve


def _fig3_calibration_curve(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    _x_unit = bundle.x_unit_symbol
    fig.suptitle(
        f"How the sensor is translated into a real temperature\n"
        f"Model: {bundle.model_label}\n"
        f"X: {bundle.sensor_label} [{_x_unit} / {bundle.unit_symbol}]"
        f"   Y: {bundle.ref_label} [{bundle.unit_symbol}]",
        fontsize=10,
    )

    # Dense model curve(s). When a parameter adjustment was applied this run,
    # show both the as-found curve (old coefficients, dashed grey) and the
    # as-left curve (new coefficients, solid red). When no adjustment was
    # made (sensor found in tolerance, or --update-parameters none), there
    # is no "new" fit to report — show only the as-found curve, in red,
    # since it IS the certified curve for this certificate.
    if bundle.adjustment_done:
        if bundle.model_x_pre and bundle.model_y_pre:
            ax.plot(bundle.model_x_pre, bundle.model_y_pre,
                    color="gray", linestyle="--", linewidth=1.2, zorder=2,
                    label="As-found (previous coefficients)")
        ax.plot(bundle.model_x_lsb, bundle.model_y,
                "r-", linewidth=1.4, zorder=3, label=f"As-left: {bundle.model_label}")
    else:
        _x_curve = bundle.model_x_pre or bundle.model_x_lsb
        _y_curve = bundle.model_y_pre or bundle.model_y
        ax.plot(_x_curve, _y_curve,
                "r-", linewidth=1.4, zorder=3, label=f"As-found: {bundle.model_label}")

    x_lsb = np.array(bundle.sensor_means)
    y_ref  = np.array(bundle.ref_means)
    u_x    = np.array(bundle.u_sensor_lsb)  # [LSB] (or preprocessed unit)
    u_y    = np.array(bundle.u_ref_y)       # [°C]

    # Reference points with per-point error bars
    ax.errorbar(x_lsb, y_ref, xerr=u_x, yerr=u_y,
                fmt="b.", capsize=4, markersize=8, zorder=4,
                label=f"Reference ± u_c")

    # Predicted points (colour by node/interior). When no adjustment was
    # made this run, there is no "as-left" prediction to show — plot the
    # as-found points (old coefficients) instead, matching the single
    # curve drawn above.
    _point_values = bundle.t_sensor_post if bundle.adjustment_done else bundle.t_sensor_pre
    _point_label  = "Calibrated prediction (as-left)" if bundle.adjustment_done else "As-found prediction"
    for i in range(len(bundle.steps)):
        color  = _step_color(bundle.is_node, i)
        marker = _step_marker(bundle.is_node, i)
        x_i = x_lsb[i]
        y_i = _point_values[i]
        ax.plot(x_i, y_i,
                marker=marker, color=color, markersize=7, zorder=5,
                linestyle="none")
        ax.annotate(f"{bundle.steps[i]:.0f}", (x_i, y_i),
                    textcoords="offset points", xytext=(4, -8),
                    fontsize=7, alpha=0.7, color="tab:red")

    ax.plot([], [], marker="o", color="tab:red", linestyle="none",
            markersize=7, label=_point_label)

    _annotate(ax, x_lsb, y_ref, bundle.steps)

    ax.set_xlabel(f"{bundle.sensor_label} reading [{_x_unit}]", fontsize=10)
    ax.set_ylabel(_phys_label(bundle.measurand_label, bundle.unit_symbol), fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    if bundle.x_unit == "LSB":
        _add_sensor_secondary_axis(ax, bundle.lsb_min, bundle.lsb_per_y, bundle.unit_symbol)

    fig.tight_layout()
    return fig



# Figure 4 — Pre-calibration error (as-found)


def _fig4_pre_error(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"How far off was the sensor before this calibration?\n"
        f"Error = {bundle.sensor_label} (as-found) − {bundle.ref_label}  [{bundle.unit_symbol}]\n"
        f"Error bars show the expanded measurement uncertainty",
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
                    label=f"Tolerance ±{lim:.3f} {bundle.unit_symbol}")
        ax.axhline(-lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6)

    # Y-axis: show actual error magnitude in physical unit (°C), with margin
    # so error bars and limit bands remain visible.
    if len(me_pre) > 0:
        y_lo = float(np.min(me_pre - u_E))
        y_hi = float(np.max(me_pre + u_E))
        span = max(abs(y_lo), abs(y_hi), 1e-9)
        ax.set_ylim(-span * 1.20, span * 1.20)

    ax.set_xlabel(f"{bundle.ref_label} [{bundle.unit_symbol}]", fontsize=10)
    ax.set_ylabel(f"Pre-calibration error M_e_pre [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig



# Figure 5 — Post-calibration residuals (as-left)


def _fig5_post_residuals(bundle: PlotBundle, plt):
    fig, ax = plt.subplots(1, 1, figsize=(FIG_W_1x2 / 2, FIG_H_1x2), dpi=DPI)
    fig.suptitle(
        f"How far off is the sensor after this new calibration?\n"
        f"Error = {bundle.sensor_label} (as-left) − {bundle.ref_label}  [{bundle.unit_symbol}]\n"
        f"Error bars show the expanded measurement uncertainty",
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
                    label=f"Tolerance ±{lim:.3f} {bundle.unit_symbol}")
        ax.axhline(-lim, color="green", linewidth=0.8, linestyle="-.", alpha=0.6)

    if bundle.is_node is not None:
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Patch(facecolor="tab:blue",   label="Node (residual = 0 by construction)"),
            Patch(facecolor="tab:orange", label="Interior (true interpolation error)"),
        ], fontsize=8)
    else:
        ax.legend(fontsize=8)

    # Y-axis: auto-scale to actual residual magnitude (°C) with margin so
    # error bars and limit bands remain visible.
    if len(me_post) > 0:
        y_lo = float(np.min(me_post - u_E))
        y_hi = float(np.max(me_post + u_E))
        span = max(abs(y_lo), abs(y_hi), 1e-9)
        ax.set_ylim(-span * 1.20, span * 1.20)

    ax.set_xlabel(f"{bundle.ref_label} [{bundle.unit_symbol}]", fontsize=10)
    ax.set_ylabel(f"Post-calibration residual M_e_post [{bundle.unit_symbol}]", fontsize=10)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig



# Public entry point


def _add_seed_watermark(fig, seed: str) -> None:
    """Stamp a small run-unique seed string in the bottom-right corner."""
    if not seed:
        return
    fig.text(0.99, 0.01, seed, fontsize=5, color="gray",
             alpha=0.35, ha="right", va="bottom", family="monospace")


def save_five_charts(bundle: PlotBundle, output_dir: Path, prefix: str) -> List[Path]:
    """Generate and save all five standard calibration charts at 600 dpi."""
    import importlib
    import matplotlib
    matplotlib.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    charts = [
        (_fig1_sample_timeseries, "fig1_sample_timeseries"),
        (_fig2_raw_scatter,       "fig2_raw_scatter"),
        (_fig3_calibration_curve, "fig3_calibration_curve"),
        (_fig4_pre_error,         "fig4_pre_error"),
    ]
    # fig5 (as-left residuals) only makes sense when a parameter adjustment
    # was actually applied — with no adjustment there is no new fit to
    # report a post-calibration residual for (as-left == as-found, and
    # showing it again would be redundant / potentially confusing about
    # what was actually "left" on the sensor).
    if bundle.adjustment_done:
        charts.append((_fig5_post_residuals, "fig5_post_residuals"))

    saved: List[Path] = []
    for draw_fn, suffix in charts:
        try:
            fig = draw_fn(bundle, plt)
            if fig is None:
                continue
            _add_seed_watermark(fig, bundle.run_seed)
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



# Internal: extract per-point GUM uncertainties from budget


def _extract_unc_from_budget(
    steps: List[float],
    risultati: Dict[float, Any],
    budget: List[Dict[str, Any]],
    lsb_per_y: float,
    ub_ref_y: float,
    ub_sensor_lsb: float,
    u_res: float = 0.0,
) -> tuple:


    # Priority:
    #   1. Cubic/linear OLS budget keys:   mu_T_ref, mu_T_i, U_E
    #   2. Linear OLS budget keys:         u_ref, u_sensor, U_exp
    #   3. Fallback: recompute from raw pstd_* stats + ub_ref_y + ub_sensor_lsb
    #    Index budget by step nominal value — support both key name styles



    budget_by_step: Dict[float, Dict] = {}
    for b in (budget or []):
        key = b.get("t_nominal", b.get("t_nom"))
        if key is not None:
            budget_by_step[float(key)] = b

    u_ref_y    = []
    u_sensor_lsb_ = []
    u_sensor_y = []
    u_E_list   = []

    for t in steps:
        b = budget_by_step.get(float(t))
        r = risultati.get(t, {})

        if b and "mu_T_ref" in b:
            # Interpolation model budget (linear_interp, cubic_interp)
            u_ref    = float(b["mu_T_ref"])
            u_si_y   = float(b.get("mu_T_i", 0.0))
            u_E      = float(b.get("U_E", 2.0 * math.sqrt(u_ref**2 + u_si_y**2)))
        elif b and "u_ref" in b:
            # Linear OLS budget (u_budget_per_step)
            u_ref    = float(b["u_ref"])
            u_si_y   = float(b.get("u_sensor", 0.0))
            u_E      = float(b.get("U_exp", 2.0 * math.sqrt(u_ref**2 + u_si_y**2)))
        else:
            # Fallback: recompute from raw type-A stats
            uA_ref   = float(r.get("pstd_ref", 0.0))    # Y
            uA_i_lsb = float(r.get("pstd_sensor", 0.0)) # [LSB]
            uA_i_y   = uA_i_lsb / lsb_per_y
            u_ref    = math.sqrt(uA_ref**2 + ub_ref_y**2)
            u_si_y   = math.sqrt(uA_i_y**2 + (ub_sensor_lsb / lsb_per_y)**2 + u_res**2)
            u_E      = 2.0 * math.sqrt(u_ref**2 + u_si_y**2)

        # Sensor uncertainty in LSB domain (for x-error bars)
        uA_i_lsb_raw = float(r.get("pstd_sensor", 0.0))
        u_si_lsb     = math.sqrt(uA_i_lsb_raw**2 + ub_sensor_lsb**2)

        u_ref_y.append(u_ref)
        u_sensor_lsb_.append(u_si_lsb)
        u_sensor_y.append(u_si_y)
        u_E_list.append(u_E)

    return u_ref_y, u_sensor_lsb_, u_sensor_y, u_E_list



# Bundle builders


def bundle_from_linear(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    # Build a PlotBundle from a linear OLS calibration result dict.


    from .linear_calibration import get_scale_from_sensor

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    A = calib_result["A"]
    B = calib_result["B"]
    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    sensor_means = [risultati[t]["pmean_sensor"] for t in steps]

    # Resolve ub_pt in °C — prefer the explicit °C key, fall back to LSB key / lsb_per_y
    if "ub_ref_y" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_y"])
    elif "ub_ref_lsb" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_lsb"]) / lsb_per_y
    else:
        ub_ref_y = 0.0

    ub_sensor_lsb = float(calib_result.get("ub_sensor_lsb", 0.0))

    # Use the real per-point GUM budget when available
    budget = calib_result.get("u_budget_per_step", [])
    u_res  = 0.1 / math.sqrt(12.0)

    u_ref_y, u_sensor_lsb_, u_sensor_y, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_y, ub_ref_y, ub_sensor_lsb, u_res,
    )

    # Use expanded_uncertainties from result when budget is absent (they should match)
    if not budget and calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    # As-found (old coefficients) — fall back to the naive identity LSB→°C
    # mapping only when no prior calibration exists (first-ever run).
    _old_A = calib_result.get("old_A")
    _old_B = calib_result.get("old_B")
    _has_old = _old_A is not None and _old_B is not None
    if _has_old:
        t_sensor_pre = [_old_A * lsb + _old_B for lsb in sensor_means]
    else:
        t_sensor_pre = [min_v + lsb / lsb_per_y for lsb in sensor_means]
    t_sensor_post = [A * lsb + B for lsb in sensor_means]
    me_pre  = [p - r for p, r in zip(t_sensor_pre, ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    y_dense = (A * x_dense + B).tolist()
    if _has_old:
        y_dense_pre = (_old_A * x_dense + _old_B).tolist()
    else:
        y_dense_pre = (min_v + x_dense / lsb_per_y).tolist()

    _adjustment_done = calib_result.get("calibration_done", "done") != "not_necessary"

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_y=u_ref_y,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_y=u_sensor_y,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y=y_dense,
        model_x_pre=x_dense.tolist(),
        model_y_pre=y_dense_pre,
        lsb_per_y=lsb_per_y,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        measurand_label=measurand_label,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Linear OLS  y = A·x + B",
        is_node=None,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
        run_seed=calib_result.get("run_seed", ""),
        adjustment_done=_adjustment_done,
    )


def bundle_from_cubic(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    """Build a PlotBundle from a cubic OLS calibration result dict.

    Uses the ``per_step_budget`` entries (keys ``mu_T_ref``, ``mu_T_i``,
    ``U_E``) for all per-point uncertainties.  The post-calibration residual
    (me_post) is ``cubic_predict(pmean_sensor) − ref_mean``, i.e. the true
    fitting residual at each calibration point.
    """
    from .linear_calibration import get_scale_from_sensor
    from .cubic_calibration import cubic_predict_y

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    theta     = calib_result["theta"]
    theta_arr = np.array(theta)
    cov_arr   = np.array(calib_result.get("cov_theta",
                         [[0.0]*4]*4))

    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]       # [°C]
    sensor_means = [risultati[t]["pmean_sensor"] for t in steps]  # [LSB]

    # Per-point uncertainties — prefer budget, fall back to compute
    if "ub_ref_y" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_y"])
    elif "ub_pt_y" in calib_result:
        ub_ref_y = float(calib_result["ub_pt_y"])
    elif "ub_ref_lsb" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_lsb"]) / lsb_per_y
    elif "ub_pt_lsb" in calib_result:
        ub_ref_y = float(calib_result["ub_pt_lsb"]) / lsb_per_y
    else:
        ub_ref_y = 0.0
    ub_sensor_lsb = float(
        calib_result.get("ub_sensor_lsb",
        calib_result.get("ub_tmp_lsb", 0.0))
    )
    budget        = calib_result.get("per_step_budget", [])

    u_ref_y, u_sensor_lsb_, u_sensor_y, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_y, ub_ref_y, ub_sensor_lsb,
    )

    # Override u_E with the stored expanded_uncertainties when available —
    # they already account for the full GUM budget exactly as computed.
    if calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    # As-found (old coefficients) — fall back to the naive identity LSB→°C
    # mapping only when no prior calibration exists (first-ever run).
    _old_a0, _old_a1 = calib_result.get("old_a0"), calib_result.get("old_a1")
    _old_a2, _old_a3 = calib_result.get("old_a2"), calib_result.get("old_a3")
    _has_old = all(v is not None for v in [_old_a0, _old_a1, _old_a2, _old_a3])
    _old_theta = np.array([_old_a0, _old_a1, _old_a2, _old_a3], dtype=float) if _has_old else None

    if _has_old:
        t_sensor_pre = [
            cubic_predict_y(float(lsb), _old_theta, lsb_scale_sensor_info, adc_max)
            for lsb in sensor_means
        ]
    else:
        t_sensor_pre = [min_v + lsb / lsb_per_y for lsb in sensor_means]

    # Post-calibration: evaluate cubic model at each step
    t_sensor_post = [
        cubic_predict_y(float(lsb), theta_arr, lsb_scale_sensor_info, adc_max)
        for lsb in sensor_means
    ]

    me_pre  = [p - r for p, r in zip(t_sensor_pre,  ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    # Dense model curve in LSB → °C
    x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    y_dense = [
        cubic_predict_y(float(xi), theta_arr, lsb_scale_sensor_info, adc_max)
        for xi in x_dense
    ]
    if _has_old:
        y_dense_pre = [
            cubic_predict_y(float(xi), _old_theta, lsb_scale_sensor_info, adc_max)
            for xi in x_dense
        ]
    else:
        y_dense_pre = (min_v + x_dense / lsb_per_y).tolist()

    _adjustment_done = calib_result.get("calibration_done", "done") != "not_necessary"

    return PlotBundle(
        steps=steps,
        ref_means=ref_means,
        sensor_means=sensor_means,
        u_ref_y=u_ref_y,
        u_sensor_lsb=u_sensor_lsb_,
        u_sensor_y=u_sensor_y,
        u_E=u_E,
        me_pre=me_pre,
        me_post=me_post,
        t_sensor_pre=t_sensor_pre,
        t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(),
        model_y=y_dense,
        model_x_pre=x_dense.tolist(),
        model_y_pre=y_dense_pre,
        lsb_per_y=lsb_per_y,
        lsb_min=min_v,
        lsb_max=max_v,
        adc_max=adc_max,
        unit_symbol=unit_symbol,
        measurand_label=measurand_label,
        sensor_label=sensor_label,
        ref_label=ref_label,
        model_label="Cubic OLS  y = a₀ + a₁·x + a₂·x² + a₃·x³",
        is_node=None,
        sample_data=risultati,
        sample_size=20,
        accuracy_limit=accuracy_limit,
        run_seed=calib_result.get("run_seed", ""),
        adjustment_done=_adjustment_done,
    )


def bundle_from_quadratic(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    from .linear_calibration import get_scale_from_sensor
    from .quadratic_calibration import quadratic_predict_y

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    theta     = calib_result["theta"]
    theta_arr = np.array(theta)

    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    sensor_means = [risultati[t]["pmean_sensor"] for t in steps]

    if "ub_ref_y" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_y"])
    elif "ub_ref_lsb" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_lsb"]) / lsb_per_y
    else:
        ub_ref_y = 0.0
    ub_sensor_lsb = float(calib_result.get("ub_sensor_lsb", 0.0))
    budget        = calib_result.get("per_step_budget", [])

    u_ref_y, u_sensor_lsb_, u_sensor_y, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_y, ub_ref_y, ub_sensor_lsb,
    )
    if calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    _old_a0, _old_a1, _old_a2 = calib_result.get("old_a0"), calib_result.get("old_a1"), calib_result.get("old_a2")
    _has_old = all(v is not None for v in [_old_a0, _old_a1, _old_a2])
    _old_theta = np.array([_old_a0, _old_a1, _old_a2], dtype=float) if _has_old else None

    if _has_old:
        t_sensor_pre = [
            quadratic_predict_y(float(lsb), _old_theta, lsb_scale_sensor_info, adc_max)
            for lsb in sensor_means
        ]
    else:
        t_sensor_pre = [min_v + lsb / lsb_per_y for lsb in sensor_means]
    t_sensor_post = [
        quadratic_predict_y(float(lsb), theta_arr, lsb_scale_sensor_info, adc_max)
        for lsb in sensor_means
    ]
    me_pre  = [p - r for p, r in zip(t_sensor_pre,  ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    y_dense = [
        quadratic_predict_y(float(xi), theta_arr, lsb_scale_sensor_info, adc_max)
        for xi in x_dense
    ]
    if _has_old:
        y_dense_pre = [
            quadratic_predict_y(float(xi), _old_theta, lsb_scale_sensor_info, adc_max)
            for xi in x_dense
        ]
    else:
        y_dense_pre = (min_v + x_dense / lsb_per_y).tolist()

    _adjustment_done = calib_result.get("calibration_done", "done") != "not_necessary"

    return PlotBundle(
        steps=steps, ref_means=ref_means, sensor_means=sensor_means,
        u_ref_y=u_ref_y, u_sensor_lsb=u_sensor_lsb_, u_sensor_y=u_sensor_y, u_E=u_E,
        me_pre=me_pre, me_post=me_post,
        t_sensor_pre=t_sensor_pre, t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(), model_y=y_dense,
        model_x_pre=x_dense.tolist(), model_y_pre=y_dense_pre,
        lsb_per_y=lsb_per_y, lsb_min=min_v, lsb_max=max_v, adc_max=adc_max,
        unit_symbol=unit_symbol, measurand_label=measurand_label,
        sensor_label=sensor_label, ref_label=ref_label,
        model_label="Quadratic OLS  y = a₀ + a₁·x + a₂·x²",
        is_node=None, sample_data=risultati, sample_size=20,
        accuracy_limit=accuracy_limit,
        run_seed=calib_result.get("run_seed", ""),
        adjustment_done=_adjustment_done,
    )


def bundle_from_steinhart(
    calib_result: Dict[str, Any],
    lsb_scale_sensor_info: Dict[str, Any],
    adc_max: float,
    unit_symbol: str = "°C",
    measurand_label: str = "Temperature",
    sensor_label: str = "Sensor",
    ref_label: str = "Reference",
    accuracy_limit: Optional[float] = None,
) -> PlotBundle:
    from .linear_calibration import get_scale_from_sensor
    from .steinhart_calibration import steinhart_predict_sh

    min_v, max_v = get_scale_from_sensor(lsb_scale_sensor_info)
    lsb_per_y = adc_max / (max_v - min_v)

    theta     = calib_result["theta"]
    theta_arr = np.array(theta)

    steps     = calib_result["temp_nominali"]
    risultati = calib_result["risultati_elaborati"]
    ref_means = calib_result["ref_temp_means"]
    # pmean_sensor is already in the preprocessed domain (e.g. ohm for
    # Steinhart) — the orchestrator runs the sensor's preprocessingFormula
    # on the raw LSB samples before invoking the fit, so the engine never
    # sees LSB.
    sensor_means = [risultati[t]["pmean_sensor"] for t in steps]

    if "ub_ref_y" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_y"])
    elif "ub_ref_lsb" in calib_result:
        ub_ref_y = float(calib_result["ub_ref_lsb"]) / lsb_per_y
    else:
        ub_ref_y = 0.0
    ub_sensor_lsb = float(calib_result.get("ub_sensor_lsb", 0.0))
    budget        = calib_result.get("per_step_budget", [])

    u_ref_y, u_sensor_lsb_, u_sensor_y, u_E = _extract_unc_from_budget(
        steps, risultati, budget, lsb_per_y, ub_ref_y, ub_sensor_lsb,
    )
    if calib_result.get("expanded_uncertainties"):
        u_E = list(calib_result["expanded_uncertainties"])

    # As-found (pre) error: use old Steinhart coefficients applied to R[Ohm].
    # sensor_means already contains R[Ohm] after preprocessing.
    # If old coefficients exist, predict with them; else fall back to new theta
    # (pre = post, meaning no drift since last calibration or first calibration).
    _old_a = calib_result.get("old_a")
    _old_b = calib_result.get("old_b")
    _old_c = calib_result.get("old_c")
    _has_old = all(v is not None for v in [_old_a, _old_b, _old_c])
    _old_theta = np.array([_old_a, _old_b, _old_c], dtype=float) if _has_old else theta_arr

    t_sensor_pre  = [steinhart_predict_sh(float(xi), _old_theta) for xi in sensor_means]
    t_sensor_post = [steinhart_predict_sh(float(xi), theta_arr)  for xi in sensor_means]
    me_pre  = [p - r for p, r in zip(t_sensor_pre,  ref_means)]
    me_post = [p - r for p, r in zip(t_sensor_post, ref_means)]

    # Dense model curve in the sensor's native X domain.
    if len(sensor_means) >= 2:
        x_dense = np.linspace(min(sensor_means) * 0.99, max(sensor_means) * 1.01, 500)
    else:
        x_dense = np.array(sensor_means, dtype=float)
    y_dense = [steinhart_predict_sh(float(xi), theta_arr) for xi in x_dense]
    y_dense_pre = [steinhart_predict_sh(float(xi), _old_theta) for xi in x_dense]

    _adjustment_done = calib_result.get("calibration_done", "done") != "not_necessary"

    return PlotBundle(
        steps=steps, ref_means=ref_means, sensor_means=sensor_means,
        u_ref_y=u_ref_y, u_sensor_lsb=u_sensor_lsb_, u_sensor_y=u_sensor_y, u_E=u_E,
        me_pre=me_pre, me_post=me_post,
        t_sensor_pre=t_sensor_pre, t_sensor_post=t_sensor_post,
        model_x_lsb=x_dense.tolist(), model_y=y_dense,
        model_x_pre=x_dense.tolist(), model_y_pre=y_dense_pre,
        lsb_per_y=lsb_per_y, lsb_min=min_v, lsb_max=max_v, adc_max=adc_max,
        unit_symbol=unit_symbol, measurand_label=measurand_label,
        sensor_label=sensor_label, ref_label=ref_label,
        model_label="Steinhart-Hart  1/T = a + b\u00b7ln(R) + c\u00b7(ln R)\u00b3",
        is_node=None, sample_data=risultati, sample_size=20,
        accuracy_limit=accuracy_limit,
        x_unit="PREPROCESSED", x_unit_symbol="\u03a9",
        run_seed=calib_result.get("run_seed", ""),
        adjustment_done=_adjustment_done,
    )
