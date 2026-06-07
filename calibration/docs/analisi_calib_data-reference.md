# analisi_calib_data.py  —  quick reference

> the main orchestrator. runs the whole pipeline from raw LSB16 data to pdf+xml+conformity.
> file: `backend\calibration\scripts\analisi_calib_data.py` (1014 lines)

---

## what it does, in order

| # | step | function / module | takes | gives |
|---|------|-------------------|-------|-------|
| 1 | load models | `SENSOR_model.from_json()` `RIFERIMENTO_model.from_json()` `VAR_extra()` | `--sensor` (default `models_in\ntc_temperature.json`) `--ref` (default `models_in\fluke_9142.json`) | sensor object, ref object, adc bits=16 |
| 2 | resolve procedure | inline in `main()` | `sensor.calibrationProcedure` from json, or `--procedure` override | `"linear"` `"cubic"` `"cube-log"` `"linear_interp"` `"cubic_interp"` |
| 3 | resolve old coeffs | inline `_coeff_from_json()` | `sensor.coeffA/B/C/D` from sensor json; 0.0 treated as "not set" → None; `--old-a/b/c/d` overrides | `old_A, old_B, old_C, old_D` (float or None) |
| 4 | run calibration | `_run_calibration()` → dispatches to `model_calibration\*.calibrate()` | `payload` (LSB16 json), `lsb_scale`, `sample_size=20`, `adc_max=65535`, `ub_pt_degc`, `ub_tmp_lsb`, `old_A/B/C/D` | `calib_result` dict |
| 5 | sensor accuracy gate | `SensorAccuracyChecker` from `calib_utils` | `accuracy_ranges` from `sensor_json.metrology.sensorAccuracy` | if `--update-parameters-if-out-range-error` + all points in range → skip cal, set `calibration_done="not_necessary"` |
| 6 | build filled cert | `_build_cert_filled()` | `cert_input` (template json), `sensor`, `calib_result`, `adc_max`, `lsb_scale` | `certificato_funzione_filled.json` |
| 7 | generate pdf | `certificato_funzione.py` (subprocess fallback) | `certificato_funzione_filled.json` | `ntc_cert_funzione.pdf` |
| 8 | generate dcc xml | `generate_dcc_xml.py` | `certificato_funzione_filled.json` | `ntc_calibration_certificate.xml` (ptb dcc 3.3.0) |
| 9 | save calib charts | `save_charts()` from the selected model module | `calib_result`, `lsb_scale`, `adc_max` | 5 png files in `images/calibration/` |
| 10 | run conformity | `run_validation.py` helpers (`check_G`, `check_A`..`check_H`) | `certificato_funzione_filled.json` | `conformity.json` + `images/conformity/` charts |

---

## functions

### `main()` (line 388)
entry point. parses cli, sets hardcoded thresholds, calls everything.

**hardcoded constants in `main()`:**
```
CONFORMITY_MAE_DEGC          = 0.30   # Check H — max acceptable error
CONFORMITY_PFA_THRESHOLD_PCT = 20.0   # Check H — false-acceptance threshold
CONFORMITY_PFA_U_STD_MODE     = "combined"  # "combined" | "type_a"
```

**hardcoded defaults (file paths):**
```
default_input_json  → data_in\export2_tmp126_lsb16.json
default_sensor_json → models_in\ntc_temperature.json
default_ref_json    → models_in\fluke_9142.json
default_cert_input  → template_in\certificato_funzione_input.json
default_cert_output → certificato_out\certificato_funzione_filled.json
default_pdf_output  → certificato_out\ntc_cert_funzione.pdf
default_xml_output  → certificato_out\ntc_calibration_certificate.xml
```

---

### `_run_calibration()` (line 294)
dispatches to correct calibration module by `procedure` string.

| procedure | module | function called | formula |
|-----------|--------|----------------|---------|
| `"linear"` | `linear_calibration` | `calibrate()` | `T = A·D + B`  (ols, gum) |
| `"cubic"` | `cubic_calibration` | `calibrate()` | `T = a0 + a1·D + a2·D² + a3·D³` |
| `"cube-log"` | `cube_log_calibration` | `calibrate()` | `1/T = C0 + C1·ln(D) + C3·ln³(D)` (steinhart-hart) |
| `"linear_interp"` | `linear_interp_calibration` | `calibrate()` | piecewise linear, first/last nodes |
| `"cubic_interp"` | `cubic_interp_calibration` | `calibrate()` | cubic lagrange, first-2/last-2 nodes |

