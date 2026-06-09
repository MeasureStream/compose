# Architecture

> Keep aligned with the code. Update when scripts, models, or folder layout change.

---

## Pipeline stages

| # | Stage | Script | Input | Output |
|---|---|---|---|---|
| 1 | Orchestration | `analisi_calib_data.py` | LSB16 JSON (`--input`) + sensor JSON (`--sensor`) + ref JSON (`--ref`) | drives stages 2–6 |
| 2a | Calibration (linear) | `model_calibration/linear_calibration.py` | LSB16 payload dict + old A/B | A, B, uncertainties, old_A/old_B dict |
| 2b | Calibration (cubic) | `model_calibration/cubic_calibration.py` | LSB16 payload dict + old a0..a3 | a0…a3, cov_theta, uncertainties, old_a0..old_a3 dict |
| 3 | Sensor-accuracy gate | `analisi_calib_data._check_sensor_accuracy_in_range()` | as-found errors + `sensorAccuracy` ranges | skip/proceed decision + `_sensor_accuracy_check` dict |
| 4 | Certificate JSON | `analisi_calib_data._build_cert_filled()` | template + calib result | `certificato_funzione_filled.json` |
| 5 | PDF certificate | `certificato_funzione.py` | filled JSON | `ntc_cert_funzione.pdf` |
| 6 | DCC XML | `generate_dcc_xml.py` | filled JSON | `ntc_calibration_certificate.xml` |
| — | Conformity check | `checks_helper.py` (invoked inline by orchestrator) | filled JSON | PASS/FAIL/WARN/N/A report to stdout |

---

## Key scripts

### `analisi_calib_data.py` — Orchestrator

- Bootstraps `sys.path` to include `scripts/` (for the `model_calibration` package and other modules).
- Loads sensor and reference JSON models directly via `json.loads()` (no class-based deserialisation).
  Default paths: `models_in/sensors/ntc_temperature.json` and `models_in/references/fluke_9142.json`.
- Resolves previous firmware coefficients from `sensor.calibration.calibrationCoefficients.{A,B,C,D}`
  (zero is treated as the "not set" sentinel → `None`). CLI flags `--old-a/b/c/d` override JSON
  values when present. These are passed to each engine as `old_a/old_b` (linear) or
  `old_a/old_b/old_c/old_d` (cubic) and used throughout for as-found error computation
  and the skipped-calibration path.
- Dispatches calibration on `--procedure` CLI flag (or `sensor.calibration.type` if flag is omitted):
  - `"linear"` → `model_calibration.linear_calibration.calibrate()`
  - `"cubic"` → `model_calibration.cubic_calibration.calibrate()`
  - Legacy alias `"qubic-interpolation"` maps to `"linear"`.
  - Any other value → exits with error.
- After calibration, evaluates the **sensor-accuracy gate** (step 3):
  reads `metrology.sensorAccuracy` from the sensor JSON, computes the as-found
  error at each step (applying old coefficients when available, raw LSB→Y otherwise),
  and checks each error against the most restrictive `maxError` of all
  `sensorAccuracy` ranges that contain `T_ref`.
  - When `--update-parameters-if-out-range-error` is set and all as-found errors
    are within their declared limits, calibration is marked `not_necessary`:
    the certificate keeps the old (or identity) coefficients as both as-found and
    as-left, `M_e_post = M_e_pre` for every row, and `_calibration_done =
    "not_necessary"` is written to the filled JSON. A console message is printed.
  - When at least one error exceeds its limit, calibration proceeds normally and
    `_calibration_done = "done"` is written.
- Builds the filled JSON by deep-copying the template and injecting computed
  keys (prefixed with `_`). The `M_e_pre` column uses the as-found reading (old
  coefficients applied) rather than the raw uncorrected reading.
- Replaces `calculated_calibration_values` entirely with computed measurements.
- Runs the full conformity check suite inline (checks G, A, B, H) and optionally writes
  a conformity JSON when `--conformity-output` is set.
