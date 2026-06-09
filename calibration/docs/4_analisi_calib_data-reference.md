# analisi_calib_data.py  —  quick reference


## what it does, in order

| # | step | function / module | takes | gives |
|---|------|-------------------|-------|-------|
| 1 | load models | `json.loads()` directly | `--sensor` (default `models_in\sensors\ntc_temperature.json`) `--ref` (default `models_in\references\fluke_9142.json`) | raw dicts, no class wrappers |
| 2 | resolve procedure | inline in `main()` | `sensor.calibration.type` from json, or `--procedure` override | `"linear"` or `"cubic"`; alias `"qubic-interpolation"` → `"linear"` |
| 3 | resolve old coeffs | `_get_calib_coeff()` | `sensor.calibration.calibrationCoefficients.{A,B,C,D}.value` from sensor json; 0.0 treated as "not set" → None; `--old-a/b/c/d` overrides | `old_A, old_B, old_C, old_D` (float or None) |
| 4 | run calibration | `_run_calibration()` → dispatches to `model_calibration\*.calibrate()` | `payload` (LSB16 json), `lsb_scale`, `sample_size=20`, `adc_max=65535`, `ub_ref_y`, `ub_sensor_lsb`, `old_A/B/C/D` | `calib_result` dict |
| 5 | sensor accuracy gate | `SensorAccuracyChecker` from `calib_utils` | `accuracy_ranges` from `sensor_json.metrology.sensorAccuracy` | if `--update-parameters-if-out-range-error` + all points in range → skip cal, set `calibration_done="not_necessary"` |
| 6 | build filled cert | `_build_cert_filled()` | `cert_input` (template json), `sensor_json`, `calib_result`, `adc_max`, `lsb_scale` | `certificato_funzione_filled.json` |
| 7 | generate pdf | `certificato_funzione.py` (subprocess fallback) | `certificato_funzione_filled.json` | `ntc_cert_funzione.pdf` |
| 8 | generate dcc xml | `generate_dcc_xml.py` | `certificato_funzione_filled.json` | `ntc_calibration_certificate.xml` (ptb dcc 3.3.0) |
| 9 | save calib charts | `save_charts()` from the selected model module | `calib_result`, `lsb_scale`, `adc_max` | 5 png files in `images/calibration/` |
| 10 | run conformity | `checks_helper.py` helpers (`check_G`, `check_A`, `check_B`, `check_H`) | `certificato_funzione_filled.json` | `conformity.json` + `images/conformity/` charts |

---

## functions

### `main()` (line 321)
entry point. parses cli, sets hardcoded thresholds, calls everything.

**hardcoded defaults in `main()`:**
```
CONFORMITY_MAE_Y            = 0.30   # Check H — max acceptable error [Y]
CONFORMITY_PFA_THRESHOLD_PCT = 20.0   # Check H — false-acceptance threshold
CONFORMITY_PFA_U_STD_MODE     = "combined"  # "combined" | "type_a"
```

**hardcoded defaults (file paths):**
```
default_input_json  → data_in\export2_tmp126_lsb16.json
default_sensor_json → models_in\sensors\ntc_temperature.json
default_ref_json    → models_in\references\fluke_9142.json
default_cert_input  → template_in\certificato_funzione_input.json
default_cert_output → certificato_out\certificato_funzione_filled.json
default_pdf_output  → certificato_out\ntc_cert_funzione.pdf
default_xml_output  → certificato_out\ntc_calibration_certificate.xml
```

---

### `_run_calibration()` (line 248)
dispatches to correct calibration module by `procedure` string.

| procedure | module | function called | formula |
|-----------|--------|----------------|---------|
| `"linear"` | `linear_calibration` | `calibrate()` | `Y = A·D + B`  (ols, gum, both axes in physical units) |
| `"cubic"` | `cubic_calibration` | `calibrate()` | `Y = a0 + a1·D + a2·D² + a3·D³` (mixed domain) |

each `calibrate()` receives the same base kwargs: `payload`, `lsb_scale_sensor_info`, `sample_size`, `adc_max`, `ub_ref_y`, `ub_sensor_lsb`, `verbose`, `risol` + optional unit-check args.

**procedure alias mapping** (in `main()`, line 451):
```
"qubic-interpolation"   → "linear"
```

---

### `_build_cert_filled()` (line 54)
builds the filled certificate json from template + calib result.

**logic:**
- deep-copies `cert_input` (template)
- populates `sensor_method_template.sensor_model` (was `ntc_model`) with sensor properties:
  - `calibration_procedure`, `method_description`, `observations`
- writes model-specific coefficients into `sensor_model`:
  - **linear**: `_A_cal`, `_B_cal`, `_u_A`, `_u_B`, `_cov_AB`
  - **cubic**: `_a0`..`_a3`, `_u_a0`..`_u_a3`, `_cov_theta`
