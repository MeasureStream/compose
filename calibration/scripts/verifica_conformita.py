from __future__ import annotations

import argparse
import json
import math
import re
import sys
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
    # row format: [punto, T_ref, T_c_post, M_e_pre, M_e_post, U_exp]
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


def extract_notes(filled: Dict[str, Any]) -> List[str]:
    smt = filled["template_parts"]["sensor_method_template"]
    return smt.get("notes_template", smt.get("_notes_computed", []))


def check_A(measurements: List[List[float]], verbose: bool) -> Tuple[str, List[Dict]]:
    # verifies |M_e_post| <= U(E) at every calibration point
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
    # verifies U(E) <= declared limit at every point
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


def check_C(
    measurements: List[List[float]],
    A_cert: float,
    B_cert: float,
    min_phys: float,
    max_phys: float,
    verbose: bool,
) -> Tuple[str, Dict]:
    # checks internal consistency of M_e values (post-calibration residuals ~0)
    n = len(measurements)
    lsb_per_c = ADC_MAX / (max_phys - min_phys)

    residui  = [abs(r[2] - r[1]) for r in measurements]
    max_res  = max(residui)
    ok_res   = max_res < 1e-6

    result = {
        "n_punti": n, "trivial": True,
        "A_cert": A_cert, "B_cert": B_cert,
        "B_cert_degc": B_cert / lsb_per_c,
        "max_residuo_degc": max_res, "pass": ok_res,
        "note": (
            "Check limitato: il JSON filled contiene T_sensor post-calibrazione. "
            "Per un check genuino di A/B occorrono i dati grezzi LSB pre-calibrazione."
        ),
    }

    if verbose:
        print(f"   n punti = {n}  (tabella contiene valori post-calibrazione)")
        print(f"   A certificato = {A_cert:.10f}   B = {B_cert:.4f} LSB = {B_cert/lsb_per_c:.6f}degC")
        print(f"   Max |T_sensor - T_ref| = {max_res:.2e}degC  => {'PASS' if ok_res else 'FAIL'}")

    return (PASS if ok_res else FAIL), result


def check_D(
    measurements: List[List[float]],
    u_exp_list: List[float],
    min_phys: float,
    max_phys: float,
    resolution_degc: float,
    verbose: bool,
) -> Tuple[str, List[Dict]]:
    # decomposes u^2(E) into type-A and type-B components
    u_B_ref    = U_PT_DEGC / K_PT
    u_B_sensor = D_TMP126_DEGC / math.sqrt(3.0)
    u_res      = resolution_degc / math.sqrt(12.0)

    results = []
    all_ok  = True
    for i, row in enumerate(measurements):
        punto = int(row[0])
        u_exp = u_exp_list[i]
        u_std = u_exp / K_COPERTURA

        u_B2_sum = u_B_ref**2 + u_B_sensor**2 + u_res**2
        u_A2_est = max(0.0, u_std**2 - u_B2_sum)
        u_A_est  = math.sqrt(u_A2_est)

        u_ricostruita = math.sqrt(u_B2_sum + u_A_est**2)
        delta         = abs(u_ricostruita - u_std)
        u_B_only      = math.sqrt(u_B2_sum)
        ok = (u_A2_est >= 0.0) and (u_B_only <= u_std + 1e-12)

        u2_tot = u_std**2
        frac_B_ref    = 100.0 * u_B_ref**2    / u2_tot if u2_tot > 0 else 0.0
        frac_B_sensor = 100.0 * u_B_sensor**2 / u2_tot if u2_tot > 0 else 0.0
        frac_res      = 100.0 * u_res**2      / u2_tot if u2_tot > 0 else 0.0
        frac_A_est    = 100.0 * u_A2_est      / u2_tot if u2_tot > 0 else 0.0

        results.append({
            "punto": punto, "U_exp": u_exp, "u_std": u_std,
            "u_B_ref": u_B_ref, "u_B_sensor": u_B_sensor,
            "u_res": u_res, "u_A_est": u_A_est,
            "u_ricostruita": u_ricostruita, "delta": delta,
            "frac_B_ref_pct": frac_B_ref, "frac_B_sensor_pct": frac_B_sensor,
            "frac_res_pct": frac_res, "frac_A_est_pct": frac_A_est,
            "pass": ok,
        })
        if not ok:
            all_ok = False

    return (PASS if all_ok else FAIL), results


