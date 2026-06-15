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

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

_W = 60


def _hr(char: str = "=") -> str:
    return char * _W


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
                f"   Punto {punto}: T_ref={t_ref:.6f}  T_sensor={t_sensor:.6f}"
                f"  |M_e_post|={abs(me_post):.3e}  U(E)={u_exp:.4f}  => {'PASS' if ok else 'FAIL'}"
            )

    return (PASS if all(r["pass"] for r in results) else FAIL), results


def check_B(measurements: List[List[float]], limit_y: float, verbose: bool) -> Tuple[str, List[Dict]]:
    results = []
    for row in measurements:
        punto = int(row[0])
        u_exp = row[5]
        ok    = u_exp <= limit_y
        excess = u_exp - limit_y
        results.append({"punto": punto, "U_exp": u_exp, "limit": limit_y,
                         "excess": excess, "pass": ok})
        if verbose:
            detail = f"excess={excess:+.4f}" if not ok else ""
            print(f"   Punto {punto}: U(E)={u_exp:.4f}  limit={limit_y:.4f}  => {'PASS' if ok else 'FAIL'}  {detail}")

    return (PASS if all(r["pass"] for r in results) else FAIL), results


def _max_error_for_temp(temp_y: float, max_tollerance: float | None) -> float:
    if max_tollerance is not None:
        return max_tollerance
    return float("inf")


def check_G(
    measurements: List[List[float]],
    max_tollerance: float | None,
    calib_model: str,
    verbose: bool,
) -> Tuple[str, Dict]:
    if max_tollerance is None:
        return "N/A", {"status": "N/A", "note": "maxTollerance not present in sensor JSON", "per_point": []}

    per_point       = []
    g1_all_pass     = True

    for row in measurements:
        punto  = int(row[0])
        t_ref  = row[1]
        me_pre = row[3]

        max_err = max_tollerance
        g1_pass = abs(me_pre) <= max_err
        if not g1_pass:
            g1_all_pass = False

        per_point.append({
            "punto": punto, "T_ref_y": t_ref, "M_e_pre_y": me_pre,
            "max_allowed_error_y": max_err,
            "G1_in_range": g1_pass, "G2_covered": True,
        })

        if verbose:
            print(
                f"   Punto {punto}: T_ref={t_ref:.6f}  |M_e_pre|={abs(me_pre):.6f}  "
                f"limit=\u00b1{max_err:.4f}  G1={'PASS' if g1_pass else 'FAIL'}"
            )

    overall = PASS if g1_all_pass else FAIL

    note = "Interpolation model: all calibration points are within the declared tolerance."

    return overall, {
        "status": overall, "calib_model": calib_model, "note": note,
        "G1_all_in_range": g1_all_pass, "G2_all_covered": True,
        "per_point": per_point,
    }


def check_H(
    measurements: List[List[float]],
    mae_y: float,
    pfa_threshold_pct: float,
    verbose: bool,
    u_std_mode: str = "combined",
    u_budget_per_step: Optional[List[Dict]] = None,
    coverage_factor: float = 2.0,
    adc_bits: int = 16,
    adc_max: float = 65535.0,
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

    _warned_lsb = False
    for idx, row in enumerate(measurements):
        punto  = int(row[0])
        t_ref  = row[1]
        me_pre = row[3]
        u_exp  = row[5]

        if (abs(me_pre) > 1e3 or abs(u_exp) > 1e3) and not _warned_lsb:
            _warned_lsb = True
            import sys
            print(
                f"\n*** [H] WARNING: M_e_pre=±{abs(me_pre):.1f} U_exp={u_exp:.1f} "
                f"— values appear to be in LSB adc domain [{adc_bits}-bit, 0–{adc_max:.0f}].\n"
                f"*** [H] Check that measurements rows were converted to physical domain "
                f"before calling check_H.\n",
                file=sys.stderr,
            )

        if effective_mode == "type_a":
            u_std = float(u_budget_per_step[idx]["uA_sensor"])
        else:
            u_std = u_exp / coverage_factor

        u_ein = u_std / mae_y
        ein   = me_pre / mae_y

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
            "punto": punto, "T_ref_y": t_ref,
            "M_e_pre_y": me_pre, "Ein": ein,
            "U_exp_y": u_exp, "u_std_y": u_std,
            "u_Ein": u_ein, "u_std_mode": effective_mode,
            "MAE_y": mae_y,
            "PFA_pct": pfa_i * 100.0, "PFA_threshold_pct": pfa_threshold_pct,
            "pass": ok,
        })

        if verbose:
            print(
                f"   Punto {punto}: T_ref={t_ref:.4f}  M_e_pre={me_pre:+.4f}  Ein={ein:+.3f}  "
                f"u(E)={u_std:.4f} [{effective_mode}]  PFA={pfa_i*100.0:.2f}%  => {'PASS' if ok else 'FAIL'}"
            )

    return (PASS if all_pass else FAIL), results



