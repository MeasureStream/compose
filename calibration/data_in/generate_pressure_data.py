"""Generate synthetic pressure calibration data with cubic characteristic + noise.

Pressure range:    0 to 20 atm  (subdivided on 16 bits)
Output unit:       atm          (reference reads in atm, sensor raw in LSB16)
True relationship: slightly cubic polynomial from true pressure to ADC LSB
Noise:             Gaussian on reference readings and ADC counts

Usage:
    python generate_pressure_data.py              # writes data_in/pressure_data.json
    python generate_pressure_data.py --steps 6    # 6 steps (default)
    python generate_pressure_data.py --noise-ref 0.003 --noise-lsb 60  # tune noise
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Cubic polynomial: true pressure (atm) -> ideal ADC LSB value
# ---------------------------------------------------------------------------
# y_lsb = c0 + c1*P + c2*P^2 + c3*P^3
# where P is in atm, y_lsb is in ADC counts [0 .. 65535]
#
# The dominant linear term:  c1 = 65535 / 20 = 3276.75  LSB/atm
# c2, c3 introduce mild cubic curvature so the relationship is not purely linear.
# ---------------------------------------------------------------------------
_CUBIC_COEFFS = (0.0, 3276.75, 50.0, -2.5)

ADC_MAX   = 65535.0
ADC_BITS  = 16
P_MIN     = 0.0
P_MAX     = 20.0


def _cubic_p_to_lsb(p_atm: float) -> float:
    c0, c1, c2, c3 = _CUBIC_COEFFS
    raw = c0 + c1 * p_atm + c2 * (p_atm ** 2) + c3 * (p_atm ** 3)
    return max(0.0, min(ADC_MAX, raw))


def _generate_step_data(
    p_nominal: float,
    idx: int,
    n_samples: int,
    n_values_per_frame: int,
    noise_ref_std: float,
    noise_lsb_std: float,
    rng: random.Random,
) -> Tuple[List[dict], List[dict]]:
    """Return (reference_samples, sensor_raw_samples) for one pressure step."""
    target_lsb = _cubic_p_to_lsb(p_nominal)

    ref_samples: List[dict] = []
    sensor_samples: List[dict] = []

    for s in range(n_samples):
        # Reference: true pressure + tiny Gaussian noise
        ref_reading = p_nominal + rng.gauss(0.0, noise_ref_std)
        ts = f"2026-06-01T10:{idx:02d}:{s:05.2f}Z"
        ref_samples.append({
            "index_step": idx,
            "timestamp": ts,
            "target": p_nominal,
            "reading": round(ref_reading, 6),
            "stable_hw": "True",
        })

        # Sensor raw: ideal LSB + cubic deviation + Gaussian noise
        values = []
        for _ in range(n_values_per_frame):
            lsb_val = target_lsb + rng.gauss(0.0, noise_lsb_std)
            lsb_val = max(0.0, min(ADC_MAX, lsb_val))
            values.append(round(lsb_val))
        sensor_samples.append({
            "index_step": idx,
            "timestamp": ts,
            "value": values,
        })

    return ref_samples, sensor_samples


def _build_steps(num_steps: int) -> List[float]:
    """Return *num_steps* evenly spaced nominal pressures in (0, 20) atm."""
    if num_steps < 2:
        raise ValueError("At least 2 steps required")
    return [
        round(P_MIN + (P_MAX - P_MIN) * i / (num_steps - 1), 2)
        for i in range(num_steps)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic pressure calibration data (cubic + noise)"
    )
    parser.add_argument("--steps", type=int, default=6,
                        help="Number of calibration steps (default: 6)")
    parser.add_argument("--samples-per-step", type=int, default=100,
                        help="Number of sample frames per step (default: 100)")
    parser.add_argument("--values-per-frame", type=int, default=20,
                        help="Raw ADC values per frame (default: 20)")
    parser.add_argument("--noise-ref", type=float, default=0.004,
                        help="Std dev of reference noise in atm (default: 0.004)")
    parser.add_argument("--noise-lsb", type=float, default=80.0,
                        help="Std dev of ADC noise in LSB (default: 80.0)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "pressure_data.json",
                        help="Output JSON path")
    parser.add_argument("--print-relationship", action="store_true",
                        help="Print the cubic P-to-LSB mapping table")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calibration_id = f"calib-pressure-{now_iso}"

    nominal_pressures = _build_steps(args.steps)

    if args.print_relationship:
        print("Cubic P -> LSB  relationship (ideal, no noise):")
        print(f"  coeffs: c0={_CUBIC_COEFFS[0]}, c1={_CUBIC_COEFFS[1]}, "
              f"c2={_CUBIC_COEFFS[2]}, c3={_CUBIC_COEFFS[3]}")
        print(f"  {'P [atm]':>10s}  {'LSB ideal':>10s}  {'LSB/atm':>10s}")
        for p in nominal_pressures:
            lsb = _cubic_p_to_lsb(p)
            eff = lsb / p if p > 0 else float("nan")
            print(f"  {p:10.2f}  {lsb:10.1f}  {eff:10.2f}")
        # Also print the purely linear equivalent for comparison
        print(f"  {'P [atm]':>10s}  {'LSB linear':>10s}  {'delta':>10s}")
        for p in nominal_pressures:
            lsb_cubic = _cubic_p_to_lsb(p)
            lsb_lin   = p / P_MAX * ADC_MAX
            print(f"  {p:10.2f}  {lsb_lin:10.1f}  {lsb_cubic - lsb_lin:+10.1f}")
        print()

    # ------------------------------------------------------------------
    # Build payload
    # ------------------------------------------------------------------
    steps_str = [f"({p},{1})" for p in nominal_pressures]
    all_ref: List[dict] = []
    all_sensor: List[dict] = []

    for i, p_nom in enumerate(nominal_pressures):
        refs, sensors = _generate_step_data(
            p_nominal=p_nom, idx=i,
            n_samples=args.samples_per_step,
            n_values_per_frame=args.values_per_frame,
            noise_ref_std=args.noise_ref,
            noise_lsb_std=args.noise_lsb,
            rng=rng,
        )
        all_ref.extend(refs)
        all_sensor.extend(sensors)
        p_mean_ref = sum(r["reading"] for r in refs) / len(refs)
        p_mean_lsb = sum(sum(f["value"]) / len(f["value"]) for f in sensors) / len(sensors)
        p_mean_phys = p_mean_lsb / ADC_MAX * P_MAX
        print(f"Step {i}: nominal={p_nom} atm  |  "
              f"ref_mean={p_mean_ref:.6f} atm  |  "
              f"sensor_mean={p_mean_lsb:.1f} LSB ({p_mean_phys:.6f} atm equiv)")

    payload = {
        "calibration_id": calibration_id,
        "calibrator_id": 1,
        "mu_id": 1,
        "sensor_id": 1,
        "steps": steps_str,
        "reference_temperature_samples": all_ref,
        "sensor_raw_samples": all_sensor,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_ref)} ref samples + {len(all_sensor)} sensor frames "
          f"-> {args.output}  ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