- When `--check-units` is set, passes `sensor_json` and `ref_json` to each
  engine's `calibrate()` call. The engine calls `unit_checks.check_dsi()` before
  regression; on hard dimensional errors it raises `ValueError` which the
  orchestrator catches, prints, and exits with code 1.
- When `--convert-units` is set, the engine calls `unit_checks.convert_result()`
  after regression and returns a `converted` sub-dict.
- Invokes `certificato_funzione.py` and `generate_dcc_xml.py` in-process;
  falls back to subprocess if import fails.

### `model_calibration/linear_calibration.py` — Linear calibration engine

- Both axes in the **physical domain** (Y units, e.g. °C); no LSB conversion during regression.
- Model: `T_ref = A * D_sensor + B`  (both T_ref and D_sensor in Y)
- Statistic: GUM OLS with full analytical uncertainty propagation (sensitivity
  coefficients for every data point).
- Sample grouping: raw readings grouped into blocks of `sample_size=20`; per-block
  mean and std computed; population mean and std of block means used as inputs to OLS.
- Type A uncertainty: population std of block means (per step).
- Type B uncertainty: passed in as `ub_ref_y` (reference) and `ub_sensor_lsb` (sensor ADC).
- Expanded uncertainty `U(E)` at each step: `2 × √(u(T_ref)² + u(T_i)²)` where
  both `u` include type A and type B components.
- Accepts optional `old_a` / `old_b` (previous firmware coefficients). When supplied,
  prints a baseline pre-fit error block (signed mean, max abs, per-step values).
  Stored in the result dict as `old_A` / `old_B`.
- Returns dict with `model="linear"`, `A`, `B`, `u_A`, `u_B`, `cov_AB`, `old_A`,
  `old_B`, `rmse`, `expanded_uncertainties`, `ref_temp_means`, `lsb_per_c`,
  `temp_nominali`, `dati_raw`, `risultati_elaborati`, `u_budget_per_step`,
  `ub_ref_y`, `ub_sensor_lsb`, `ub_ref_lsb`. If `convert_units=True`, also
  contains a `converted` sub-dict.

### `model_calibration/cubic_calibration.py` — Cubic polynomial engine

- **Mixed domain**: sensor X in LSB, reference Y in native physical unit (e.g. °C).
- Model: `T_ref = a0 + a1·D + a2·D² + a3·D³`
  where `D` is the raw sensor ADC reading in LSB.
- The model is linear in unknowns `[a0, a1, a2, a3]` given regressors
  `[1, D, D², D³]`, so OLS applies directly.
- Requires at least 4 calibration steps (one per coefficient).
- GUM propagation: analytical sensitivity coefficients of `theta_hat` wrt each
  `(x_i, y_i)` observation, accounting for the change in both `X'y` and `X'X`
  when the sensor reading `x_i` varies.
- `cubic_uncertainty()` propagates the full coefficient covariance and sensor
  reading uncertainty through the model at any point `D` via GUM Eq. 13.
- Accepts optional `old_a` / `old_b` / `old_c` / `old_d` (previous a0..a3).
  When all four are supplied, prints a baseline pre-fit error block (applied via
  `cubic_predict()`). Stored in the result dict as `old_a0` / `old_a1` / `old_a2`
  / `old_a3`.
- Returns dict with `model="cubic"`, `a0`…`a3`, `u_a0`…`u_a3`, `cov_theta`,
  `rmse`, `old_a0`…`old_a3`, `expanded_uncertainties`, `per_step_budget`,
  `ref_temp_means`, `lsb_per_c`, `temp_nominali`, `dati_raw`, `risultati_elaborati`,
  `ub_ref_y`, `ub_sensor_lsb`, `ub_ref_lsb`.

### `model_calibration/calib_plots.py` — Unified chart generator

- Produces standardised 5-chart PNG sets at 600 dpi for any calibration model:
  (1) sample timeseries, (2) raw scatter, (3) calibration curve, (4) pre-calibration
  error, (5) post-calibration residuals.