#  chart generation


def save_charts(
    measurements: List[List[float]],
    accuracy_ranges: List[Dict],
    limit_y: float,
    variant: str,
    output_dir: Path,
    prefix: str = "conformity",
    unit_symbol: str = "\u00b0C",
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

    # --- fig1: post-calibration residuals with U(E) bars ---
    fig1, ax1 = plt.subplots(figsize=(9, 5))
    ax1.set_title(f"Check A/B \u2014 Post-calibration residuals vs U(E)", fontsize=11)
    for i, p in enumerate(punti):
        ax1.fill_between([p - 0.35, p + 0.35], [-u_exp[i], -u_exp[i]], [u_exp[i], u_exp[i]],
                         color="green", alpha=0.15, label="U(E) band" if i == 0 else "")
    ax1.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    ax1.errorbar(punti, me_post, yerr=u_exp, fmt="o", color="royalblue", ecolor="royalblue",
                 capsize=7, linewidth=1.5, markersize=6, label="|M_e_post| +/- U(E)")
    ax1.set_xticks(punti)
    ax1.set_xticklabels([f"P{p}\n({t_ref[i]:.2f} {unit_symbol})" for i, p in enumerate(punti)])
    ax1.set_xlabel("Calibration point")
    ax1.set_ylabel(f"Error M_e_post  [{unit_symbol}]")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)
    for i, p in enumerate(punti):
        ax1.annotate(f"U={u_exp[i]:.3f}", xy=(p, me_post[i]), xytext=(0, 14),
                     textcoords="offset points", ha="center", fontsize=8, color="royalblue")
    plt.tight_layout()
    p1 = output_dir / f"{prefix}_fig1_residuals.png"
    fig1.savefig(p1, dpi=75, bbox_inches="tight")
    plt.close(fig1)
    saved.append(p1)

    # --- fig2: as-found errors with sensorAccuracy limits (Check G visual) ---
    # DISABLED: only fig1 (post-calibration residuals) is generated.
    # fig2, ax2 = plt.subplots(figsize=(9, 5))
    # ax2.set_title(f"Check G \u2014 As-found errors (pre-calibration) vs tolerance", fontsize=11)
    #
    # x_pos = np.arange(len(punti))
    # width = 0.40
    #
    # bars = ax2.bar(x_pos, me_pre, width, color="#4472C4", edgecolor="white", linewidth=0.8, label="M_e_pre (as-found)")
    #
    # if accuracy_ranges is not None:
    #     for i, p in enumerate(punti):
    #         max_err = float(accuracy_ranges) if isinstance(accuracy_ranges, (int, float)) else _max_error_for_temp(float(t_ref[i]), accuracy_ranges)
    #         if max_err < float("inf"):
    #             ax2.plot([x_pos[i] - width/2 - 0.05, x_pos[i] + width/2 + 0.05],
    #                      [max_err, max_err], color="red", linewidth=2.0, linestyle="--")
    #             ax2.plot([x_pos[i] - width/2 - 0.05, x_pos[i] + width/2 + 0.05],
    #                      [-max_err, -max_err], color="red", linewidth=2.0, linestyle="--")
    #
    #     ax2.plot([], [], color="red", linewidth=2.0, linestyle="--", label="tolerance")
    #
    # ax2.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    # ax2.set_xticks(x_pos)
    # ax2.set_xticklabels([f"P{p}\n({t_ref[i]:.2f} {unit_symbol})" for i, p in enumerate(punti)], fontsize=8)
    # ax2.set_xlabel("Calibration point")
    # ax2.set_ylabel(f"Error as-found M_e_pre  [{unit_symbol}]")
    #
    # if len(me_pre) > 0:
    #     y_max = float(np.max(np.abs(me_pre)))
    #     if isinstance(accuracy_ranges, (int, float)):
    #         y_max = max(y_max, abs(float(accuracy_ranges)))
    #     elif accuracy_ranges:
    #         for r in accuracy_ranges:
    #             try:
    #                 y_max = max(y_max, abs(float(r.get("maxError", 0.0))))
    #             except (TypeError, ValueError):
    #                 pass
    #     ax2.set_ylim(-max(y_max, 1e-9) * 1.20, max(y_max, 1e-9) * 1.20)
    #
    # ax2.legend(loc="upper right", fontsize=9)
    # ax2.grid(True, alpha=0.3, axis="y")
    #
    # for i, (bar, val) in enumerate(zip(bars, me_pre)):
    #     if isinstance(accuracy_ranges, (int, float)):
    #         max_err_val = float(accuracy_ranges) if accuracy_ranges is not None else float("inf")
    #     else:
    #         max_err_val = _max_error_for_temp(float(t_ref[i]), accuracy_ranges)
    #     color = "darkgreen" if abs(val) <= max_err_val else "darkred"
    #     ax2.text(bar.get_x() + bar.get_width()/2, val + (0.02 if val >= 0 else -0.06),
    #              f"{val:+.4f}", ha="center", va="bottom" if val >= 0 else "top",
    #              fontsize=8, color=color, fontweight="bold")
    #
    # plt.tight_layout()
    # p2 = output_dir / f"{prefix}_fig2_asfound.png"
    # fig2.savefig(p2, dpi=75, bbox_inches="tight")
    # plt.close(fig2)
    # saved.append(p2)

    return saved



