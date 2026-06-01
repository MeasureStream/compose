# Architecture

> Keep aligned with the code. Update when scripts, models, or folder layout change.

---

## Pipeline stages

| # | Stage | Script | Input | Output |
|---|---|---|---|---|
| 1 | Orchestration | `analisi_calib_data.py` | LSB16 JSON (`--input`) + sensor JSON (`--sensor`) + ref JSON (`--ref`) | drives stages 2–6 |
| 2a | Calibration (linear) | `model_calibration/linear_calibration.py` | LSB16 payload dict + old A/B | A, B, uncertainties, old_A/old_B dict |
| 2b | Calibration (cubic) | `model_calibration/cubic_calibration.py` | LSB16 payload dict + old a0..a3 | a0…a3, cov_theta, uncertainties, old_a0..old_a3 dict |
| 2c | Calibration (cube-log) | `model_calibration/cube_log_calibration.py` | LSB16 payload dict | C0, C1, C3, uncertainties dict |
| 3 | Sensor-accuracy gate | `analisi_calib_data._check_sensor_accuracy_in_range()` | as-found errors + `sensorAccuracy` ranges | skip/proceed decision + `_sensor_accuracy_check` dict |
| 4 | Certificate JSON | `analisi_calib_data._build_certificato_filled()` | template + calib result | `certificato_funzione_filled.json` |
| 5 | PDF certificate | `certificato_funzione.py` | filled JSON | `ntc_cert_funzione.pdf` |
| 6 | DCC XML | `generate_dcc_xml.py` | filled JSON | `ntc_calibration_certificate.xml` |
| — | Conformity check | `verifica_conformita.py` | filled JSON | PASS/FAIL/WARN/N/A report to stdout |

---

## Key scripts

### `analisi_calib_data.py` — Orchestrator

- Bootstraps `sys.path` to include `models_in/` (for `VAR_REF_SENSOR`) and
  `scripts/` (for the `model_calibration` package and other modules).
- Loads `SENSOR_model` from `--sensor` JSON and `RIFERIMENTO_model` from `--ref` JSON
  via `VAR_REF_SENSOR.from_json()`. Both default to files already in `models_in/`.
  `VAR_extra` remains hardcoded (ADC bits, PT100 uncertainty constants).
- Resolves previous firmware coefficients from `sensor.coeffA/B/C/D` (zero is treated
  as the "not set" sentinel → `None`). These are passed to each engine as `old_a/old_b`
  (linear) or `old_a/old_b/old_c/old_d` (cubic) and used throughout for as-found error
  computation and the skipped-calibration path.
- Dispatches calibration on `--procedure` CLI flag (or `sensor.calibrationProcedure`
  if flag is omitted):
  - `"linear"` or `"qubic-interpolation"` → `model_calibration.linear_calibration.calibrate()`
  - `"cubic"` → `model_calibration.cubic_calibration.calibrate()`
  - `"cube-log"` → `model_calibration.cube_log_calibration.calibrate()`
  - Any other value → exits with error.
- After calibration, evaluates the **sensor-accuracy gate** (step 3):
  reads `metrology.sensorAccuracy` from the sensor JSON, computes the as-found
  error at each step (applying old coefficients when available, raw LSB→°C otherwise),
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
- Runs the full conformity check suite inline (checks G, A–F) and optionally writes
  a conformity JSON when `--conformity-output` is set.
- When `--check-units` is set, passes `sensor._data` and `fluke._data` to each
  engine's `calibrate()` call. The engine calls `unit_checks.check_dsi()` before
  regression; on hard dimensional errors it raises `ValueError` which the
  orchestrator catches, prints, and exits with code 1.
- When `--convert-units` is set, the engine calls `unit_checks.convert_result()`
  after regression and returns a `converted` sub-dict.
- Invokes `certificato_funzione.py` and `generate_dcc_xml.py` in-process;
  falls back to subprocess if import fails.

### `model_calibration/linear_calibration.py` — Linear calibration engine

- All maths in the **16-bit LSB domain** — no °C conversion during regression.
- Model: `T_ref_lsb = A * T_sensor_lsb + B`
- Statistic: GUM OLS with full analytical uncertainty propagation (sensitivity
  coefficients for every data point).
