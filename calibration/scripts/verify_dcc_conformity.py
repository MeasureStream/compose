#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
NA   = "N/A"

_W = 70


def _hr(char: str = "=") -> str:
    return char * _W


def _status_line(label: str, status: str, detail: str = "") -> str:
    dots = "." * max(1, _W - len(label) - len(status) - len(detail) - 4)
    return f"  {label} {dots} [{status}]  {detail}".rstrip()


def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    if sigma <= 0.0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def parse_dcc_xml(xml_path: Path) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        print(f"[ERROR] Failed to parse XML file {xml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    quantities = [elem for elem in root.iter() if elem.tag.endswith("quantity")]

    t_ref: List[float]   = []
    t_sensor: List[float] = []
    me_pre: List[float]  = []
    me_post: List[float] = []
    u_exp: List[float]   = []

    for qty in quantities:
        ref_type = qty.get("refType")
        if not ref_type:
            continue
        value_elems = [e for e in qty.iter() if e.tag.endswith("valueXMLList")]
        values: List[float] = []
        if value_elems and value_elems[0].text:
            values = [float(x) for x in value_elems[0].text.strip().split()]

        if ref_type == "basic_referenceValue":
            t_ref = values
        elif ref_type == "basic_measuredValue":
            t_sensor = values
        elif ref_type == "gp_measurementErrorPreCalibration":
            me_pre = values
        elif ref_type == "basic_measurementError":
            me_post = values
            unc_elems = [e for e in qty.iter() if e.tag.endswith("uncertaintyXMLList")]
            if unc_elems and unc_elems[0].text:
                u_exp = [float(x) for x in unc_elems[0].text.strip().split()]

    n = len(t_ref)
    if n == 0:
        print("[ERROR] No reference temperature values found in XML.", file=sys.stderr)
        sys.exit(1)

    if len(t_sensor) != n:
        print(f"[WARNING] T_ref has {n} values, T_sensor has {len(t_sensor)}. Padding/truncating.")
        t_sensor = (t_sensor + [0.0] * n)[:n]

    if not me_pre:
        print("[INFO] Pre-calibration errors not found in XML. Deriving from T_sensor - T_ref.")
        me_pre = [ts - tr for ts, tr in zip(t_sensor, t_ref)]
    elif len(me_pre) != n:
        me_pre = (me_pre + [0.0] * n)[:n]

    if not me_post:
        me_post = [ts - tr for ts, tr in zip(t_sensor, t_ref)]
    elif len(me_post) != n:
        me_post = (me_post + [0.0] * n)[:n]

    if not u_exp:
        print("[WARNING] Expanded uncertainties not found in XML. Defaulting to 0.0.")
        u_exp = [0.0] * n
    elif len(u_exp) == 1:
        u_exp = u_exp * n
    elif len(u_exp) != n:
        u_exp = (u_exp + [u_exp[-1]] * n)[:n]

    return t_ref, t_sensor, me_pre, me_post, u_exp


def load_sensor_accuracy_ranges(sensor_path: Path) -> List[Dict[str, Any]]:
    if not sensor_path.exists():
        print(f"[WARNING] Sensor model file not found at: {sensor_path}")
        return []
    try:
        data = json.loads(sensor_path.read_text(encoding="utf-8"))
        return data.get("metrology", {}).get("sensorAccuracy", [])
    except Exception as e:
        print(f"[WARNING] Failed to load sensor accuracy from {sensor_path}: {e}")
        return []


def run_checks(
    t_ref: List[float],
    t_sensor: List[float],
    me_pre: List[float],
    u_sensor: List[float],
    accuracy_ranges: List[Dict[str, Any]],
    mae: float,
    pfa_threshold_pct: float,
    u_ref: float,
) -> Dict[str, Any]:
    n = len(t_ref)
    results: Dict[str, Any] = {
        "check_g": {"status": NA, "details": [], "note": ""},
        "check_h": {"status": FAIL, "details": []},
        "check_overlap": {"status": FAIL, "details": []},
    }

    # Check G: sensorAccuracy as-found conformity
    if accuracy_ranges:
        g1_all_pass    = True
        g2_all_covered = True
        g_details      = []

        for i in range(n):
            temp      = t_ref[i]
            error_val = me_pre[i]
            applicable = [r["maxError"] for r in accuracy_ranges if r["tempMin"] <= temp <= r["tempMax"]]
            max_err   = min(applicable) if applicable else float("inf")
            covered   = max_err < float("inf")

            if not covered:
                g2_all_covered = False
                g1_pass = True
                g2_pass = False
            else:
                g1_pass = abs(error_val) <= max_err
                g2_pass = True
                if not g1_pass:
                    g1_all_pass = False

            g_details.append({
                "index": i + 1, "t_ref": temp, "me_pre": error_val,
                "max_allowed_error": max_err if covered else None,
                "G1_pass": g1_pass, "G2_pass": g2_pass,
            })

        g_status = FAIL if not g1_all_pass else (WARN if not g2_all_covered else PASS)
        results["check_g"] = {
            "status": g_status, "details": g_details,
            "note": (
                "Validated against declared sensorAccuracy specifications."
                if g_status != WARN
                else "Some calibration points fall outside declared temperature ranges."
            ),
        }
    else:
        results["check_g"] = {
            "status": NA, "details": [],
            "note": "Skipped: No sensor model accuracy ranges loaded.",
        }

    # Check H: Probability of False Acceptance (PFA)
    h_all_pass    = True
    h_details     = []
    pfa_threshold = pfa_threshold_pct / 100.0

    for i in range(n):
        error_val = me_pre[i]
        u_std     = u_sensor[i] / 2.0
        u_ein     = u_std / mae
        ein       = error_val / mae

        if u_std > 0.0:
            pfa = 1.0 - normal_cdf(1.0, mu=ein, sigma=u_ein) + normal_cdf(-1.0, mu=ein, sigma=u_ein)
        else:
            pfa = 0.0 if abs(ein) <= 1.0 else 1.0

        pfa = max(0.0, min(1.0, pfa))
        ok  = pfa <= pfa_threshold
        if not ok:
            h_all_pass = False

        h_details.append({
            "index": i + 1, "t_ref": t_ref[i], "me_pre": error_val,
            "u_std": u_std, "pfa_pct": pfa * 100.0, "pass": ok,
        })

    results["check_h"] = {"status": PASS if h_all_pass else FAIL, "details": h_details}

    # Uncertainties Overlap Check (simple interval and RSS)
    overlap_all_pass = True
    overlap_details  = []

    for i in range(n):
        diff    = abs(t_sensor[i] - t_ref[i])
        u_sns   = u_sensor[i]
        sum_unc = u_sns + u_ref
        rss_unc = math.sqrt(u_sns**2 + u_ref**2)

        simple_ok = diff <= sum_unc
        rss_ok    = diff <= rss_unc
        if not simple_ok or not rss_ok:
            overlap_all_pass = False

        overlap_details.append({
            "index": i + 1, "t_ref": t_ref[i], "t_sns": t_sensor[i],
            "diff": diff, "u_sns": u_sns, "u_ref": u_ref,
            "simple_pass": simple_ok, "rss_pass": rss_ok,
        })

    results["check_overlap"] = {"status": PASS if overlap_all_pass else FAIL, "details": overlap_details}

    return results


def print_results_report(
    xml_path: Path,
    t_ref: List[float],
    t_sensor: List[float],
    me_pre: List[float],
    me_post: List[float],
    u_sensor: List[float],
    results: Dict[str, Any],
    mae: float,
    pfa_threshold: float,
    u_ref: float,
) -> None:
    print()
    print(_hr("="))
    print(f"  DCC XML CONFORMITY VERIFICATION REPORT")
    print(f"  Target XML : {xml_path.name}")
    print(f"  Points     : {len(t_ref)}")
    print(_hr("="))

    print("\n  EXTRACTED DATA FROM DCC XML:")
    print(
        f"  {'Pt':>3}  {'T_ref':>12}  {'T_sensor':>14}  "
        f"{'M_e_pre':>13}  {'M_e_post':>14}  {'U_sensor':>12}"
    )
    print(f"  {'-'*3}  {'-'*12}  {'-'*14}  {'-'*13}  {'-'*14}  {'-'*12}")
    for i in range(len(t_ref)):
        print(
            f"  {i+1:>3}  {t_ref[i]:>12.4f}  {t_sensor[i]:>14.4f}  "
            f"{me_pre[i]:>13.4e}  {me_post[i]:>14.4e}  {u_sensor[i]:>12.4f}"
        )

    g_res = results["check_g"]
    print("\n" + _hr("-"))
    print(f"  [G] SENSOR ACCURACY AS-FOUND CONFORMITY")
    print(_hr("-"))
    if g_res["status"] == NA:
        print(f"  Status: {NA} (No sensor model accuracy ranges loaded)")
    else:
        print(f"  {'Pt':>3}  {'T_ref':>12}  {'M_e_pre':>13}  {'Limit':>12}  {'G1 (In Limit)':>13}  {'G2 (Covered)':>12}")
        print(f"  {'-'*3}  {'-'*12}  {'-'*13}  {'-'*12}  {'-'*13}  {'-'*12}")
        for pt in g_res["details"]:
            lim_str = f"+/-{pt['max_allowed_error']:.4f}" if pt["max_allowed_error"] is not None else "N/A"
            print(f"  {pt['index']:>3}  {pt['t_ref']:>12.4f}  {pt['me_pre']:>13.4f}  {lim_str:>12}  {PASS if pt['G1_pass'] else FAIL:>13}  {PASS if pt['G2_pass'] else WARN:>12}")
        print(f"\n  Check G Verdict: [{g_res['status']}]  {g_res['note']}")

    h_res = results["check_h"]
    print("\n" + _hr("-"))
    print(f"  [H] PROBABILITY OF FALSE ACCEPTANCE (PFA)")
    print(f"      MAE = +/-{mae:.3f},  PFA Acceptance Threshold = {pfa_threshold:.1f} %")
    print(_hr("-"))
    print(f"  {'Pt':>3}  {'T_ref':>12}  {'M_e_pre':>13}  {'u_std':>12}  {'PFA [%]':>11}  {'Verdict':>10}")
    print(f"  {'-'*3}  {'-'*12}  {'-'*13}  {'-'*12}  {'-'*11}  {'-'*10}")
    for pt in h_res["details"]:
        print(f"  {pt['index']:>3}  {pt['t_ref']:>12.4f}  {pt['me_pre']:>13.4f}  {pt['u_std']:>12.4f}  {pt['pfa_pct']:>10.1f}%  {PASS if pt['pass'] else FAIL:>10}")
    print(f"\n  Check H Verdict: [{h_res['status']}]")

    overlap_res = results["check_overlap"]
    print("\n" + _hr("-"))
    print(f"  UNCERTAINTIES OVERLAP & COMPATIBILITY CHECK")
    print(f"  Reference Expanded Uncertainty (U_ref) = {u_ref:.4f} (k=2)")
    print(_hr("-"))
    print(
        f"  {'Pt':>3}  {'T_ref':>12}  {'T_sensor':>14}  "
        f"{'|Diff|':>12}  {'Simple Overlap':>15}  {'RSS Compat.':>12}"
    )
    print(f"  {'-'*3}  {'-'*12}  {'-'*14}  {'-'*12}  {'-'*15}  {'-'*10}")
    for pt in overlap_res["details"]:
        print(
            f"  {pt['index']:>3}  {pt['t_ref']:>12.4f}  {pt['t_sns']:>14.4f}  "
            f"{pt['diff']:>12.4f}  {PASS if pt['simple_pass'] else FAIL:>15}  {PASS if pt['rss_pass'] else FAIL:>12}"
        )
    print(f"\n  Overlap Check Verdict: [{overlap_res['status']}]")

    print("\n" + _hr("="))
    g_ok      = g_res["status"] in (PASS, NA)
    h_ok      = h_res["status"] == PASS
    o_ok      = overlap_res["status"] == PASS
    overall   = "CONFORME" if (g_ok and h_ok and o_ok) else "NON CONFORME"
    print(f"  OVERALL VERDICT: {overall}")
    print(f"  [G] SensorAccuracy: [{g_res['status']}]")
    print(f"  [H] PFA Check     : [{h_res['status']}]")
    print(f"  [*] Overlap Check : [{overlap_res['status']}]")
    print(_hr("="))
    print()


# ── Chart generation ────────────────────────────────────────────────────────

def save_charts(
    images_dir: Path,
    t_ref: List[float],
    t_sensor: List[float],
    me_pre: List[float],
    me_post: List[float],
    u_sensor: List[float],
    results: Dict[str, Any],
    mae: float,
    pfa_threshold_pct: float,
    u_ref: float,
    model_label: str = "Calibration model",
    variant: str = "funzione",
) -> List[str]:

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        print("[WARNING] matplotlib not available — skipping chart generation.")
        return []

    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    saved: List[str] = []
    pts = list(range(1, len(t_ref) + 1))
    t_arr = np.array(t_ref)

    DPI = 150
    COLOR_PASS = "#2ecc71"
    COLOR_FAIL = "#e74c3c"
    COLOR_WARN = "#f39c12"
    COLOR_PRE  = "#3498db"
    COLOR_POST = "#9b59b6"

    def _status_color(s: str) -> str:
        return COLOR_PASS if s == PASS else (COLOR_WARN if s == WARN else (COLOR_FAIL if s == FAIL else "#95a5a6"))

    UNIT = "°C"

    # ── Fig 1: PFA Bar Chart (Check H) ────────────────────────────────────
    h_res = results["check_h"]
    if h_res["details"]:
        fig, ax = plt.subplots(figsize=(10, 5))
        pfas = [d["pfa_pct"] for d in h_res["details"]]
        colors = [COLOR_PASS if d["pass"] else COLOR_FAIL for d in h_res["details"]]
        labels = [f"Pt {d['index']}\n{d['t_ref']:.1f}{UNIT}" for d in h_res["details"]]

        bars = ax.bar(labels, pfas, color=colors, edgecolor="white", linewidth=0.8, zorder=3)
        ax.axhline(pfa_threshold_pct, color="#e74c3c", linewidth=1.5, linestyle="--",
                   label=f"PFA threshold = {pfa_threshold_pct:.1f}%", zorder=4)

        # Value labels on bars
        for bar, val in zip(bars, pfas):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    f"{val:.1f}%", ha="center", va="bottom", fontsize=8, fontweight="bold")

        ax.set_xlabel("Calibration Point", fontsize=11)
        ax.set_ylabel("PFA [%]", fontsize=11)
        ax.set_title(f"[H] Probability of False Acceptance per Point — {model_label}",
                     fontsize=13, fontweight="bold")
        ax.set_ylim(bottom=0, top=max(max(pfas) * 1.25, pfa_threshold_pct * 1.5))
        ax.grid(axis="y", alpha=0.4, zorder=0)
        ax.legend(fontsize=10)

        pass_patch = mpatches.Patch(color=COLOR_PASS, label="PASS")
        fail_patch = mpatches.Patch(color=COLOR_FAIL, label="FAIL")
        ax.legend(handles=[pass_patch, fail_patch,
                            mpatches.Patch(color="#e74c3c", label=f"Threshold {pfa_threshold_pct:.1f}%")],
                  fontsize=9, loc="upper right")

        fig.tight_layout()
        out = images_dir / "fig1_pfa_chart.png"
        fig.savefig(out, dpi=DPI)
        plt.close(fig)
        saved.append(str(out))
        print(f"[INFO] Saved chart: {out}")

    # ── Fig 2: Pre/Post Error Bars with ±U_exp ───────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.array(pts, dtype=float)
    u_arr = np.array(u_sensor)

    ax.errorbar(x - 0.1, me_pre, yerr=u_arr, fmt="o", color=COLOR_PRE,
                ecolor=COLOR_PRE, elinewidth=1.5, capsize=5, capthick=1.5,
                label="Pre-cal error (M_e_pre) ± U_exp", zorder=4)
    ax.errorbar(x + 0.1, me_post, yerr=u_arr, fmt="s", color=COLOR_POST,
                ecolor=COLOR_POST, elinewidth=1.5, capsize=5, capthick=1.5,
                label="Post-cal error (M_e_post) ± U_exp", zorder=4)

    ax.axhline(0, color="black", linewidth=0.8, linestyle="-", zorder=2)
    ax.axhline(mae, color="#e74c3c", linewidth=1.2, linestyle="--",
               label=f"+MAE = +{mae:.3f}{UNIT}", zorder=3)
    ax.axhline(-mae, color="#e74c3c", linewidth=1.2, linestyle="--",
               label=f"-MAE = -{mae:.3f}{UNIT}", zorder=3)

    ax.set_xticks(pts)
    ax.set_xticklabels([f"Pt {i}\n{t:.1f}{UNIT}" for i, t in zip(pts, t_ref)], fontsize=8)
    ax.set_xlabel("Calibration Point", fontsize=11)
    ax.set_ylabel(f"Measurement Error [{UNIT}]", fontsize=11)
    ax.set_title(f"Pre/Post Calibration Errors with Expanded Uncertainty — {model_label}",
                 fontsize=13, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.4, zorder=0)
    fig.tight_layout()
    out = images_dir / "fig2_error_bars.png"
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    saved.append(str(out))
    print(f"[INFO] Saved chart: {out}")

    # ── Fig 3: Overlap Check ─────────────────────────────────────────────
    overlap_res = results["check_overlap"]
    if overlap_res["details"]:
        fig, ax = plt.subplots(figsize=(10, 5))
        diffs    = np.array([d["diff"] for d in overlap_res["details"]])
        u_sns_arr = np.array([d["u_sns"] for d in overlap_res["details"]])
        sum_unc  = u_sns_arr + u_ref
        rss_unc  = np.sqrt(u_sns_arr**2 + u_ref**2)

        x = np.array(pts, dtype=float)
        ax.bar(x - 0.25, diffs, width=0.25, label="|T_sensor - T_ref|", color="#3498db",
               edgecolor="white", zorder=3)
        ax.bar(x,       sum_unc, width=0.25, label="U_sensor + U_ref (simple)", color="#f39c12",
               edgecolor="white", alpha=0.85, zorder=3)
        ax.bar(x + 0.25, rss_unc, width=0.25, label="sqrt(U_s²+U_r²) (RSS)", color="#2ecc71",
               edgecolor="white", alpha=0.85, zorder=3)

        # Color bars by pass/fail
        for i, d in enumerate(overlap_res["details"]):
            ok = d["simple_pass"] and d["rss_pass"]
            ax.get_children()[i].set_edgecolor(COLOR_PASS if ok else COLOR_FAIL)
            ax.get_children()[i].set_linewidth(1.5 if not ok else 0.5)

        ax.set_xticks(pts)
        ax.set_xticklabels([f"Pt {i}\n{t:.1f}{UNIT}" for i, t in zip(pts, t_ref)], fontsize=8)
        ax.set_xlabel("Calibration Point", fontsize=11)
        ax.set_ylabel(f"Temperature [{UNIT}]", fontsize=11)
        ax.set_title(f"Uncertainty Overlap & Compatibility Check — {model_label}",
                     fontsize=13, fontweight="bold")
        ax.legend(fontsize=9)
        ax.grid(axis="y", alpha=0.4, zorder=0)
        fig.tight_layout()
        out = images_dir / "fig3_overlap_check.png"
        fig.savefig(out, dpi=DPI)
        plt.close(fig)
        saved.append(str(out))
        print(f"[INFO] Saved chart: {out}")

    # ── Fig 4: Check G — As-Found Errors vs Sensor Accuracy Bands ────────
    g_res = results["check_g"]
    if g_res["status"] != NA and g_res["details"]:
        fig, ax = plt.subplots(figsize=(10, 5))
        g_details = g_res["details"]

        for d in g_details:
            color = COLOR_PASS if d["G1_pass"] else COLOR_FAIL
            marker = "o" if d["G1_pass"] else "X"
            ax.scatter(d["t_ref"], d["me_pre"], color=color, marker=marker,
                       s=80, zorder=5, linewidths=1.5, edgecolors="white")

            if d["max_allowed_error"] is not None:
                ax.annotate(
                    f"±{d['max_allowed_error']:.3f}",
                    xy=(d["t_ref"], d["me_pre"]),
                    xytext=(5, 8), textcoords="offset points",
                    fontsize=7, color="#555555",
                )

        # Draw the sensor accuracy band as step function
        limits_used: Dict[Tuple[float, float], float] = {}
        from itertools import groupby
        t_sorted = sorted(g_details, key=lambda d: d["t_ref"])
        for d in t_sorted:
            if d["max_allowed_error"] is not None:
                # Draw horizontal band at this point
                ax.vlines(d["t_ref"], -d["max_allowed_error"], d["max_allowed_error"],
                          color="#e74c3c", linewidth=0.6, alpha=0.5, zorder=2)

        ax.axhline(0, color="black", linewidth=0.8, zorder=2)
        ax.set_xlabel(f"Reference Temperature [{UNIT}]", fontsize=11)
        ax.set_ylabel(f"As-Found Error M_e_pre [{UNIT}]", fontsize=11)
        ax.set_title(f"[G] Sensor Accuracy As-Found Conformity — {model_label} ({variant})",
                     fontsize=13, fontweight="bold")

        pass_patch = mpatches.Patch(color=COLOR_PASS, label="In limit (PASS)")
        fail_patch = mpatches.Patch(color=COLOR_FAIL, label="Out of limit (FAIL)")
        ax.legend(handles=[pass_patch, fail_patch], fontsize=9)
        ax.grid(alpha=0.4, zorder=0)
        fig.tight_layout()
        out = images_dir / "fig4_check_g_accuracy.png"
        fig.savefig(out, dpi=DPI)
        plt.close(fig)
        saved.append(str(out))
        print(f"[INFO] Saved chart: {out}")

    return saved