each `calibrate()` receives the same base kwargs: `payload`, `lsb_scale_sensor_info`, `sample_size`, `adc_max`, `ub_pt_degc`, `ub_tmp_lsb`, `verbose`, `risol_degc` + optional unit-check args.

**procedure alias mapping** (in `main()`, line 516):
```
"qubic-interpolation"   → "linear"
"linear-interpolation"  → "linear_interp"
"cubic-interpolation"   → "cubic_interp"
```

---

### `_build_cert_filled()` (line 47)
builds the filled certificate json from template + calib result.

**logic:**
- deep-copies `cert_input` (template)
- populates `sensor_method_template.ntc_model` with sensor properties:
  - `R25`, `B25_85`, `A_steinhart`, `B_steinhart`, `C_steinhart`, `alpha_25`
  - `uncertainty_limit`, `calibration_procedure`, `calibration_formula`
  - `formula_steinhart`, `formula_beta`, `observations`
- writes model-specific coefficients into ntc_model:
  - **linear**: `A`, `B`, `u_A`, `u_B`, `cov_AB`
  - **cubic**: `a0`..`a3`, `u_a0`..`u_a3`, `cov_theta`
  - **interp**: `x_nodes`, `y_nodes`, `node_steps`, `rmse_degC`, `u_H_degC`
  - **cube-log**: `C0`, `C1`, `C3`, `u_C0`..`u_C3`, `cov_theta`
- computes per-point measurements (6 columns): `[point, T_ref, T_sensor_post, error_pre, error_post, U_exp]`
- pre-cal error uses **old** coefficients if available, otherwise falls back to `lsb_to_degc()`
- post-cal temp computed from the **new** calibration model
- stores all in `calculated_calibration_values`
- appends `_calibration_result` with model-specific metadata + uncertainties + `_lsb_per_c`, `_adc_bits`, `_phys_unit_symbol`

**functions imported for prediction:**
- `cubic_predict()` from `cubic_calibration.py` — eval cubic poly at a point
- `steinhart_hart_predict_degc()` from `cube_log_calibration.py` — eval s-h at a point
- `lsb_to_degc()` from `calib_utils.py` — `min_phys + (lsb / adc_max) * (max_phys - min_phys)`

---

### `_get_accuracy_ranges()` (line 38)
extracts `metrology.sensorAccuracy` list from sensor json. used by the accuracy gate.

### `_worst_accuracy_limit()` (line 42)
takes the maximum `maxError` across all accuracy ranges. used for chart title annotation.

### `_apply_calibration_skipped()` (line 349)
when calibration is skipped (all as-found errors already in range), copies old coefficients as new and sets `M_e_post = M_e_pre`.

---

## data taken from models_in (hardcoded)

### `VAR_REF_SENSOR.py` — `SENSOR_model` class defaults
| attribute | hardcoded default | meaning |
|-----------|-------------------|---------|
| `R25` | 10000.0 Ω | ntc resistance at 25°c |
| `B25_85` | 3950.0 | beta param |
| `A_steinhart` | 0.001129148 | steinhart-hart A |
| `B_steinhart` | 0.000234125 | steinhart-hart B |
| `C_steinhart` | 8.76741e-8 | steinhart-hart C |
| `alpha_25` | -0.044430 | temp coefficient |
| `calibrationProcedure` | `"qubic-interpolation"` | default procedure |
| `calibration_formula` | `"T = A * dout + B"` | formula string |
| `formula_steinhart` | `"1/T = A_sh + B_sh*ln(R) + C_sh*(ln R)^3"` | s-h string |
| `formula_beta` | `"R(T) = R25 * exp[B25_85 * (1/T - 1/T25)]"` | beta string |
| `uB` | 2.9 | sensor uncertainty [lsb] |
| `absUncertainty` | 5.0 | absolute uncertainty [lsb] |
| `K` | 2.0 | coverage factor |
| `resolution_degC` | 0.01 | sensor resolution |
| `uncertainty_limit` | `"within 0.10 C"` | declared limit |