- Sample grouping: raw readings grouped into blocks of `sample_size=20`; per-block
  mean and std computed; population mean and std of block means used as inputs to OLS.
- Type A uncertainty: population std of block means (per step).
- Type B uncertainty: passed in as `ub_pt_lsb` (PT100) and `ub_tmp_lsb` (NTC ADC).
- Expanded uncertainty `U(E)` at each step: `2 × √(u(T_ref)² + u(T_i)²)` where
  both `u` include type A and type B components.
- Accepts optional `old_a` / `old_b` (previous firmware coefficients). When supplied,
  prints a baseline pre-fit error block (signed mean, max abs, per-step values in
  LSB and °C). Stored in the result dict as `old_A` / `old_B`.
- Returns dict with `model="linear"`, `A`, `B`, `u_A`, `u_B`, `cov_AB`, `old_A`,
  `old_B`, `expanded_uncertainties`, `ref_temp_means`, `lsb_per_c`, `temp_nominali`,
  `dati_raw`, `risultati_elaborati`. If `convert_units=True`, also contains a
  `converted` sub-dict.

### `model_calibration/cubic_calibration.py` — Cubic polynomial engine

- All maths in the **16-bit LSB domain**.
- Model: `T_ref_lsb = a0 + a1·D + a2·D² + a3·D³`
  where `D` is the raw NTC ADC reading in LSB.
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
  `old_a0`…`old_a3`, `expanded_uncertainties`, `per_step_budget`, `ref_temp_means`,
  `lsb_per_c`, `temp_nominali`, `dati_raw`, `risultati_elaborati`.

### `model_calibration/cube_log_calibration.py` — Cubic-log (Steinhart-Hart) engine

- All maths in the **16-bit LSB domain**.
- Model: `1/T [K⁻¹] = C0 + C1·ln(D) + C3·(ln(D))³`  (Steinhart-Hart equation)
  where `D` is the raw NTC ADC reading in LSB.
- The model is linear in unknowns `[C0, C1, C3]` once regressors
  `[1, ln(D), (ln(D))³]` are formed, so OLS is applied directly.
- Requires at least 3 calibration steps (one per coefficient).
- GUM propagation: analytical sensitivity coefficients of `theta_hat` with respect
  to each observation `(x_i, y_i)`.
- `steinhart_hart_uncertainty()` propagates the full coefficient covariance and
  sensor reading uncertainty through the nonlinear model via GUM Eq. 13.
- Returns dict with `model="cube-log"`, `C0`, `C1`, `C3`, `u_C0`, `u_C1`, `u_C3`,
  `cov_theta`, `expanded_uncertainties`, `per_step_budget`, `ref_temp_means`,
  `lsb_per_c`, `temp_nominali`, `dati_raw`, `risultati_elaborati`.

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

### `verifica_conformita.py` — Conformity checker

- Standalone post-pipeline tool; reads `certificato_funzione_filled.json`.
- Runs 7 metrological checks (G first, then A–F). See [conformity-checks.md](conformity-checks.md).
- Check G runs before all others and has two ordered sub-checks:
  - G1: as-found error `|M_e_pre|` within the most restrictive `sensorAccuracy.maxError`
    for the point's temperature. FAIL if any error exceeds its limit.
  - G2: every calibration point covered by at least one `sensorAccuracy` range.
    WARN for regression models when a point falls outside all declared ranges.
  - Returns N/A when `sensorAccuracy` is absent from the sensor JSON.
- `sensorAccuracy` ranges are loaded directly from `ntc_temperature.json` at runtime.
- Optionally produces 3 matplotlib figures (residuals, GUM budget, calibration curve).

---

## Model layer — `models_in/`

### `VAR_REF_SENSOR.py`

Three dataclasses:

| Class | Default JSON | CLI arg | Purpose |
|---|---|---|---|
| `SENSOR_model` | `models_in/ntc_temperature.json` | `--sensor` | NTC physical, metrology, and previous-calibration parameters |
| `RIFERIMENTO_model` | `models_in/fluke_9142.json` | `--ref` | Reference calibrator parameters |
| `VAR_extra` | — (hardcoded) | — | ADC resolution + reference uncertainty constants |