def main() -> None:
    global ET
    import xml.etree.ElementTree as ET

    parser = argparse.ArgumentParser(description="Verify conformity of a PTB DCC XML certificate.")
    parser.add_argument("--xml",    type=Path, required=True)
    parser.add_argument(
        "--sensor", type=Path,
        default=Path(__file__).resolve().parent.parent / "models_in" / "sensors" / "ntc_temperature.json",
    )
    parser.add_argument("--mae",           type=float, default=0.10)
    parser.add_argument("--pfa-threshold", type=float, default=20.0)
    parser.add_argument("--u-ref",         type=float, default=0.065)
    parser.add_argument(
        "--images-dir", type=Path, default=None,
        help="Directory to save verification charts (PNG). If omitted, no charts are saved.",
    )
    args = parser.parse_args()

    if not args.xml.exists():
        print(f"[ERROR] XML file not found at: {args.xml}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading and parsing DCC XML file: {args.xml}...")
    t_ref, t_sensor, me_pre, me_post, u_sensor = parse_dcc_xml(args.xml)

    accuracy_ranges = load_sensor_accuracy_ranges(args.sensor)

    results = run_checks(
        t_ref=t_ref, t_sensor=t_sensor, me_pre=me_pre, u_sensor=u_sensor,
        accuracy_ranges=accuracy_ranges, mae=args.mae,
        pfa_threshold_pct=args.pfa_threshold, u_ref=args.u_ref,
    )

    print_results_report(
        xml_path=args.xml, t_ref=t_ref, t_sensor=t_sensor,
        me_pre=me_pre, me_post=me_post, u_sensor=u_sensor,
        results=results, mae=args.mae, pfa_threshold=args.pfa_threshold, u_ref=args.u_ref,
    )

    if args.images_dir is not None:
        print(f"\n[INFO] Saving charts to: {args.images_dir}")
        saved = save_charts(
            images_dir=args.images_dir,
            t_ref=t_ref, t_sensor=t_sensor,
            me_pre=me_pre, me_post=me_post, u_sensor=u_sensor,
            results=results, mae=args.mae,
            pfa_threshold_pct=args.pfa_threshold, u_ref=args.u_ref,
        )
        print(f"[INFO] {len(saved)} chart(s) saved.")


if __name__ == "__main__":
    main()
