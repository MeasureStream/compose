import base64
import json
import struct
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


# =============================================================================
# CONSTANTS — change these to tune the simulation
# =============================================================================

NUM_STEPS = 6
TEMPERATURES_C = [0.0, 25.0, 50.0, 75.0, 100.0, 125.0]

NUM_REF_MEASUREMENTS_PER_STEP = 240
SENSOR_VALUES_PER_REF = 5
SENSOR_SAMPLING_FREQ_HZ = 1

SENSOR_ID = 3
MU_ID = 4

REF_ERROR_MEAN = 0.0
REF_ERROR_STD = 0.015
REF_OSCILLATION_AMPLITUDE = 0.025
REF_OSCILLATION_PERIOD_SAMPLES = 60

SENSOR_NOISE_STD = 0.020

MIN_LSB = 0
MAX_LSB = 2**16 - 1

TEMP_MIN_MARGIN_C = -10.0
TEMP_MAX_MARGIN_C = 135.0

LSB_SCALE = (MAX_LSB - MIN_LSB) / (TEMP_MAX_MARGIN_C - TEMP_MIN_MARGIN_C)
LSB_TO_DEGC = 1.0 / LSB_SCALE
LSB_OFFSET = MIN_LSB - TEMP_MIN_MARGIN_C * LSB_SCALE


# =============================================================================
# Sensor-response functions:  sensor_temp = f(reference_temp)
# Set USE_CUBIC = True to switch to cubic.
# =============================================================================

USE_CUBIC = False

A_LIN = 1.002
B_LIN = -0.05

def sensor_linear(ref_temp):
    return A_LIN * ref_temp + B_LIN

A_CUB = 0.000001
B_CUB = 0.0001
C_CUB = 0.98
D_CUB = 0.3

def sensor_cubic(ref_temp):
    return A_CUB * ref_temp**3 + B_CUB * ref_temp**2 + C_CUB * ref_temp + D_CUB

SENSOR_FN = sensor_cubic if USE_CUBIC else sensor_linear


# =============================================================================
# calib-id  (sensor_id and mu_id are params)
# =============================================================================


OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = OUTPUT_DIR / "one_measure.json"
PLOT_FILE = OUTPUT_DIR / "one_measure_scatter.png"


def make_calib_id(sensor_id, mu_id):
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"calib-{sensor_id}-{mu_id}-{ts}"


def encode_sensor_b64(values):
    if not values:
        return ""
    packed = struct.pack(f"<{len(values)}H", *values)
    return base64.b64encode(packed).decode("ascii")


def simulate():
    rng = np.random.default_rng(42)
    now = datetime.now().replace(microsecond=0)
    calib_id = make_calib_id(SENSOR_ID, MU_ID)

    step_summary = [
        {"target": t, "minutes": 1}
        for t in TEMPERATURES_C
    ]

    overall_start = now.isoformat() + "Z"

    messages = []
    all_ref_c = []
    all_sensor_lsb = []

    for step_idx, target in enumerate(TEMPERATURES_C):
        n = NUM_REF_MEASUREMENTS_PER_STEP

        oscillation = REF_OSCILLATION_AMPLITUDE * np.sin(
            2.0 * np.pi * np.arange(n) / REF_OSCILLATION_PERIOD_SAMPLES
        )

        noise = rng.normal(REF_ERROR_MEAN, REF_ERROR_STD, size=n)

        ref_readings = target + oscillation + noise

        sensor_temps = SENSOR_FN(ref_readings)
        sensor_noise = rng.normal(0.0, SENSOR_NOISE_STD, size=n)
        sensor_temps += sensor_noise

        sensor_values = []
        for st in sensor_temps:
            frame_size = SENSOR_VALUES_PER_REF
            frame = rng.normal(st, SENSOR_NOISE_STD / 2.0, size=frame_size)
            frame_lsb = np.rint(frame * LSB_SCALE + LSB_OFFSET).astype(int).clip(MIN_LSB, MAX_LSB)
            sensor_values.extend(frame_lsb.tolist())

        sensor_b64 = encode_sensor_b64(sensor_values)

        dwell_dt = now + timedelta(seconds=0, milliseconds=200)
        start_time_dwell = dwell_dt.isoformat() + "Z"

        msg = {
            "calib_id": calib_id,
            "target": target,
            "step_summary": step_summary,
            "step_index": step_idx,
            "start_time": overall_start,
            "start_time_dwell": start_time_dwell,
            "ref_readings": [round(float(r), 6) for r in ref_readings],
            "sensor_sampling_freq": SENSOR_SAMPLING_FREQ_HZ,
            "sensor_b64": sensor_b64,
        }
        messages.append(msg)

        for i, r in enumerate(ref_readings):
            all_ref_c.extend([r] * SENSOR_VALUES_PER_REF)
        all_sensor_lsb.extend(sensor_values)

    OUTPUT_FILE.write_text(json.dumps(messages, indent=2), encoding="utf-8")

    print(f"calib_id : {calib_id}")
    print(f"steps    : {len(messages)}")
    print(f"LSB range: [{MIN_LSB}, {MAX_LSB}]")
    for m in messages:
        print(f"  step {m['step_index']}: target={m['target']:6.1f}C  "
              f"ref={len(m['ref_readings']):4d} readings  "
              f"sensor_b64={len(m['sensor_b64'])} chars")
    print(f"written  : {OUTPUT_FILE}")

    all_sensor_c = (np.array(all_sensor_lsb, dtype=float) - LSB_OFFSET) * LSB_TO_DEGC
    all_ref_c_arr = np.array(all_ref_c, dtype=float)
    residuals = all_sensor_c - all_ref_c_arr

    print(f"residuals: mean={residuals.mean():.6f}C  std={residuals.std():.6f}C  "
          f"min={residuals.min():.6f}C  max={residuals.max():.6f}C")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7))

    ax1.scatter(all_sensor_c, all_ref_c_arr, s=2, alpha=0.5, c="tab:blue", edgecolors="none")
    ax1.set_xlabel("sensor LSB mapped to C")
    ax1.set_ylabel("ref C")
    ax1.set_title(f"sensor vs ref  ({calib_id})")
    ax1.grid(True, alpha=0.3)

    lim_min = min(ax1.get_xlim()[0], ax1.get_ylim()[0])
    lim_max = max(ax1.get_xlim()[1], ax1.get_ylim()[1])
    ax1.plot([lim_min, lim_max], [lim_min, lim_max], "k--", linewidth=0.8, alpha=0.4)
    ax1.set_xlim(lim_min, lim_max)
    ax1.set_ylim(lim_min, lim_max)

    ax2.scatter(all_ref_c_arr, residuals, s=2, alpha=0.5, c="tab:red", edgecolors="none")
    ax2.axhline(y=0, color="k", linewidth=0.8, linestyle="--", alpha=0.4)
    ax2.set_xlabel("ref C")
    ax2.set_ylabel("residual  sensor - ref  C")
    ax2.set_title(f"residuals  (sensor - ref)")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(str(PLOT_FILE), dpi=150)
    plt.close(fig)
    print(f"plot    : {PLOT_FILE}")


if __name__ == "__main__":
    simulate()
