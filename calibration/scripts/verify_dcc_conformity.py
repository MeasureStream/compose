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


def _erfinv_manual(z: float) -> float:
    if z <= -1.0:
        return -float("inf")
    if z >= 1.0:
        return float("inf")
    a = 0.147
    ln1z2 = math.log(1.0 - z * z)
    inner = (2.0 / (math.pi * a) + ln1z2 / 2.0)
    x = math.sqrt(math.sqrt(inner * inner - ln1z2 / a) - inner)
    x = math.copysign(x, z)
    sp = math.sqrt(math.pi)
    for _ in range(3):
        er = math.erf(x)
        x = x - (er - z) * sp * math.exp(x * x) / 2.0
    return x


def inverse_normal_cdf(p: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
    return mu + sigma * math.sqrt(2.0) * _erfinv_manual(2.0 * p - 1.0)


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


def load_sensor_max_tollerance(sensor_path: Path) -> float | None:
    if not sensor_path.exists():
        print(f"[WARNING] Sensor model file not found at: {sensor_path}")
        return None
    try:
        data = json.loads(sensor_path.read_text(encoding="utf-8"))
        unc = data.get("metrology", {}).get("Uncertainty", [])
        for item in unc:
            if item.get("varName") == "maxTollerance":
                return float(item.get("value", 0))
        legacy = data.get("metrology", {}).get("sensorAccuracy", [])
        if legacy:
            return float(legacy[0].get("maxError", 0))
        return None
    except Exception as e:
        print(f"[WARNING] Failed to load sensor accuracy from {sensor_path}: {e}")
        return None


def load_sensor_coverage_factor(sensor_path: Path) -> float:
    if not sensor_path.exists():
        return 2.0
    try:
        data = json.loads(sensor_path.read_text(encoding="utf-8"))
        ru = data.get("metrology", {}).get("readingUncertainty", [])
        for item in ru:
            if item.get("varName") == "coverageFactor":
                return float(item.get("value", 2.0))
        unc = data.get("metrology", {}).get("Uncertainty", [])
        for item in unc:
            k = item.get("k")
            if k is not None:
                return float(k)
        return 2.0
    except Exception:
        return 2.0


def run_checks(
    t_ref: List[float],
    me_pre: List[float],
    u_sensor: List[float],
    mae: float,
    pfa_threshold_pct: float,
    coverage_factor: float = 2.0,
) -> Dict[str, Any]:
    """Conformity is decided solely by Check H (Probability of False
    Acceptance) on the as-found errors, guard-banded against the declared
    MAE. A point passes iff its as-found error falls within the reduced
    acceptance limits [AL, AU]; the run is conforming iff every point
    passes. No overlap check, no plain max-tolerance check.
    """
    n = len(t_ref)
    pfa_threshold = pfa_threshold_pct / 100.0
    # Guard-band factor k_w = Φ⁻¹(1 − PFA_acc), constant across the run.
    k_w = inverse_normal_cdf(1.0 - pfa_threshold)

    h_all_pass = True
    h_details: List[Dict[str, Any]] = []

    for i in range(n):
        error_val = me_pre[i]
        u_std     = u_sensor[i] / coverage_factor
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

        # Reduced acceptance limits (guard-banded), same units as me_pre.
        al_y = -mae + k_w * u_std
        au_y =  mae - k_w * u_std
        within_guard_band = al_y <= error_val <= au_y

        h_details.append({
            "index": i + 1, "t_ref": t_ref[i], "me_pre": error_val,
            "u_std": u_std, "Ein": ein, "pfa_pct": pfa * 100.0,
            "AL": al_y, "AU": au_y, "within_guard_band": within_guard_band,
            "pass": ok,
        })

    return {
        "check_h": {
            "status": PASS if h_all_pass else FAIL,
            "details": h_details,
            "k_w": k_w, "mae": mae, "pfa_threshold_pct": pfa_threshold_pct,
        },
    }


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

    # Conformity is decided solely by Check H: as-found error vs guard-banded
    # acceptance limits [AL, AU], driven by PFA <= threshold. No overlap
    # check, no plain max-tolerance check.
    h_res = results["check_h"]
    k_w   = h_res["k_w"]
    print("\n" + _hr("-"))
    print(f"  [H] PROBABILITY OF FALSE ACCEPTANCE (PFA) + GUARD BAND")
    print(f"      MAE = +/-{mae:.3f},  PFA Acceptance Threshold = {pfa_threshold:.1f} %,  "
          f"k_w = invCDF(1-alpha) = {k_w:.4f}")
    print(_hr("-"))
    print(
        f"  {'Pt':>3}  {'T_ref':>12}  {'M_e_pre':>13}  {'u_std':>10}  {'Ein':>8}  "
        f"{'AL':>10}  {'AU':>10}  {'PFA [%]':>9}  {'Verdict':>10}"
    )
    print(f"  {'-'*3}  {'-'*12}  {'-'*13}  {'-'*10}  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*9}  {'-'*10}")
    for pt in h_res["details"]:
        print(
            f"  {pt['index']:>3}  {pt['t_ref']:>12.4f}  {pt['me_pre']:>13.4f}  {pt['u_std']:>10.4f}  "
            f"{pt['Ein']:>+8.3f}  "
            f"{pt['AL']:>+10.4f}  {pt['AU']:>+10.4f}  {pt['pfa_pct']:>8.1f}%  {PASS if pt['pass'] else FAIL:>10}"
        )
    print(f"\n  Check H Verdict: [{h_res['status']}]")

    print("\n" + _hr("="))
    overall = "CONFORMING" if h_res["status"] == PASS else "NON-CONFORMING"
    print(f"  OVERALL VERDICT: {overall}")
    print(f"  [H] PFA / Guard band: [{h_res['status']}]")
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

    DPI = 75
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

    # ── Fig 2: as-found error vs guard-banded acceptance limits [AL, AU] ──
    if h_res["details"]:
        fig, ax = plt.subplots(figsize=(10, 5))
        h_details = h_res["details"]
        t_arr = [d["t_ref"] for d in h_details]

        for d in h_details:
            color  = COLOR_PASS if d["pass"] else COLOR_FAIL
            marker = "o" if d["pass"] else "X"
            ax.scatter(d["t_ref"], d["me_pre"], color=color, marker=marker,
                       s=80, zorder=5, linewidths=1.5, edgecolors="white")

        al_vals = [d["AL"] for d in h_details]
        au_vals = [d["AU"] for d in h_details]
        ax.plot(t_arr, al_vals, color=COLOR_FAIL, linewidth=1.2, linestyle="--",
                alpha=0.85, zorder=2, label="Guard-band limit AL")
        ax.plot(t_arr, au_vals, color=COLOR_FAIL, linewidth=1.2, linestyle="--",
                alpha=0.85, zorder=2, label="Guard-band limit AU")
        ax.axhspan(-mae, mae, facecolor=COLOR_WARN, alpha=0.06, zorder=1,
                   label=f"MAE = ±{mae:.3f} {UNIT}")
        ax.axhline(0, color="black", linewidth=0.8, zorder=2)

        ax.set_xlabel(f"Reference Temperature [{UNIT}]", fontsize=11)
        ax.set_ylabel(f"As-Found Error M_e_pre [{UNIT}]", fontsize=11)
        ax.set_title(f"[H] As-Found Error vs Guard Band [AL, AU] — {model_label} ({variant})",
                     fontsize=13, fontweight="bold")

        pass_patch = mpatches.Patch(color=COLOR_PASS, label="Within guard band (PASS)")
        fail_patch = mpatches.Patch(color=COLOR_FAIL, label="Outside guard band (FAIL)")
        ax.legend(handles=[pass_patch, fail_patch,
                            mpatches.Patch(color=COLOR_FAIL, label="AL / AU limit")],
                  fontsize=9, loc="best")
        ax.grid(alpha=0.4, zorder=0)
        fig.tight_layout()
        out = images_dir / "fig2_guard_band.png"
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
    parser.add_argument(
        "--u-ref", type=float, default=None,
        help="Deprecated, ignored. Kept for backward CLI compatibility with "
             "callers that still pass --u-ref (the overlap check that used "
             "it was removed; conformity now depends solely on Check H).",
    )
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

    coverage_factor = load_sensor_coverage_factor(args.sensor)

    results = run_checks(
        t_ref=t_ref, me_pre=me_pre, u_sensor=u_sensor,
        mae=args.mae, pfa_threshold_pct=args.pfa_threshold,
        coverage_factor=coverage_factor,
    )

    print_results_report(
        xml_path=args.xml, t_ref=t_ref, t_sensor=t_sensor,
        me_pre=me_pre, me_post=me_post, u_sensor=u_sensor,
        results=results, mae=args.mae, pfa_threshold=args.pfa_threshold,
    )

    if args.images_dir is not None:
        print(f"\n[INFO] Saving charts to: {args.images_dir}")
        saved = save_charts(
            images_dir=args.images_dir,
            t_ref=t_ref, t_sensor=t_sensor,
            me_pre=me_pre, me_post=me_post, u_sensor=u_sensor,
            results=results, mae=args.mae,
            pfa_threshold_pct=args.pfa_threshold,
        )
        print(f"[INFO] {len(saved)} chart(s) saved.")


if __name__ == "__main__":
    main()