- Uses real per-point GUM combined standard uncertainty for all error bars.
- All calibration engines delegate chart generation to this single module.

### `calib_utils.py` — Shared utilities

- `_lookup` — finds a dict in a list by key=value.
- `SensorAccuracyChecker` — evaluates as-found error against `sensorAccuracy` ranges.
- `lsb_to_y()` / `y_to_lsb()` — domain conversion functions (generic physical unit).
- `round_to_significant_figures()` — rounding helper.
- Import from here; do not duplicate these helpers in other modules.

### `checks_helper.py` — Conformity check library (invoked inline)

- Implements checks G, A, B, H. Used inline by `analisi_calib_data.py`.
- Defines constants (`K_COPERTURA=2.0`, `U_PT_DEGC=0.065`, `D_TMP126_DEGC=0.30`, `ADC_BITS=16`).
- Uses `scipy.stats` for normal CDF in Check H (PFA).
- Provides `check_G()`, `check_A()`, `check_B()`, `check_H()`, `_parse_limit()`, `save_charts()`.

### `verify_dcc_conformity.py` — Standalone DCC XML verifier

- Parses a PTB DCC 3.3.0 XML file and runs 3 checks: Check G (sensor accuracy as-found),
  Check H (PFA), and an overlap/compatibility check.
- Uses pure `math.erf` (no scipy dependency).
- Generates 4 PNG charts. Parses XML (not JSON) — a distinct pipeline from the JSON-based checks in `checks_helper.py`.

### `model_calibration/unit_checks.py` — Dimensional analysis and unit conversion

- Pure functions module; no side effects. Imported lazily by each engine so that
  `pint` is only required when `--check-units` or `--convert-units` is passed.
- `check_dsi(sensor_json, ref_json, model) -> UnitCheckResult`
  - Reads `ranges.elec.dsi` and `ranges.phys.dsi` from raw JSON dicts.
  - Maps LaTeX DSI strings to pint unit strings via `_DSI_TO_PINT`.
  - Applies model-specific dimensional rules. Hard errors set `ok=False`; the engine
    raises `ValueError` and calibration is blocked.
- `convert_result(calib_result, sensor_json, ref_json) -> dict`
  - Converts temperature quantities from °C to the target unit declared in
    `ranges.phys.dsi`. Delta quantities (uncertainties) use magnitude-only conversion.
- `UnitCheckResult.print_report()` prints a colour-free PASS/FAIL summary to stdout.

### `certificato_funzione.py` — PDF generator

- ReportLab-based, 4 pages A4.
- Page 3: results table — all six columns (Point | T_ref | T_c_post | M_e_pre | M_e_post | U(E)).
- Accepts the `template_parts` grouped format produced by the orchestrator, or the
  legacy flat format.

### `generate_dcc_xml.py` — DCC XML generator

- PTB DCC schema version 3.3.0.
- Namespaces: `dcc`, `si`, `ds`, `xades`.
- Measurement data encoded as `realListXMLList` space-separated values.
- Reads 5-element measurement rows; columns 2 and 3 are treated as temperatures in °C;
  column 5 as expanded uncertainty.

### `verifica_conformita.py` — Conformity checker (standalone)

- Standalone post-pipeline tool; reads `certificato_funzione_filled.json`.
- Runs 4 metrological checks (G, A, B, H). See [conformity-checks.md](conformity-checks.md).
- Shares check logic with `checks_helper.py` (inline library variant used by the orchestrator).
- Check G runs before all others and has two ordered sub-checks:
  - G1: as-found error `|M_e_pre|` within the most restrictive `sensorAccuracy.maxError`
    for the point's temperature. FAIL if any error exceeds its limit.
  - G2: every calibration point covered by at least one `sensorAccuracy` range.
    WARN for regression models when a point falls outside all declared ranges.
  - Returns N/A when `sensorAccuracy` is absent from the sensor JSON.
- `sensorAccuracy` ranges are loaded directly from the sensor JSON at runtime.
- Optionally produces 3 matplotlib figures (residuals, GUM budget, calibration curve).

