from __future__ import annotations

import argparse
import json
import math
import re
import sys
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as _scipy_stats

K_COPERTURA: float  = 2.0
CONF_LEVEL_PCT: float = 95.0
U_PT_DEGC: float    = 0.065
K_PT: float         = 2.0
D_TMP126_DEGC: float = 0.30
ADC_BITS: int       = 16
ADC_MAX: float      = float((1 << ADC_BITS) - 1)

EPSILON_A: float      = 1e-6
EPSILON_B_DEGC: float = 1e-4

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

_W = 60


def _hr(char: str = "=") -> str:
    return char * _W


def _lsb_to_degc(lsb: float, min_phys: float, max_phys: float) -> float:
    return min_phys + (lsb / ADC_MAX) * (max_phys - min_phys)


def _degc_to_lsb(degc: float, min_phys: float, max_phys: float) -> float:
    return (degc - min_phys) / (max_phys - min_phys) * ADC_MAX


def _parse_limit(limit_str: str) -> Optional[float]:
    m = re.search(r"([\d.]+)", limit_str)
    return float(m.group(1)) if m else None


def _ols(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    n = len(x)
    if n < 2:
        raise ValueError("Servono almeno 2 punti per OLS")
    x_m = np.mean(x)
    y_m = np.mean(y)
    den = np.sum((x - x_m) ** 2)
    if np.isclose(den, 0.0):
        raise ValueError("Denominatore OLS nullo")
    a = np.sum((x - x_m) * (y - y_m)) / den
    b = y_m - a * x_m
    return float(a), float(b)


def _status_line(label: str, status: str, detail: str = "") -> str:
    dots = "." * max(1, _W - len(label) - len(status) - len(detail) - 4)
    return f"  {label} {dots} [{status}]  {detail}".rstrip()


def load_filled(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_calib(filled: Dict[str, Any]) -> Dict[str, Any]:
    cr = filled.get("_calibration_result", {})
    if not cr:
        raise ValueError("Chiave '_calibration_result' assente nel JSON filled")
    return cr


def extract_measurements(filled: Dict[str, Any]) -> List[List[float]]:
    ccv  = filled["template_parts"]["calculated_calibration_values"]
    rows = ccv.get("measurements", ccv.get("_measurements", []))
    result = []
    for row in rows:
        r = [float(v) for v in row]
        if len(r) == 5:
            r = [r[0], r[1], r[2], 0.0, r[3], r[4]]
        result.append(r)
    return result


def extract_ntc_model(filled: Dict[str, Any]) -> Dict[str, Any]:
    return filled["template_parts"]["sensor_method_template"].get("ntc_model", {})



#  core conformity checks


def check_A(measurements: List[List[float]], verbose: bool) -> Tuple[str, List[Dict]]:
    results = []
    for row in measurements:
        punto   = int(row[0])
        t_ref   = row[1]
        t_sensor = row[2]
        me_post = row[4]
        u_exp   = row[5]

        ok = abs(me_post) <= u_exp
        results.append({"punto": punto, "T_ref": t_ref, "T_sensor": t_sensor,
                         "M_e": me_post, "U_exp": u_exp, "pass": ok})

        if verbose:
            print(
                f"   Punto {punto}: T_ref={t_ref:.6f}degC  T_sensor={t_sensor:.6f}degC"
                f"  |M_e_post|={abs(me_post):.3e}degC  U(E)={u_exp:.4f}degC  => {'PASS' if ok else 'FAIL'}"
            )

    return (PASS if all(r["pass"] for r in results) else FAIL), results


def check_B(measurements: List[List[float]], limit_degc: float, verbose: bool) -> Tuple[str, List[Dict]]:
    results = []
    for row in measurements:
        punto = int(row[0])
        u_exp = row[5]
        ok    = u_exp <= limit_degc
        excess = u_exp - limit_degc
        results.append({"punto": punto, "U_exp": u_exp, "limit": limit_degc,
                         "excess": excess, "pass": ok})
        if verbose:
            detail = f"eccesso={excess:+.4f}degC" if not ok else ""
            print(f"   Punto {punto}: U(E)={u_exp:.4f}degC  limite={limit_degc:.4f}degC  => {'PASS' if ok else 'FAIL'}  {detail}")

    return (PASS if all(r["pass"] for r in results) else FAIL), results


def _max_error_for_temp(temp_degc: float, accuracy_ranges: List[Dict]) -> float:
    applicable = [
        r["maxError"]
        for r in accuracy_ranges
        if r["tempMin"] <= temp_degc <= r["tempMax"]
    ]
    return min(applicable) if applicable else float("inf")


def check_G(
    measurements: List[List[float]],
    accuracy_ranges: List[Dict],
    calib_model: str,
    verbose: bool,
) -> Tuple[str, Dict]:
    if not accuracy_ranges:
        return "N/A", {"status": "N/A", "note": "sensorAccuracy not present in sensor JSON", "per_point": []}

    per_point       = []
    g1_all_pass     = True
    g2_all_covered  = True

    for row in measurements:
        punto  = int(row[0])
        t_ref  = row[1]
        me_pre = row[3]

        max_err = _max_error_for_temp(t_ref, accuracy_ranges)
        covered = max_err < float("inf")

        if not covered:
            g2_all_covered = False
            g1_pass = True
            g2_pass = False
        else:
            g1_pass = abs(me_pre) <= max_err
            g2_pass = True
            if not g1_pass:
                g1_all_pass = False

        per_point.append({
            "punto": punto, "T_ref_degC": t_ref, "M_e_pre_degC": me_pre,
            "max_allowed_error_degC": max_err if covered else None,
            "G1_in_range": g1_pass, "G2_covered": g2_pass,
        })

        if verbose:
            cov_str = f"\u00b1{max_err:.4f}\u00b0C" if covered else "OUT_OF_COVERAGE"
            print(
                f"   Punto {punto}: T_ref={t_ref:.6f}\u00b0C  |M_e_pre|={abs(me_pre):.6f}\u00b0C  "
                f"limit={cov_str}  G1={'PASS' if g1_pass else 'FAIL'}  G2={'PASS' if g2_pass else 'NOT_COVERED'}"
            )

    if not g1_all_pass:
        overall = FAIL
    elif not g2_all_covered:
        overall = WARN
    else:
        overall = PASS

    if calib_model in ("cubic", "cube-log") and not g2_all_covered:
        note = (
            f"Regression model '{calib_model}': some calibration points fall outside "
            "all declared sensorAccuracy temperature ranges (extrapolation)."
        )
    elif calib_model in ("linear", "qubic-interpolation"):
        note = "Interpolation model: all calibration points are within the declared physical range."
    else:
        note = ""

    return overall, {
        "status": overall, "calib_model": calib_model, "note": note,
        "G1_all_in_range": g1_all_pass, "G2_all_covered": g2_all_covered,
        "per_point": per_point,
    }


def check_H(
    measurements: List[List[float]],
    mae_degc: float,
    pfa_threshold_pct: float,
    verbose: bool,
    u_std_mode: str = "combined",
    u_budget_per_step: Optional[List[Dict]] = None,
) -> Tuple[str, List[Dict]]:
    _valid_modes = ("combined", "type_a")
    if u_std_mode not in _valid_modes:
        raise ValueError(f"check_H: u_std_mode must be one of {_valid_modes!r}, got {u_std_mode!r}")

    effective_mode = u_std_mode
    if u_std_mode == "type_a":
        if not u_budget_per_step or len(u_budget_per_step) != len(measurements):
            effective_mode = "combined"
            if verbose:
                print("   [H] WARNING: type_a requested but budget missing/mismatched \u2014 falling back to combined.")

    pfa_threshold = pfa_threshold_pct / 100.0
    results: List[Dict] = []
    all_pass = True

    for idx, row in enumerate(measurements):
        punto  = int(row[0])
        t_ref  = row[1]
        me_pre = row[3]
        u_exp  = row[5]

        if effective_mode == "type_a":
            u_std = float(u_budget_per_step[idx]["uA_i_degC"])
        else:
            u_std = u_exp / K_COPERTURA

        u_ein = u_std / mae_degc
        ein   = me_pre / mae_degc

        if u_std > 0.0:
            pfa_i = (
                1.0 - _scipy_stats.norm.cdf(1.0, loc=ein, scale=u_ein)
                + _scipy_stats.norm.cdf(-1.0, loc=ein, scale=u_ein)
            )
        else:
            pfa_i = 0.0 if abs(ein) <= 1.0 else 1.0

        pfa_i = float(max(0.0, min(1.0, pfa_i)))
        ok    = pfa_i <= pfa_threshold
        if not ok:
            all_pass = False

        results.append({
            "punto": punto, "T_ref_degC": t_ref,
            "M_e_pre_degC": me_pre, "Ein": ein,
            "U_exp_degC": u_exp, "u_std_degC": u_std,
            "u_Ein": u_ein, "u_std_mode": effective_mode,
            "MAE_degC": mae_degc,
            "PFA_pct": pfa_i * 100.0, "PFA_threshold_pct": pfa_threshold_pct,
            "pass": ok,
        })

        if verbose:
            print(
                f"   Punto {punto}: T_ref={t_ref:.4f}\u00b0C  M_e_pre={me_pre:+.4f}\u00b0C  Ein={ein:+.3f}  "
                f"u(E)={u_std:.4f}\u00b0C [{effective_mode}]  PFA={pfa_i*100.0:.2f}%  => {'PASS' if ok else 'FAIL'}"
            )

    return (PASS if all_pass else FAIL), results



#  chart generation


def save_charts(
    measurements: List[List[float]],
    accuracy_ranges: List[Dict],
    limit_degc: float,
    variant: str,
    output_dir: Path,
    prefix: str = "conformity",
) -> List[Path]:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")
    mpl = importlib.import_module("matplotlib")
    mpl.rcParams.update({"font.size": 10})

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    punti    = [int(r[0]) for r in measurements]
    t_ref    = np.array([r[1] for r in measurements])
    me_pre   = np.array([r[3] for r in measurements])
    me_post  = np.array([r[4] for r in measurements])
    u_exp    = np.array([r[5] for r in measurements])

    saved: List[Path] = []

    # --- fig1: post-calibration residuals with U(E) bars and limit lines ---
    fig1, ax1 = plt.subplots(figsize=(9, 5))
    ax1.set_title(f"Check A/B \u2014 Residui post-calibrazione vs U(E) e limite dichiarato\n(variante: {variant})", fontsize=11)
    for i, p in enumerate(punti):
        ax1.fill_between([p - 0.35, p + 0.35], [-u_exp[i], -u_exp[i]], [u_exp[i], u_exp[i]],
                         color="green", alpha=0.15, label="Banda U(E)" if i == 0 else "")
    ax1.axhline( limit_degc, color="red", linestyle="--", linewidth=1.2, label=f"Limite +/-{limit_degc} degC")
    ax1.axhline(-limit_degc, color="red", linestyle="--", linewidth=1.2)
    ax1.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    ax1.errorbar(punti, me_post, yerr=u_exp, fmt="o", color="royalblue", ecolor="royalblue",
                 capsize=7, linewidth=1.5, markersize=6, label="|M_e_post| +/- U(E)")
    ax1.set_xticks(punti)
    ax1.set_xticklabels([f"P{p}\n({t_ref[i]:.2f}\u00b0C)" for i, p in enumerate(punti)])
    ax1.set_xlabel("Punto di calibrazione")
    ax1.set_ylabel("Errore M_e_post  [degC]")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)
    for i, p in enumerate(punti):
        ax1.annotate(f"U={u_exp[i]:.3f}degC", xy=(p, me_post[i]), xytext=(0, 14),
                     textcoords="offset points", ha="center", fontsize=8, color="royalblue")
    plt.tight_layout()
    p1 = output_dir / f"{prefix}_fig1_residuals.png"
    fig1.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    saved.append(p1)

    # --- fig2: as-found errors with sensorAccuracy limits (Check G visual) ---
    fig2, ax2 = plt.subplots(figsize=(9, 5))
    ax2.set_title(f"Check G \u2014 Errori as-found (pre-calibrazione) vs limiti sensorAccuracy\n(variante: {variant})", fontsize=11)

    x_pos = np.arange(len(punti))
    width = 0.40

    bars = ax2.bar(x_pos, me_pre, width, color="#4472C4", edgecolor="white", linewidth=0.8, label="M_e_pre (as-found)")

    if accuracy_ranges:
        for i, p in enumerate(punti):
            max_err = _max_error_for_temp(float(t_ref[i]), accuracy_ranges)
            if max_err < float("inf"):
                ax2.plot([x_pos[i] - width/2 - 0.05, x_pos[i] + width/2 + 0.05],
                         [max_err, max_err], color="red", linewidth=2.0, linestyle="--")
                ax2.plot([x_pos[i] - width/2 - 0.05, x_pos[i] + width/2 + 0.05],
                         [-max_err, -max_err], color="red", linewidth=2.0, linestyle="--")

        ax2.plot([], [], color="red", linewidth=2.0, linestyle="--", label="sensorAccuracy \u00b1maxError")

    ax2.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels([f"P{p}\n({t_ref[i]:.2f}\u00b0C)" for i, p in enumerate(punti)])
    ax2.set_xlabel("Punto di calibrazione")
    ax2.set_ylabel("Errore as-found M_e_pre  [degC]")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, alpha=0.3, axis="y")

    for i, (bar, val) in enumerate(zip(bars, me_pre)):
        color = "darkgreen" if abs(val) <= _max_error_for_temp(float(t_ref[i]), accuracy_ranges) else "darkred"
        ax2.text(bar.get_x() + bar.get_width()/2, val + (0.02 if val >= 0 else -0.06),
                 f"{val:+.4f}", ha="center", va="bottom" if val >= 0 else "top",
                 fontsize=8, color=color, fontweight="bold")

    plt.tight_layout()
    p2 = output_dir / f"{prefix}_fig2_asfound.png"
    fig2.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    saved.append(p2)

    return saved