- computes per-point measurements (6 columns): `[point, T_ref, T_sensor_post, error_pre, error_post, U_exp]`
- pre-cal error uses **old** coefficients if available, otherwise falls back to `lsb_to_y()`
- post-cal temp computed from the **new** calibration model
- stores all in `calculated_calibration_values`
- appends `_calibration_result` with model-specific metadata + uncertainties + `_lsb_per_c`, `_adc_bits`, `_phys_unit_symbol`

**functions imported for prediction:**
- `cubic_predict()` from `cubic_calibration.py` — eval cubic poly at a point
- `lsb_to_y()` from `calib_utils.py` — `min_phys + (lsb / adc_max) * (max_phys - min_phys)`

---

### `_get_accuracy_ranges()` (line 38)
extracts `metrology.sensorAccuracy` list from sensor json. used by the accuracy gate.

### `_worst_accuracy_limit()` (line 42)
takes the maximum `maxError` across all accuracy ranges. used for chart title annotation.

### `_apply_calibration_skipped()` (line 349)
when calibration is skipped (all as-found errors already in range), copies old coefficients as new and sets `M_e_post = M_e_pre`.

---

## data taken from models_in (loaded via json.loads)

### `sensors/ntc_temperature.json` — key values
| json path | typical value | meaning |
|-----------|---------------|---------|
| `ranges.threshold.min` | −40.0 °C | physical range min |
| `ranges.threshold.max` | 105.0 °C | physical range max |
| `ranges.elec.adcBits` | 16 | adc resolution |
| `calibration.type` | `"linear"` | default procedure |
| `calibration.calibrationCoefficients.A.value` | 0.0 | previous coeff A (0.0 = not set) |
| `calibration.calibrationCoefficients.B.value` | 0.0 | previous coeff B (0.0 = not set) |
| `calibration.calibrationCoefficients.C.value` | 0.0 | previous coeff C (0.0 = not set) |
| `calibration.calibrationCoefficients.D.value` | 0.0 | previous coeff D (0.0 = not set) |
| `metrology.readingUncertainty[uB].value` | 0.30 | sensor type-B uncertainty [lsb] |
| `metrology.readingUncertainty[absUncertainty].value` | 5.0 | absolute uncertainty [lsb] |
| `metrology.sensorAccuracy[]` | `[{tempMin, tempMax, maxError:0.60}]` | accuracy gate ranges |
| `metrology.Uncertainty[0].absUncertainty` | 0.10 | declared limit (check B) |

### `references/fluke_9142.json` — key values
| json path | typical value | meaning |
|-----------|---------------|---------|
| `metrology.Uncertainty[0].ub` | 0.15 | reference type-B std uncertainty [°c] |

---

## formulas used (by model)

| model | formula name | formula | source function |
|-------|-------------|---------|-----------------|
| linear | ols linear | `Y = A·D + B` (both axes in physical units) | `linear_calibration.calibrate()` → numpy.linalg.lstsq |
| linear | gum uncertainty | `u²(ŷ) = u²(B) + D²·u²(A) + 2D·cov(A,B)` | `linear_calibration` internal |
| cubic | cubic polynomial | `Y = a0 + a1·D + a2·D² + a3·D³` (mixed domain) | `cubic_calibration.calibrate()` → design matrix ols |
| cubic | prediction | `Y(D) = a0 + a1·D + a2·D² + a3·D³` | `cubic_predict()` |
| linear (corr) | lsb → Y | `Y = min_phys + (lsb/adc_max) · (max_phys − min_phys)` | `lsb_to_y()` in `calib_utils.py` |
| all | round to sig figs | `round(val, sig−1−floor(log10(|val|)))` | `round_to_significant_figures()` in `calib_utils.py` |
| conformity (H) | pfa | `1 − Φ(1; ein, u_ein) + Φ(−1; ein, u_ein)` | `checks_helper.check_H()` → scipy.stats.norm.cdf |

---

## supporting modules loaded

| module | role |
|--------|------|
| `models_in/sensors/ntc_temperature.json` | sensor model JSON loaded directly via `json.loads()` |
| `models_in/references/fluke_9142.json` | reference model JSON loaded directly via `json.loads()` |
| `calib_utils` | `_lookup`, `SensorAccuracyChecker`, `lsb_to_y()`, `round_to_significant_figures()` |
| `model_calibration.unit_checks` | `dsi_to_symbol()`, `dsi_to_xml_unit()` |
| `model_calibration.linear_calibration` | `calibrate()`, `save_charts()` |
| `model_calibration.cubic_calibration` | `calibrate()`, `cubic_predict()`, `save_charts()` |
| `certificato_funzione` | `configure_from_input()`, `build_pdf()` |
| `generate_dcc_xml` | `load_input_data()`, `build_dcc_tree()` |
| `checks_helper` | `extract_calib()`, `extract_measurements()`, `check_G`, `check_A`, `check_B`, `check_H`, `save_charts()` |