def check_E(
    notes: List[str],
    u_exp_list: List[float],
    verbose: bool,
) -> Tuple[str, Dict]:
    # verifies k=2 is declared in notes and uncertainties are physically plausible
    k_declared = any("k = 2" in n or "k=2" in n for n in notes)
    u_std_list = [u / K_COPERTURA for u in u_exp_list]
    plausible  = all(0.01 <= u <= 2.0 for u in u_std_list)
    all_ok     = k_declared and plausible

    result = {
        "k_declared_in_notes": k_declared,
        "k_nominal": K_COPERTURA,
        "conf_level_pct": CONF_LEVEL_PCT,
        "u_std_list": u_std_list,
        "plausible": plausible,
        "pass": all_ok,
    }

    if verbose:
        print(f"   k=2 dichiarato nelle note: {'SÌ' if k_declared else 'NO'}")
        for i, u_std in enumerate(u_std_list):
            print(f"   Punto {i+1}: U(E)={u_exp_list[i]:.4f}degC  u(E)={u_std:.6f}degC")

    return (PASS if all_ok else (WARN if k_declared else FAIL)), result


def check_F(
    measurements: List[List[float]],
    A: float,
    B: float,
    min_phys: float,
    max_phys: float,
    variant: str,
    verbose: bool,
) -> Tuple[str, List[Dict]]:
    # verifies M_e_post = T_sensor - T_ref and coefficient plausibility
    lsb_per_c = ADC_MAX / (max_phys - min_phys)
    results   = []
    all_ok    = True

    if variant == "funzione":
        B_degc       = B / lsb_per_c
        t_at_lsb0    = _lsb_to_degc(B, min_phys, max_phys)
        t_at_lsbmax  = _lsb_to_degc(A * ADC_MAX + B, min_phys, max_phys)
        ok_coeff = (A > 0 and -300.0 < B_degc < 300.0
                    and min(t_at_lsb0, t_at_lsbmax) < max_phys
                    and max(t_at_lsb0, t_at_lsbmax) > min_phys)
    else:
        ok_coeff = True

    for row in measurements:
        punto    = int(row[0])
        t_ref    = row[1]
        t_sensor = row[2]
        me_cert  = row[4]

        me_calc  = t_sensor - t_ref
        delta_me = abs(me_calc - me_cert)
        ok_me    = delta_me < 1e-10
        ok       = ok_me and ok_coeff
        if not ok:
            all_ok = False

        results.append({
            "punto": punto, "T_ref": t_ref, "T_sensor": t_sensor,
            "M_e_cert": me_cert, "M_e_calc": me_calc,
            "delta_me": delta_me, "ok_coeff": ok_coeff, "pass": ok,
        })

        if verbose:
            print(f"   Punto {punto}: M_e_cert={me_cert:.3e}  M_e_calc={me_calc:.3e}  Delta={delta_me:.2e}  => {'PASS' if ok_me else 'FAIL'}")

    return (PASS if all_ok else FAIL), results


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
    # G1: as-found error within sensorAccuracy maxError
    # G2: reference temperature covered by at least one sensorAccuracy range
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
            cov_str = f"±{max_err:.4f}°C" if covered else "OUT_OF_COVERAGE"
            print(
                f"   Punto {punto}: T_ref={t_ref:.6f}°C  |M_e_pre|={abs(me_pre):.6f}°C  "
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
    # PFA (Probability of False Acceptance) on as-found error per calibration point
    _valid_modes = ("combined", "type_a")
    if u_std_mode not in _valid_modes:
        raise ValueError(f"check_H: u_std_mode must be one of {_valid_modes!r}, got {u_std_mode!r}")

    effective_mode = u_std_mode
    if u_std_mode == "type_a":
        if not u_budget_per_step or len(u_budget_per_step) != len(measurements):
            effective_mode = "combined"
            if verbose:
                print("   [H] WARNING: type_a requested but budget missing/mismatched — falling back to combined.")

    pfa_threshold = pfa_threshold_pct / 100.0
    results: List[Dict] = []
    all_pass = True

    for idx, row in enumerate(measurements):
        punto  = int(row[0])
        t_ref  = row[1]
        me_pre = row[3]
        u_exp  = row[5]

        if effective_mode == "type_a":
            u_std = float(u_budget_per_step[idx]["uA_i_degC"])  # type: ignore[index]
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
                f"   Punto {punto}: T_ref={t_ref:.4f}°C  M_e_pre={me_pre:+.4f}°C  Ein={ein:+.3f}  "
                f"u(E)={u_std:.4f}°C [{effective_mode}]  PFA={pfa_i*100.0:.2f}%  => {'PASS' if ok else 'FAIL'}"
            )

    return (PASS if all_pass else FAIL), results