#  report


def print_report(
    variant: str,
    input_path: Path,
    check_results: Dict[str, Tuple[str, Any]],
    measurements: List[List[float]],
    calib: Dict[str, Any],
    limit_degc: float,
    min_phys: float,
    max_phys: float,
    mae_degc: float = 0.10,
    pfa_threshold_pct: float = 20.0,
) -> None:
    lsb_per_c = ADC_MAX / (max_phys - min_phys)

    print()
    print(_hr("="))
    print(f"  VERIFICA CONFORMITA' -- CERTIFICATO DI TARATURA NTC")
    print(f"  Variante : {variant.upper()}")
    print(f"  File     : {input_path.name}")
    print(f"  Punti    : {len(measurements)}")
    print(_hr("="))

    print()
    print("  TABELLA MISURE")
    print(
        f"  {'Punto':>5}  {'T_ref [degC]':>12}  {'T_c_post [degC]':>15}  "
        f"{'M_e_pre [degC]':>14}  {'M_e_post [degC]':>15}  {'U(E) [degC]':>10}"
    )
    print(f"  {'-'*5}  {'-'*12}  {'-'*15}  {'-'*14}  {'-'*15}  {'-'*10}")
    for row in measurements:
        print(
            f"  {int(row[0]):>5}  {row[1]:>12.6f}  {row[2]:>15.6f}  "
            f"{row[3]:>14.3e}  {row[4]:>15.3e}  {row[5]:>10.4f}"
        )

    A      = calib["_A"]
    B      = calib["_B"]
    u_A    = calib["_u_A"]
    u_B    = calib["_u_B"]
    cov_AB = calib["_cov_AB"]
    print()
    print("  COEFFICIENTI DI CALIBRAZIONE (OLS GUM, dominio LSB)")
    print(f"    A       = {A:.10f}              (adimensionale)")
    print(f"    B       = {B:.4f} LSB  =  {B/lsb_per_c:.6f} degC")
    print(f"    u(A)    = {u_A:.10f}")
    print(f"    u(B)    = {u_B:.4f} LSB  =  {u_B/lsb_per_c:.6f} degC")
    print(f"    cov(AB) = {cov_AB:.6f}")
    if u_A > 0 and u_B > 0:
        print(f"    corr    = {cov_AB/(u_A*u_B):.6f}")

    print()
    print(_hr("\u2500"))
    print("  RISULTATI CHECK")
    print(_hr("\u2500"))

    for label, (status, _detail) in check_results.items():
        detail_str = ""
        if label == "H":
            detail_str = f"MAE=\u00b1{mae_degc:.3f}\u00b0C  soglia={pfa_threshold_pct:.0f}%"
        elif label == "B":
            detail_str = f"limite={limit_degc:.4f}\u00b0C"
        print(_status_line(f"Check {label}", status, detail_str))

    statuses = {lbl: res[0] for lbl, res in check_results.items()}
    checks_for_overall = ["G", "A", "B", "H"]
    overall = "CONFORME" if all(statuses.get(c, "FAIL") == "PASS" for c in checks_for_overall) else "NON CONFORME"

    print(_hr("\u2500"))
    print(f"  ESITO COMPLESSIVO: {overall}")
    print(_hr("="))
    print()



