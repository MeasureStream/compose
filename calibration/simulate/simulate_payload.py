"""
Generate a calibration payload in the format consumed by
``scripts/analisi_calib_data.py`` (the format of ``data_in/simulated_8p.json``).

The sensor model is the same ratiometric NTC divider used by
``simulate_one_measure.py``:
    R25 = 100 000 Ω, BETA = 4190 K, R_fixed = 50 000 Ω
    LSB = 2^16 * R_fixed / (R_fixed + R_ntc)

Noise model
-----------
* Systematic offset (calibration bias): added in °C, sensor = ref + N(0.1, 0.5)
* Within-group dispersion:                added in LSB after the °C→LSB
                                          conversion, then quantised to uint16.
  The dispersion std is constant in LSB, which is realistic for ADC +
  reference voltage noise.  Adding it in °C would make the LSB-domain
  std scale with |dLSB/dT| and blow up at high temperatures.

The output replaces ``data_in/simulated_8p.json`` (same field set, same
sample counts per step as the previous payload).
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_IN    = SCRIPT_DIR.parent / "data_in"
OUTPUT     = DATA_IN / "simulated_8p.json"


# --- Geometry (match ``simulate_one_measure.py``) ----------------------------

CALIBRATION_ID_PREFIX = "calib-sim"
MU_ID     = 3
SENSOR_ID = 2
SENSOR_SAMPLING_FREQ_HZ = 1

STEPS_C = [-20.0, -10.0, 0.0, 25.0, 50.0, 75.0, 100.0, 110.0]
STEP_DWELL_MIN = 1.0

# Reference sample / sensor frame / chunk
REFERENCE_SAMPLE_PERIOD_S = 1.0
SENSOR_FRAME_PERIOD_S     = 1.0
NTC_VALUES_PER_FRAME      = 10
SAMPLE_CHUNK_SIZE         = 20

# Per-step reference sample count (matches the existing simulated_8p.json)
N_SAMPLES_PER_STEP = {
    -20.0:  460,
    -10.0:  320,
     0.0:  280,
    25.0:  300,
    50.0:  320,
    75.0:  260,
   100.0:  300,
   110.0:  300,
}


# --- NTC parameters (ratiometric divider) ------------------------------------
# These MUST match the sensor JSON in models_in/sensors/ntc_temperature_steinhart.json
# (rDivider / adcMax), otherwise the calibration script will decode the LSB
# values into wrong resistance values and the Steinhart fit will be wrong.

R25    = 100_000.0    # NTC at 25 °C [Ω]
BETA   = 4190.0       # Beta coefficient [K]
T25_K  = 273.15 + 25.0
R_FIX  = 100_000.0    # rDivider from sensor JSON
V_REF  = 3.3          # informational, ratiometric so it cancels
ADC_MAX   = 65535     # adcMax from sensor JSON (last representable code)


# --- Noise model -------------------------------------------------------------
# Two additive noise terms, both expressed in LSB so the std is constant
# in the LSB domain across the full temperature range (which is what a
# real 16-bit ADC with a stable reference voltage produces):
#   * systematic per-frame bias  ~ N(BIAS_MEAN_LSB, BIAS_STD_LSB)
#   * per-sample dispersion      ~ N(0,            DISPERSION_STD_LSB)
# Adding the bias in °C and then converting to LSB would scale with
# |dLSB/dT| and inflate the LSB-domain std at high temperatures.

BIAS_MEAN_LSB      = 200.0       # mean systematic offset, LSB
BIAS_STD_LSB       = 1.0         # systematic offset std, LSB
DISPERSION_STD_LSB = 5.0         # within-frame dispersion std, LSB


# --- Reference profile (real-lab RTD drift, centred) -------------------------
# Centred °C offset applied to the step target.  Kept intentionally small
# (0.001 °C std) so the LSB-domain variation from the reference itself is
# negligible compared to the per-sample LSB dispersion.  The reference
# drift is a *physical* °C quantity (from the RTD stability spec, which
# is constant in °C), so a non-zero value here would inflate the LSB
# std at high temperatures through the |dLSB/dT| gain.

_rng_seed = np.random.default_rng(2026)
def _rtd_offset_series(step_target: float, n: int) -> np.ndarray:
    """Synthetic centred RTD drift for a step: mean ≈ 0, std ≈ 0.001 °C."""
    phase = (step_target + 30.0) * 0.1
    t = np.arange(n) / max(1, n - 1)
    drift = 0.001 * np.sin(2 * math.pi * t + phase)
    drift += 0.0003 * _rng_seed.standard_normal(n)
    return drift


# --- NTC helpers -------------------------------------------------------------

def temp_c_to_lsb(temp_c):
    """NTC thermistor divider encoding to LSB, matching the sensor JSON
    preprocessing formula exactly:  R = LSB / (adcMax - LSB) * rDivider.

    Inverse (encoding) form:  LSB = R_ntc * adcMax / (R_ntc + rDivider).
    """
    t_k  = temp_c + 273.15
    r    = R25 * np.exp(BETA * (1.0 / t_k - 1.0 / T25_K))
    lsb  = r * ADC_MAX / (r + R_FIX)
    return lsb


def simulate_one_measure_payload(steps):
    rng = np.random.default_rng(42)

    now          = datetime.now().replace(microsecond=0)
    start_time   = now.isoformat() + "Z"
    start_dwell  = (now + timedelta(milliseconds=200)).isoformat() + "Z"

    sensor_raw_samples          = []
    reference_temperature_samples = []
    steps_summary = []

    t_ref    = now
    t_sensor = now + timedelta(milliseconds=200)

    for step_idx, target in enumerate(steps):
        n_samples = N_SAMPLES_PER_STEP[target]
        n_chunks  = max(1, n_samples // SAMPLE_CHUNK_SIZE)
        n_keep    = n_chunks * SAMPLE_CHUNK_SIZE

        # --- reference profile: target + centred drift -----------------------
        drift = _rtd_offset_series(target, n_keep)
        ref_temps = target + drift

        # --- sensor reading model -------------------------------------------
        # 1. Convert the *reference* temperature to a target LSB (the ideal
        #    sensor response, no offset).
        # 2. Add a per-frame systematic offset in LSB (constant in LSB, so
        #    it does not scale with |dLSB/dT|).
        # 3. Add per-sample Gaussian noise in LSB (within-frame dispersion).
        # 4. Quantise to uint16 and clip to [0, ADC_MAX].
        ref_lsb_means   = temp_c_to_lsb(ref_temps)
        frame_offset    = rng.normal(BIAS_MEAN_LSB, BIAS_STD_LSB, size=n_keep)
        sample_noise    = rng.normal(0.0, DISPERSION_STD_LSB,
                                     size=(n_keep, NTC_VALUES_PER_FRAME))
        frames = np.clip(
            np.rint(ref_lsb_means[:, None] + frame_offset[:, None] + sample_noise),
            0, ADC_MAX,
        ).astype(np.uint16)

        # --- write per-step samples ----------------------------------------
        for i in range(n_keep):
            reference_temperature_samples.append({
                "index_step": step_idx,
                "timestamp":  t_ref.isoformat() + "Z",
                "target":     float(target),
                "reading":    round(float(ref_temps[i]), 4),
                "stable_hw":  True,
            })
            t_ref += timedelta(seconds=REFERENCE_SAMPLE_PERIOD_S)

            sensor_raw_samples.append({
                "index_step": step_idx,
                "timestamp":  t_sensor.isoformat() + "Z",
                "target":     float(target),
                "value":      [int(v) for v in frames[i]],
            })
            t_sensor += timedelta(seconds=SENSOR_FRAME_PERIOD_S)

        steps_summary.append(f"({target:.1f},{int(STEP_DWELL_MIN)})")

    payload = {
        "calibration_id": f"{CALIBRATION_ID_PREFIX}-{MU_ID}-{SENSOR_ID}-{now.strftime('%Y%m%dT%H%M%S')}",
        "mu_id":  MU_ID,
        "sensor_id": SENSOR_ID,
        "sensor_sampling_freq": [SENSOR_SAMPLING_FREQ_HZ] * len(steps),
        "start_time":      [start_time] * len(steps),
        "start_time_dwell": [start_dwell] * len(steps),
        "steps": steps_summary,
        "reference_temperature_samples": reference_temperature_samples,
        "sensor_raw_samples": sensor_raw_samples,
    }
    return payload


def main():
    payload = simulate_one_measure_payload(STEPS_C)
    DATA_IN.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print(f"  steps             : {len(STEPS_C)}")
    print(f"  ref samples       : {len(payload['reference_temperature_samples'])}")
    print(f"  sensor frames     : {len(payload['sensor_raw_samples'])}")
    print(f"  values per frame  : {NTC_VALUES_PER_FRAME}")
    print(f"  bias mean / std   : {BIAS_MEAN_LSB} / {BIAS_STD_LSB} LSB")
    print(f"  dispersion std    : {DISPERSION_STD_LSB} LSB (constant in LSB)")
    print(f"  LSB range         : [0, {ADC_MAX}]")


if __name__ == "__main__":
    main()