#  report


def print_report(
    variant: str,
    input_path: Path,
    check_results: Dict[str, Tuple[str, Any]],
    measurements: List[List[float]],
    calib: Dict[str, Any],
    limit_y: float,
    min_phys: float,
    max_phys: float,
    mae_y: float = 0.10,
    pfa_threshold_pct: float = 20.0,
    adc_max: float = 65535.0,
) -> None:
    lsb_per_y = adc_max / (max_phys - min_phys)

    print()
    print(_hr("="))
    print(f"  CONFORMITY CHECK -- NTC CALIBRATION CERTIFICATE")
    print(f"  Variante : {variant.upper()}")
    print(f"  File     : {input_path.name}")
    print(f"  Punti    : {len(measurements)}")
    print(_hr("="))

    print()
    print("  TABELLA MISURE")
    print(
        f"  {'Punto':>5}  {'T_ref':>12}  {'T_c_post':>15}  "
        f"{'M_e_pre':>14}  {'M_e_post':>15}  {'U(E)':>10}"
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
    print(f"    B       = {B:.4f} LSB  =  {B/lsb_per_y:.6f}")
    print(f"    u(A)    = {u_A:.10f}")
    print(f"    u(B)    = {u_B:.4f} LSB  =  {u_B/lsb_per_y:.6f}")
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
            detail_str = f"MAE=\u00b1{mae_y:.3f}  threshold={pfa_threshold_pct:.0f}%"
        elif label == "B":
            detail_str = f"limite={limit_y:.4f}"
        print(_status_line(f"Check {label}", status, detail_str))

    statuses = {lbl: res[0] for lbl, res in check_results.items()}
    checks_for_overall = ["G", "A", "B", "H"]
    overall = "PASS" if all(statuses.get(c, "FAIL") == "PASS" for c in checks_for_overall) else "FAIL"

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

    for p in (str(SCRIPTS_DIR),):
        if p not in sys.path:
            sys.path.insert(0, p)

    DEFAULT_MAE_Y            = 0.30
    DEFAULT_PFA_THRESHOLD_PCT   = 20.0
    DEFAULT_PFA_U_STD_MODE      = "combined"

    parser = argparse.ArgumentParser(description="Conformity checker for NTC calibration certificate.")
    parser.add_argument("--input", type=Path,
                        default=OUT_DIR / "certificato_funzione_filled.json")
    parser.add_argument("--sensor", type=Path,
                        default=MODELS_DIR / "sensors" / "ntc_temperature.json")
    parser.add_argument("--variant", choices=["funzione", "both"], default="funzione")
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mae-y",            type=float, default=DEFAULT_MAE_Y)
    parser.add_argument("--pfa-threshold-pct", type=float, default=DEFAULT_PFA_THRESHOLD_PCT)
    parser.add_argument("--pfa-u-std-mode",    type=str,   default=DEFAULT_PFA_U_STD_MODE,
                        choices=["combined", "type_a"])
    parser.add_argument("--charts", action=argparse.BooleanOptionalAction, default=False,
                        help="Save conformity charts (fig1 residuals, fig2 as-found errors)")
    parser.add_argument("--images-dir", type=Path, default=None,
                        help="Output directory for charts (default: certificato_out/images/conformity)")
    args = parser.parse_args()

    sensor_json = json.loads(Path(args.sensor).read_text(encoding="utf-8"))
    sensor_metrology = sensor_json.get("metrology", {})

    # Read maxTollerance from Uncertainty array (new format) or fallback to sensorAccuracy (legacy)
    unc = sensor_metrology.get("Uncertainty", [])
    max_tollerance = None
    for item in unc:
        if item.get("varName") == "maxTollerance":
            max_tollerance = float(item.get("value", 0))
            break
    if max_tollerance is None:
        legacy = sensor_metrology.get("sensorAccuracy", [])
        if legacy:
            max_tollerance = float(legacy[0].get("maxError", 0))

    # Extract coverage factor
    ru = sensor_metrology.get("readingUncertainty", [])
    coverage_factor = 2.0
    for item in ru:
        if item.get("varName") == "coverageFactor":
            coverage_factor = float(item.get("value", 2.0))
            break

    filled = load_filled(args.input)

    calib = extract_calib(filled)
    measurements = extract_measurements(filled)

    # Read absUncertainty from Uncertainty by varName or legacy
    limit_y = 0.10
    for item in unc:
        if item.get("varName") == "absUncertainty":
            limit_y = float(item.get("value", 0.10))
            break
    if not any(item.get("varName") == "absUncertainty" for item in unc) and unc:
        limit_y = float(unc[0].get("absUncertainty", 0.10))
    min_phys   = float(sensor_json.get("ranges", {}).get("threshold", {}).get("min", -40.0))
    max_phys   = float(sensor_json.get("ranges", {}).get("threshold", {}).get("max", 105.0))

    conf_model = calib.get("_calib_model", "linear")

    sG, rG = check_G(measurements, max_tollerance, conf_model, verbose=args.verbose)
    sA, rA = check_A(measurements, verbose=args.verbose)
    sB, rB = check_B(measurements, limit_y, verbose=args.verbose)

    u_budget = calib.get("_u_budget_per_step", [])
    sH, rH = check_H(
        measurements, mae_y=args.mae_y,
        pfa_threshold_pct=args.pfa_threshold_pct,
        verbose=args.verbose, u_std_mode=args.pfa_u_std_mode,
        u_budget_per_step=u_budget,
        adc_bits=16, adc_max=65535.0,
        coverage_factor=coverage_factor,
    )

    check_results = {"G": (sG, rG), "A": (sA, rA), "B": (sB, rB), "H": (sH, rH)}
    print_report(
        variant="funzione", input_path=args.input,
        check_results=check_results, measurements=measurements,
        calib=calib, limit_y=limit_y,
        min_phys=min_phys, max_phys=max_phys,
        mae_y=args.mae_y, pfa_threshold_pct=args.pfa_threshold_pct,
        adc_max=65535.0,
    )

    if args.charts:
        images_dir = args.images_dir or (OUT_DIR / "images" / "conformity")
        saved = save_charts(
            measurements=measurements,
            accuracy_ranges=max_tollerance,
            limit_y=limit_y,
            variant="funzione",
            output_dir=images_dir,
        )
        if args.verbose:
            for p in saved:
                print(f"Chart saved: {p}")


if __name__ == "__main__":
    main()