### `VAR_REF_SENSOR.py` — `VAR_extra` class
| attribute | hardcoded | meaning |
|-----------|-----------|---------|
| `_adc_bits` | 16 | adc resolution |
| `_U_pt_c` | 0.065 | pt100 expanded uncertainty [°c] |
| `_k_pt` | 2.0 | pt100 coverage factor |
| `_d_tmp126_c` | 0.30 | tmp126 adc uncertainty [°c] |

### `VAR_REF_SENSOR.py` — `RIFERIMENTO_model` class defaults
| attribute | hardcoded default | meaning |
|-----------|-------------------|---------|
| `mpn` | `"9142"` | fluke model |
| `manufacturer` | `"Fluke / Hart Scientific"` | |
| `minPhysVal` | -25.0 °c | |
| `maxPhysVal` | 150.0 °c | |
| `immersionDepth` | 150.0 mm | |
| `heatingTimeMax` | 1500 s | |
| `coolingTimeMax` | 900 s | |
| `evaluationFormula` | `"RSS"` | uncertainty combination |

---

## formulas used (by model)

| model | formula name | formula | source function |
|-------|-------------|---------|-----------------|
| linear | ols linear | `T = A·D + B` | `linear_calibration.calibrate()` → numpy.linalg.lstsq |
| linear | gum uncertainty | `u²(ŷ) = u²(B) + D²·u²(A) + 2D·cov(A,B)` | `linear_calibration` internal |
| cubic | cubic polynomial | `T = a0 + a1·D + a2·D² + a3·D³` | `cubic_calibration.calibrate()` → design matrix ols |
| cubic | prediction | `T(D) = a0 + a1·D + a2·D² + a3·D³` | `cubic_predict()` |
| cube-log | steinhart-hart | `1/T = C0 + C1·ln(D) + C3·ln³(D)` | `cube_log_calibration.calibrate()` → log-transform ols |
| cube-log | prediction | `T = 1/(C0 + C1·ln(D) + C3·ln³(D))` | `steinhart_hart_predict_degc()` |
| linear_interp | piecewise linear | `T(D) = interp1d(x_nodes, y_nodes)` | `numpy.interp` |
| cubic_interp | cubic lagrange | `T(D) = L0·y0 + L1·y1 + L2·y2 + L3·y3` | `scipy.interpolate.lagrange` |
| all | lsb → °c | `T = min_phys + (lsb/adc_max) · (max_phys − min_phys)` | `lsb_to_degc()` in `calib_utils.py` |
| all | round to sig figs | `round(val, sig−1−floor(log10(|val|)))` | `round_to_significant_figures()` in `calib_utils.py` |
| conformity (h) | pfa | `1 − Φ(1; ein, u_ein) + Φ(−1; ein, u_ein)` | `run_validation.check_H()` → scipy.stats.norm.cdf |

---

## supporting modules loaded

| module | role |
|--------|------|
| `VAR_REF_SENSOR` | `SENSOR_model`, `RIFERIMENTO_model`, `VAR_extra` dataclasses |
| `calib_utils` | `JsonView`, `SensorAccuracyChecker`, `lsb_to_degc()`, `round_to_significant_figures()` |
| `model_calibration.unit_checks` | `dsi_to_symbol()`, `dsi_to_xml_unit()` |
| `model_calibration.linear_calibration` | `calibrate()`, `save_charts()` |
| `model_calibration.cubic_calibration` | `calibrate()`, `cubic_predict()`, `save_charts()` |
| `model_calibration.cube_log_calibration` | `calibrate()`, `steinhart_hart_predict_degc()`, `save_charts()` |
| `model_calibration.linear_interp_calibration` | `calibrate()`, `save_charts()` |
| `model_calibration.cubic_interp_calibration` | `calibrate()`, `save_charts()` |
| `certificato_funzione` | `configure_from_input()`, `build_pdf()` |
| `generate_dcc_xml` | `load_input_data()`, `build_dcc_tree()` |
| `run_validation` | `extract_calib()`, `extract_measurements()`, `extract_notes()`, `check_G`..`check_H`, `_parse_limit()`, `save_charts()` |