`SENSOR_model()` / `RIFERIMENTO_model()` (no args) load from `BASE_DIR` next to the file.
`SENSOR_model.from_json(path)` / `RIFERIMENTO_model.from_json(path)` load from an
explicit path — used by the orchestrator when a CLI path is supplied.

**Must stay in `models_in/`** — `BASE_DIR = Path(__file__).resolve().parent` resolves
relative to its own location.

Key values used by the pipeline:

| Attribute | Class | Source | Value |
|---|---|---|---|
| `_adc_bits` | `VAR_extra` | hardcoded | 16 |
| `_U_pt_c` | `VAR_extra` | hardcoded | 0.065 °C (expanded, k=2) |
| `_k_pt` | `VAR_extra` | hardcoded | 2.0 |
| `_d_tmp126_c` | `VAR_extra` | hardcoded | 0.30 °C (half-width, uniform) |
| `_minPhyThreshold` | `SENSOR_model` | `ntc_temperature.json` ranges.threshold.min | −40.0 °C |
| `_maxPhyThreshold` | `SENSOR_model` | `ntc_temperature.json` ranges.threshold.max | 105.0 °C |
| `absUncertainty` | `SENSOR_model` | `ntc_temperature.json` metrology.readingUncertainty | 5.0 LSB |
| `resolution_degC` | `SENSOR_model` | hardcoded in dataclass | 0.01 °C |
| `calibrationProcedure` | `SENSOR_model` | `ntc_temperature.json` calibration.type | `"qubic-interpolation"` |
| `coeffA` | `SENSOR_model` | `ntc_temperature.json` calibration.calibrationCoefficients.A | previous linear A (0.0 = not set) |
| `coeffB` | `SENSOR_model` | `ntc_temperature.json` calibration.calibrationCoefficients.B | previous linear B [LSB] (0.0 = not set) |
| `coeffC` | `SENSOR_model` | `ntc_temperature.json` calibration.calibrationCoefficients.C | previous cubic a2 (0.0 = not set) |
| `coeffD` | `SENSOR_model` | `ntc_temperature.json` calibration.calibrationCoefficients.D | previous cubic a3 (0.0 = not set) |
| `R25`, `B25_85` | `SENSOR_model` | hardcoded in dataclass | 10000 Ω, 3950 K |

The `coeffA/B/C/D` fields store the previous calibration's coefficients.
The orchestrator treats `0.0` as the "not set" sentinel (→ `None`), so a freshly
manufactured sensor with all-zero coefficients in the JSON is treated identically
to a sensor with no prior calibration.

---

## Data that is hardcoded vs. read from JSON

### Hardcoded (not in any JSON)

- ADC bits: 16
- PT100 expanded uncertainty: 0.065 °C, k=2
- NTC ADC half-width (uniform): 0.30 °C
- Sensor resolution: 0.01 °C
- Sample block size: 20 readings/block
- Coverage factor k: 2
- Confidence level: 95 %
- OLS numerical tolerance constants

### Read from `ntc_temperature.json`

- LSB physical range (−40 °C … 105 °C)
- LSB electrical range (0 … 65535)
- NTC absolute uncertainty: 5.0 LSB
- Calibration procedure type: `"qubic-interpolation"`
- Previous calibration coefficients A, B, C, D (0.0 when unset)
- Sensor accuracy ranges (`metrology.sensorAccuracy[]`)

### Read from `fluke_9142.json`

- Reference calibrator physical range
- Reference operating environment range

### Read from `certificato_funzione_input.json` (template)

- **Company data**: org name, address, phone, email, website, accreditation line
- **Organization data**: authorized by, executed by, signature name, traceability statement
- **Sensor metadata**: device type, manufacturer, model, serial number, calibration method, procedure code, traceability chain IDs
- **PDF layout strings**: labels, headers, footer text, page titles, intro text, notes lines

### Computed at runtime and injected into filled JSON

- A, B, u(A), u(B), cov(A,B) — or a0…a3 / C0, C1, C3 depending on procedure
- Per-step expanded uncertainties U(E)
- Per-step reference temperature means
- Measurement table rows (6 floats each: point, T_ref, T_c_post, M_e_pre, M_e_post, U_exp)
- NTC model computed notes
- `_calibration_done`: `"done"` or `"not_necessary"`
- `_sensor_accuracy_check`: per-point as-found accuracy gate results