def plot_charts(
    measurements: List[List[float]],
    budget_results: List[Dict],
    A: float,
    B: float,
    u_A: float,
    u_B: float,
    cov_AB: float,
    min_phys: float,
    max_phys: float,
    limit_degc: float,
    variant: str,
    temp_nominali: List[float],
) -> None:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")
    mpl = importlib.import_module("matplotlib")
    mpl.rcParams.update({"font.size": 10})

    lsb_per_c = ADC_MAX / (max_phys - min_phys)

    punti    = [int(r[0]) for r in measurements]
    t_ref    = np.array([r[1] for r in measurements])
    t_sensor = np.array([r[2] for r in measurements])
    me       = np.array([r[4] for r in measurements])
    u_exp    = np.array([r[5] for r in measurements])

    fig1, ax1 = plt.subplots(figsize=(9, 5))
    ax1.set_title(f"Check A/B — Residui vs U(E) e limite dichiarato\n(variante: {variant})", fontsize=11)
    for i, p in enumerate(punti):
        ax1.fill_between([p - 0.35, p + 0.35], [-u_exp[i], -u_exp[i]], [u_exp[i], u_exp[i]],
                         color="green", alpha=0.15, label="Banda U(E)" if i == 0 else "")
    ax1.axhline(limit_degc, color="red", linestyle="--", linewidth=1.2,
                label=f"Limite dichiarato +/-{limit_degc} degC")
    ax1.axhline(-limit_degc, color="red", linestyle="--", linewidth=1.2)
    ax1.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    ax1.errorbar(punti, me, yerr=u_exp, fmt="o", color="royalblue", ecolor="royalblue",
                 capsize=7, linewidth=1.5, markersize=6, label="|M_e| +/- U(E)")
    ax1.set_xticks(punti)
    ax1.set_xticklabels([f"P{p}\n({t_ref[i]:.2f}degC)" for i, p in enumerate(punti)])
    ax1.set_xlabel("Punto di calibrazione")
    ax1.set_ylabel("Errore M_e  [degC]")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)
    for i, p in enumerate(punti):
        ax1.annotate(f"U={u_exp[i]:.3f}degC", xy=(p, me[i]), xytext=(0, 14),
                     textcoords="offset points", ha="center", fontsize=8, color="royalblue")
    plt.tight_layout()

    fig2, axes2 = plt.subplots(1, len(measurements), figsize=(4 * len(measurements) + 1, 6), sharey=False)
    if len(measurements) == 1:
        axes2 = [axes2]
    fig2.suptitle(f"Check D — Budget GUM: scomposizione varianza u^2(E)\n(variante: {variant})", fontsize=11)
    for i, (ax, res) in enumerate(zip(axes2, budget_results)):
        u_std = res["u_std"]
        components = {
            "u^2_B(ref)\nPT100/Fluke": res["u_B_ref"]**2,
            "u^2_B(sensor)\nNTC ADC":   res["u_B_sensor"]**2,
            "u^2_res\nrisoluzione":      res["u_res"]**2,
            "u^2_A(est)\ntipo A residuo": res["u_A_est"]**2,
        }
        values = np.array(list(components.values()))
        colors = ["#4472C4", "#ED7D31", "#A9D18E", "#FF6F6F"]
        fracs  = 100.0 * values / (u_std**2) if u_std > 0 else values * 0
        bottom = 0.0
        for j, (lbl, val) in enumerate(zip(components.keys(), values)):
            ax.bar(0, val, bottom=bottom, color=colors[j], label=lbl, width=0.5)
            if val > 0:
                ax.text(0, bottom + val / 2, f"{fracs[j]:.1f}%", ha="center", va="center",
                        fontsize=9, color="white" if fracs[j] > 8 else "black", fontweight="bold")
            bottom += val
        ax.set_title(f"Punto {res['punto']}\nT_ref={measurements[i][1]:.2f}degC", fontsize=9)
        ax.set_ylabel("Varianza u^2 [degC^2]" if i == 0 else "")
        ax.set_xticks([])
        ax.set_xlim(-0.4, 0.4)
        ax.axhline(u_std**2, color="black", linestyle="--", linewidth=1)
        ax.annotate(f"u(E)={u_std:.4f}degC\nU(E)={u_std*K_COPERTURA:.4f}degC",
                    xy=(0.02, 0.97), xycoords="axes fraction", va="top", ha="left", fontsize=8,
                    bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", ec="gray", alpha=0.8))
    handles, labels_leg = axes2[0].get_legend_handles_labels()
    fig2.legend(handles[:4], labels_leg[:4], loc="lower center", ncol=2, fontsize=9, bbox_to_anchor=(0.5, -0.02))
    plt.tight_layout(rect=[0, 0.08, 1, 1])

    fig3, ax3 = plt.subplots(figsize=(9, 5))
    ax3.set_title(f"Curva di calibrazione T = A·D + B  con banda GUM +/-U(ŷ)  [k=2]\n(variante: {variant})", fontsize=11)
    d_range    = np.linspace(0, ADC_MAX, 500)
    t_hat_lsb  = A * d_range + B
    t_hat_degc = _lsb_to_degc(t_hat_lsb, min_phys, max_phys)
    u2_yhat    = np.maximum(u_B**2 + (d_range**2) * u_A**2 + 2 * d_range * cov_AB, 0.0)
    U_yhat_degc = K_COPERTURA * np.sqrt(u2_yhat) / lsb_per_c
    ax3.plot(_lsb_to_degc(d_range, min_phys, max_phys), t_hat_degc, "b-", linewidth=1.8,
             label=f"T = {A:.6f}·D + {B/lsb_per_c:.4f}degC")
    ax3.fill_between(_lsb_to_degc(d_range, min_phys, max_phys),
                     t_hat_degc - U_yhat_degc, t_hat_degc + U_yhat_degc,
                     alpha=0.20, color="blue", label="Banda +/-U(ŷ) [k=2]")
    ax3.scatter(t_sensor, t_ref, color="red", zorder=5, s=60, label="Punti calibrazione")
    for i, p in enumerate(punti):
        ax3.annotate(f" P{p}", xy=(t_sensor[i], t_ref[i]), fontsize=8, color="red")
    all_temps = np.array([min_phys, max_phys])
    ax3.plot(all_temps, all_temps, "k--", linewidth=0.8, alpha=0.5, label="Ideale T_sensor=T_ref")
    ax3.set_xlabel("T_sensor (input NTC) [degC]")
    ax3.set_ylabel("T_ref (output calibrato) [degC]")
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


def save_charts(
    measurements: List[List[float]],
    budget_results: List[Dict],
    A: float,
    B: float,
    u_A: float,
    u_B: float,
    cov_AB: float,
    min_phys: float,
    max_phys: float,
    limit_degc: float,
    variant: str,
    temp_nominali: List[float],
    output_dir: Path,
    prefix: str = "conformity",
) -> List[Path]:
    import importlib
    plt = importlib.import_module("matplotlib.pyplot")
    mpl = importlib.import_module("matplotlib")
    mpl.rcParams.update({"font.size": 10})

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lsb_per_c = ADC_MAX / (max_phys - min_phys)

    punti    = [int(r[0]) for r in measurements]
    t_ref    = np.array([r[1] for r in measurements])
    t_sensor = np.array([r[2] for r in measurements])
    me       = np.array([r[4] for r in measurements])
    u_exp    = np.array([r[5] for r in measurements])

    saved: List[Path] = []

    fig1, ax1 = plt.subplots(figsize=(9, 5))
    ax1.set_title(f"Check A/B — Residui vs U(E) e limite dichiarato\n(variante: {variant})", fontsize=11)
    for i, p in enumerate(punti):
        ax1.fill_between([p - 0.35, p + 0.35], [-u_exp[i], -u_exp[i]], [u_exp[i], u_exp[i]],
                         color="green", alpha=0.15, label="Banda U(E)" if i == 0 else "")
    ax1.axhline( limit_degc, color="red", linestyle="--", linewidth=1.2, label=f"Limite +/-{limit_degc} degC")
    ax1.axhline(-limit_degc, color="red", linestyle="--", linewidth=1.2)
    ax1.axhline(0, color="black", linestyle="-", linewidth=0.7, alpha=0.5)
    ax1.errorbar(punti, me, yerr=u_exp, fmt="o", color="royalblue", ecolor="royalblue",
                 capsize=7, linewidth=1.5, markersize=6, label="|M_e| +/- U(E)")
    ax1.set_xticks(punti)
    ax1.set_xticklabels([f"P{p}\n({t_ref[i]:.2f}degC)" for i, p in enumerate(punti)])
    ax1.set_xlabel("Punto di calibrazione")
    ax1.set_ylabel("Errore M_e  [degC]")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)
    for i, p in enumerate(punti):
        ax1.annotate(f"U={u_exp[i]:.3f}degC", xy=(p, me[i]), xytext=(0, 14),
                     textcoords="offset points", ha="center", fontsize=8, color="royalblue")
    plt.tight_layout()
    p1 = output_dir / f"{prefix}_fig1_residuals.png"
    fig1.savefig(p1, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    saved.append(p1)

    fig2, axes2 = plt.subplots(1, len(measurements), figsize=(4 * len(measurements) + 1, 6), sharey=False)
    if len(measurements) == 1:
        axes2 = [axes2]
    fig2.suptitle(f"Check D — Budget GUM: scomposizione varianza u^2(E)\n(variante: {variant})", fontsize=11)
    for i, (ax, res) in enumerate(zip(axes2, budget_results)):
        u_std = res["u_std"]
        components = {
            "u^2_B(ref)\nPT100/Fluke":  res["u_B_ref"]**2,
            "u^2_B(sensor)\nNTC ADC":    res["u_B_sensor"]**2,
            "u^2_res\nrisoluzione":       res["u_res"]**2,
            "u^2_A(est)\ntipo A residuo": res["u_A_est"]**2,
        }
        values = np.array(list(components.values()))
        colors = ["#4472C4", "#ED7D31", "#A9D18E", "#FF6F6F"]
        fracs  = 100.0 * values / (u_std**2) if u_std > 0 else values * 0
        bottom = 0.0
        for j, (lbl, val) in enumerate(zip(components.keys(), values)):
            ax.bar(0, val, bottom=bottom, color=colors[j], label=lbl, width=0.5)
            if val > 0:
                ax.text(0, bottom + val / 2, f"{fracs[j]:.1f}%", ha="center", va="center",
                        fontsize=9, color="white" if fracs[j] > 8 else "black", fontweight="bold")
            bottom += val
        ax.set_title(f"Punto {res['punto']}\nT_ref={measurements[i][1]:.2f}degC", fontsize=9)
        ax.set_ylabel("Varianza u^2 [degC^2]" if i == 0 else "")
        ax.set_xticks([])
        ax.set_xlim(-0.4, 0.4)
        ax.axhline(u_std**2, color="black", linestyle="--", linewidth=1)
        ax.annotate(f"u(E)={u_std:.4f}degC\nU(E)={u_std*K_COPERTURA:.4f}degC",
                    xy=(0.02, 0.97), xycoords="axes fraction", va="top", ha="left", fontsize=8,
                    bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", ec="gray", alpha=0.8))
    handles, labels_leg = axes2[0].get_legend_handles_labels()
    fig2.legend(handles[:4], labels_leg[:4], loc="lower center", ncol=2, fontsize=9, bbox_to_anchor=(0.5, -0.02))
    plt.tight_layout(rect=[0, 0.08, 1, 1])
    p2 = output_dir / f"{prefix}_fig2_gum_budget.png"
    fig2.savefig(p2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    saved.append(p2)

    fig3, ax3 = plt.subplots(figsize=(9, 5))
    ax3.set_title(f"Curva di calibrazione T = A·D + B  con banda GUM +/-U(ŷ)  [k=2]\n(variante: {variant})", fontsize=11)
    d_range    = np.linspace(0, ADC_MAX, 500)
    t_hat_lsb  = A * d_range + B
    t_hat_degc = _lsb_to_degc(t_hat_lsb, min_phys, max_phys)
    u2_yhat    = np.maximum(u_B**2 + (d_range**2) * u_A**2 + 2 * d_range * cov_AB, 0.0)
    U_yhat_degc = K_COPERTURA * np.sqrt(u2_yhat) / lsb_per_c
    ax3.plot(_lsb_to_degc(d_range, min_phys, max_phys), t_hat_degc, "b-", linewidth=1.8,
             label=f"T = {A:.6f}·D + {B/lsb_per_c:.4f}degC")
    ax3.fill_between(_lsb_to_degc(d_range, min_phys, max_phys),
                     t_hat_degc - U_yhat_degc, t_hat_degc + U_yhat_degc,
                     alpha=0.20, color="blue", label="Banda +/-U(ŷ) [k=2]")
    ax3.scatter(t_sensor, t_ref, color="red", zorder=5, s=60, label="Punti calibrazione")
    for i, p in enumerate(punti):
        ax3.annotate(f" P{p}", xy=(t_sensor[i], t_ref[i]), fontsize=8, color="red")
    all_temps = np.array([min_phys, max_phys])
    ax3.plot(all_temps, all_temps, "k--", linewidth=0.8, alpha=0.5, label="Ideale T_sensor=T_ref")
    ax3.set_xlabel("T_sensor (input NTC) [degC]")
    ax3.set_ylabel("T_ref (output calibrato) [degC]")
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.3)
    plt.tight_layout()
    p3 = output_dir / f"{prefix}_fig3_calibration_curve.png"
    fig3.savefig(p3, dpi=150, bbox_inches="tight")
    plt.close(fig3)
    saved.append(p3)

    return saved


def print_report(
    variant: str,
    input_path: Path,
    check_results: Dict[str, Tuple[str, Any]],
    measurements: List[List[float]],
    calib: Dict[str, Any],
    limit_degc: float,
    budget_results: List[Dict],
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
    print(_hr("─"))
    print("  RISULTATI CHECK")
    print(_hr("─"))

    for label, (status, _detail) in check_results.items():
        detail_str = ""
        if label == "H":
            detail_str = f"MAE=±{mae_degc:.3f}°C  soglia={pfa_threshold_pct:.0f}%"
        elif label == "B":
            detail_str = f"limite={limit_degc:.4f}°C"
        print(_status_line(f"Check {label}", status, detail_str))

    statuses = {lbl: res[0] for lbl, res in check_results.items()}
    checks_for_overall = ["A", "B", "D", "E", "H"]
    if "C" in statuses and statuses["C"] == "PASS":
        checks_for_overall += ["C", "F"]
    overall = "CONFORME" if all(statuses.get(c, "FAIL") == "PASS" for c in checks_for_overall) else "NON CONFORME"

    print(_hr("─"))
    print(f"  ESITO COMPLESSIVO: {overall}")
    print(_hr("="))
    print()


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
    parser.add_argument("--charts",  action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mae-degc",          type=float, default=DEFAULT_MAE_DEGC)
    parser.add_argument("--pfa-threshold-pct", type=float, default=DEFAULT_PFA_THRESHOLD_PCT)
    parser.add_argument("--pfa-u-std-mode",    type=str,   default=DEFAULT_PFA_U_STD_MODE,
                        choices=["combined", "type_a"])
    args = parser.parse_args()

    sensor = SENSOR_model.from_json(args.sensor)
    sensor_json = sensor._data

    accuracy_ranges = sensor_json.get("metrology", {}).get("sensorAccuracy", [])
    filled = load_filled(args.input)

    calib = extract_calib(filled)
    measurements = extract_measurements(filled)
    notes = extract_notes(filled)

    limit_degc = _parse_limit(sensor.uncertainty_limit) or 0.10
    resolution = sensor.resolution_degC
    min_phys   = sensor._minPhyThreshold
    max_phys   = sensor._maxPhyThreshold

    u_exp_list = calib["_expanded_uncertainties_degC"]
    temp_nom   = calib.get("_temp_nominali", [])
    conf_model = calib.get("_calib_model", "linear")

    sG, rG = check_G(measurements, accuracy_ranges, conf_model, verbose=args.verbose)
    sA, rA = check_A(measurements, verbose=args.verbose)
    sB, rB = check_B(measurements, limit_degc, verbose=args.verbose)
    sD, rD = check_D(measurements, u_exp_list, min_phys, max_phys, resolution, verbose=args.verbose)
    sE, rE = check_E(notes, u_exp_list, verbose=args.verbose)

    if conf_model == "linear":
        A_c   = calib["_A"]
        B_c   = calib["_B"]
        u_A_c = calib["_u_A"]
        u_B_c = calib["_u_B"]
        cov_c = calib["_cov_AB"]
        sC, rC = check_C(measurements, A_c, B_c, min_phys, max_phys, verbose=args.verbose)
        sF, rF = check_F(measurements, A_c, B_c, min_phys, max_phys, "funzione", verbose=args.verbose)
    else:
        A_c = B_c = u_A_c = u_B_c = cov_c = None
        na_label = f"N/A ({conf_model})"
        sC, rC = na_label, {}
        sF, rF = na_label, {}

    u_budget = calib.get("_u_budget_per_step", [])
    sH, rH = check_H(
        measurements, mae_degc=args.mae_degc,
        pfa_threshold_pct=args.pfa_threshold_pct,
        verbose=args.verbose, u_std_mode=args.pfa_u_std_mode,
        u_budget_per_step=u_budget,
    )

    check_results = {"G": (sG, rG), "A": (sA, rA), "B": (sB, rB), "C": (sC, rC),
                     "D": (sD, rD), "E": (sE, rE), "F": (sF, rF), "H": (sH, rH)}
    print_report(
        variant="funzione", input_path=args.input,
        check_results=check_results, measurements=measurements,
        calib=calib, limit_degc=limit_degc, budget_results=rD,
        min_phys=min_phys, max_phys=max_phys,
        mae_degc=args.mae_degc, pfa_threshold_pct=args.pfa_threshold_pct,
    )

    if args.charts and conf_model == "linear":
        plot_charts(
            measurements=measurements, budget_results=rD,
            A=A_c, B=B_c, u_A=u_A_c, u_B=u_B_c, cov_AB=cov_c,
            min_phys=min_phys, max_phys=max_phys,
            limit_degc=limit_degc, variant="funzione", temp_nominali=temp_nom,
        )


if __name__ == "__main__":
    main()