---

## Model layer — `models_in/`

Sensor and reference model definitions are plain JSON files loaded directly
via `json.loads()`. No class-based deserialisation.

### Directory layout

```
models_in/
  sensors.json                       ← DB-originated sensor definitions (optional)
  sensors/
    ntc_temperature.json             ← PRIMARY sensor model (default --sensor)
    ntc_temperature_kelvin.json
    pt100_temp.json
  references/
    fluke_9142.json                  ← PRIMARY reference model (default --ref)
    fluke_old.json
```

### Key values read from `sensors/ntc_temperature.json`

| JSON path | Pipeline use | Typical value |
|---|---|---|
| `ranges.threshold.min` / `.max` | LSB physical range bounds | −40.0 … 105.0 °C |
| `ranges.elec.adcBits` | ADC resolution | 16 |
| `calibration.type` | Default procedure | `"linear"` |
| `calibration.calibrationCoefficients.A.value` | Previous coeff A (0.0 = not set) | varies |
| `calibration.calibrationCoefficients.B.value` | Previous coeff B (0.0 = not set) | varies |
| `calibration.calibrationCoefficients.C.value` | Previous coeff C (0.0 = not set) | varies |
| `calibration.calibrationCoefficients.D.value` | Previous coeff D (0.0 = not set) | varies |
| `metrology.sensorAccuracy[]` | Accuracy gate + Check G | `[{tempMin, tempMax, maxError}]` |
| `metrology.readingUncertainty[uB].value` | Sensor type-B std uncertainty [LSB] | 0.30 |
| `metrology.readingUncertainty[absUncertainty].value` | Sensor absolute uncertainty [LSB] | 5.0 |
| `metrology.Uncertainty[0].absUncertainty` | Check B limit | 0.10 °C |

### Key values read from `references/fluke_9142.json`

| JSON path | Pipeline use | Typical value |
|---|---|---|
| `metrology.Uncertainty[0].ub` | Reference type-B std uncertainty | 0.15 °C |

---

## Data that is hardcoded vs. read from JSON

### Hardcoded (not in any JSON)

- ADC bits: 16
- Reference expanded uncertainty: 0.065 °C, k=2
- Sensor ADC half-width (uniform): 0.30 °C
- Sample block size: 20 readings/block
- Coverage factor k: 2
- Confidence level: 95 %
- OLS numerical tolerance constants

### Read from `sensors/ntc_temperature.json`

- LSB physical range (−40 °C … 105 °C)
- LSB electrical range (0 … 65535, 16-bit ADC)
- Sensor absolute uncertainty: 5.0 LSB; type-B std uncertainty: 0.30 LSB
- Calibration procedure type (e.g. `"linear"`)
- Previous calibration coefficients A, B, C, D (0.0 when unset)
- Sensor accuracy ranges (`metrology.sensorAccuracy[]`)
- Declared uncertainty limit (`metrology.Uncertainty[0].absUncertainty`)

### Read from `references/fluke_9142.json`

- Reference calibrator physical range
- Reference operating environment range
- Reference type-B uncertainty (`metrology.Uncertainty[0].ub`)

### Read from `certificato_funzione_input.json` (template)

- **Company data**: org name, address, phone, email, website, accreditation line
- **Organization data**: authorized by, executed by, signature name, traceability statement
- **Sensor metadata**: device type, manufacturer, model, serial number, calibration method, procedure code, traceability chain IDs
- **PDF layout strings**: labels, headers, footer text, page titles, intro text, notes lines

### Computed at runtime and injected into filled JSON

- A, B, u(A), u(B), cov(A,B) — or a0…a3 depending on procedure
- Per-step expanded uncertainties U(E)
- Per-step reference temperature means
- Measurement table rows (6 floats each: point, T_ref, T_sensor_post, M_e_pre, M_e_post, U_exp)
- Sensor model computed notes
- `_calibration_done`: `"done"` or `"not_necessary"`
- `_sensor_accuracy_check`: per-point as-found accuracy gate results