#  main


def main() -> None:
    SCRIPTS_DIR = Path(__file__).resolve().parent
    CALIB_ROOT  = SCRIPTS_DIR.parent
    MODELS_DIR  = CALIB_ROOT / "models_in"
    OUT_DIR     = CALIB_ROOT / "certificato_out"

    for p in (str(SCRIPTS_DIR), str(MODELS_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from VAR_REF_SENSOR import SENSOR_model

    DEFAULT_MAE_DEGC            = 0.30
    DEFAULT_PFA_THRESHOLD_PCT   = 20.0
    DEFAULT_PFA_U_STD_MODE      = "combined"

    parser = argparse.ArgumentParser(description="Conformity checker for NTC calibration certificate.")
    parser.add_argument("--input", type=Path,
                        default=OUT_DIR / "certificato_funzione_filled.json")
    parser.add_argument("--sensor", type=Path,
                        default=MODELS_DIR / "ntc_temperature.json")
    parser.add_argument("--variant", choices=["funzione", "both"], default="funzione")
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mae-degc",          type=float, default=DEFAULT_MAE_DEGC)
    parser.add_argument("--pfa-threshold-pct", type=float, default=DEFAULT_PFA_THRESHOLD_PCT)
    parser.add_argument("--pfa-u-std-mode",    type=str,   default=DEFAULT_PFA_U_STD_MODE,
                        choices=["combined", "type_a"])
    parser.add_argument("--charts", action=argparse.BooleanOptionalAction, default=False,
                        help="Save conformity charts (fig1 residuals, fig2 as-found errors)")
    parser.add_argument("--images-dir", type=Path, default=None,
                        help="Output directory for charts (default: certificato_out/images/conformity)")
    args = parser.parse_args()

    sensor = SENSOR_model.from_json(args.sensor)
    sensor_json = sensor._data

    accuracy_ranges = sensor_json.get("metrology", {}).get("sensorAccuracy", [])
    filled = load_filled(args.input)

    calib = extract_calib(filled)
    measurements = extract_measurements(filled)

    limit_degc = _parse_limit(sensor.uncertainty_limit) or 0.10
    min_phys   = sensor._minPhyThreshold
    max_phys   = sensor._maxPhyThreshold

    conf_model = calib.get("_calib_model", "linear")

    sG, rG = check_G(measurements, accuracy_ranges, conf_model, verbose=args.verbose)
    sA, rA = check_A(measurements, verbose=args.verbose)
    sB, rB = check_B(measurements, limit_degc, verbose=args.verbose)

    u_budget = calib.get("_u_budget_per_step", [])
    sH, rH = check_H(
        measurements, mae_degc=args.mae_degc,
        pfa_threshold_pct=args.pfa_threshold_pct,
        verbose=args.verbose, u_std_mode=args.pfa_u_std_mode,
        u_budget_per_step=u_budget,
    )

    check_results = {"G": (sG, rG), "A": (sA, rA), "B": (sB, rB), "H": (sH, rH)}
    print_report(
        variant="funzione", input_path=args.input,
        check_results=check_results, measurements=measurements,
        calib=calib, limit_degc=limit_degc,
        min_phys=min_phys, max_phys=max_phys,
        mae_degc=args.mae_degc, pfa_threshold_pct=args.pfa_threshold_pct,
    )

    if args.charts:
        images_dir = args.images_dir or (OUT_DIR / "images" / "conformity")
        saved = save_charts(
            measurements=measurements,
            accuracy_ranges=accuracy_ranges,
            limit_degc=limit_degc,
            variant="funzione",
            output_dir=images_dir,
        )
        if args.verbose:
            for p in saved:
                print(f"Chart saved: {p}")


if __name__ == "__main__":
    main()
