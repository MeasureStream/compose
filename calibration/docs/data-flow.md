# Data Flow

> Keep aligned with the code. Update when input/output formats change.

---

## End-to-end pipeline

```
[hardware measurement session]

  Three artifacts produced during the session:

    ┌── export2_tmp126_lsb16.json  (--input)  · raw ADC readings, timestamped per step
    ├── ntc_temperature.json       (--sensor) · sensor model — coefficients, accuracy ranges, ranges
    └── fluke_9142.json            (--ref)    · reference calibrator — physical range, uncertainty model
                              │
                              ▼
                     analisi_calib_data.py
                              │
     ┌─ Load models ─────────┤
     │  JSON files are loaded directly via json.loads().  No class-based
     │  deserialisation; all metadata (ADC bits, ranges, uncertainties,
     │  previous coefficients) is read from the raw dicts on demand.
     │
     ├─ Resolve prior state ─┤
     │  coeffA/B/C/D are read from the sensor model.  A value of 0.0 is
     │  treated as "never set" and replaced with None.
     │
     ├─ Compute budget —──────┤
     │  Type-B uncertainties — ub_ref_y (reference, native Y unit) and
     │  ub_sensor_lsb (sensor ADC, LSB) — plus the informational conversion
     │  factor lsb_per_c are derived from the sensor and reference models.
     │
     ├─ Dispatch calibration ─┤
     │  The `--procedure` flag (or `calibration.type` from the sensor JSON)
     │  selects one of two engines:
     │
     │    • linear  → model_calibration/linear_calibration.calibrate()
     │    • cubic   → model_calibration/cubic_calibration.calibrate()
     │
     │  Legacy alias "qubic-interpolation" maps to "linear".
     │  Each engine receives any previously stored coefficients so it can
     │  compute as-found errors alongside the new fit.
     │
     ├─ Unit checks (opt.) ─┤
     │  --check-units    runs dimensional analysis via pint; a mismatch raises
     │                   ValueError, caught by the orchestrator → exit(1).
     │  --convert-units  appends a `converted` sub-dict with results in the
     │                   target physical unit.
     │
     ├─ Sensor-accuracy gate ─┤
     │  The `sensorAccuracy` array in the sensor model declares permitted
     │  error bands (tempMin, tempMax, maxError).  For each reference point:
     │    · compute as-found error  (old coefficients → °C, or raw LSB → °C)
     │    · pick the tightest maxError whose band covers T_ref
     │
     │  Decision (driven by --update-parameters-if-out-range-error):
     │
     │    all points in range                      any point out of range
     │    └─ calibration_done = "not_necessary"    └─ calibration_done = "done"
     │       old coefficients kept as-is              new coefficients applied
     │       M_e_post = M_e_pre
     │
     ├─ Build certificate JSON ─┤
     │  _build_certificato_filled() merges:
     │    · template_in/certificato_funzione_input.json  (read-only skeleton)
     │    · the calib_result dict                        (computed values)
     │    · the sensor-accuracy verdict                  (_sensor_accuracy_check)
     │
     │  Output: certificato_out/certificato_funzione_filled.json
     │
     ├─ Generate artifacts ───┤
     │  certificato_funzione.py  →  ntc_cert_funzione.pdf
     │  generate_dcc_xml.py      →  ntc_calibration_certificate.xml
     │
     └─ Conformity verification ─┤
        Runs inline after every pipeline invocation; also available standalone.
          · Check G — as-found sensor accuracy  →  PASS / FAIL / WARN / N/A
          · Check A — post-calibration residual vs expanded uncertainty
          · Check B — expanded uncertainty vs declared limit
          · Check H — PFA (probability of false acceptance) test
          · --conformity-output PATH  writes a separate conformity JSON

        Standalone script: verifica_conformita.py
          Input:  certificato_funzione_filled.json
          Output: stdout verdict report  (+ optional PNG charts)
```

---

## Input: LSB16 payload JSON (`--input`)

```json
{
  "calibration_id": "calib-tmp126-2026-04-03T12:27:43",
  "mu_id": 102,
  "sensor_id": 103,
  "steps": ["(0.0,1)", "(25.0,1)", "(50.0,1)", "(75.0,1)", "(100.0,1)", "(125.0,1)"],
  "reference_temperature_samples": [
    { "index_step": 0, "timestamp": "...", "reading": 0.243331 }
  ],
  "sensor_raw_samples": [
    { "index_step": 0, "value": [33100, 33102, 33098] }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `steps` | `["(T_nom,n)"]` | Nominal temperature °C and repeat count per step |
| `reference_temperature_samples[].reading` | float | PT100 reading in °C |
| `sensor_raw_samples[].value` | list[int] | NTC ADC raw unsigned 16-bit integers |

## Input: sensor model JSON (`--sensor`)

Defaults to `models_in/sensors/ntc_temperature.json`. Schema: `schemaVersion 1.0.x`, `type: "temperature"`.

Key fields consumed by the pipeline:

| JSON path | Pipeline attribute / use | Description |
|---|---|---|
| `ranges.threshold.min` / `.max` | `_minPhyThreshold` / `_maxPhyThreshold` | LSB physical range bounds [°C] |
| `ranges.elec.max` | `maxElecVal` | ADC full-scale (65535 for 16-bit) |
| `ranges.elec.dsi` | `unit_checks` | LaTeX DSI string for sensor electrical unit |
| `ranges.phys.dsi` | `unit_checks` and `--convert-units` target | LaTeX DSI string for sensor physical unit |
| `metrology.readingUncertainty[varName=absUncertainty].value` | `absUncertainty` | NTC type-B absolute uncertainty [LSB] |
| `calibration.type` | `calibrationProcedure` | Default procedure if `--procedure` is omitted |
| `calibration.calibrationCoefficients.A` | `coeffA` → `_old_A` | Previous linear A (0.0 = not set) |
| `calibration.calibrationCoefficients.B` | `coeffB` → `_old_B` | Previous linear B in LSB (0.0 = not set) |
| `calibration.calibrationCoefficients.C` | `coeffC` → `_old_C` | Previous cubic a2 (0.0 = not set) |
| `calibration.calibrationCoefficients.D` | `coeffD` → `_old_D` | Previous cubic a3 (0.0 = not set) |
| `metrology.sensorAccuracy[]` | accuracy gate + check G | List of `{tempMin, tempMax, maxError}` ranges |

### `sensorAccuracy` range evaluation

For each calibration point at `T_ref`, the pipeline collects all `sensorAccuracy`
entries whose `[tempMin, tempMax]` interval contains `T_ref`, then takes the
**minimum** `maxError` among them (most restrictive). If no range covers the point,
`maxError = +inf` (point is outside all declared ranges — possible for regression
when extrapolating).

## Input: reference calibrator JSON (`--ref`)

Defaults to `models_in/references/fluke_9142.json`. Schema: `schemaVersion 1.0.0`, `type: "temperature_calibrator"`.

| JSON path | Pipeline attribute / use | Description |
|---|---|---|
| `ranges.phys.min` / `.max` | `minPhysVal` / `maxPhysVal` | Reference physical range [°C] |
| `ranges.phys.dsi` | `unit_checks` | LaTeX DSI string for reference physical unit |
| `metrology.evaluationFormula` | `evaluationFormula` | Uncertainty combination method |
| `metrology.UncertaintyPdf` | `UncertaintyPdf` | PDF type for reference uncertainty |

---

## `calib_result` dict (common keys — all models)

| Key | Type | Unit | Description |
|---|---|---|---|
| `model` | str | — | Procedure identifier: `"linear"` or `"cubic"` |
| `lsb_per_c` | float | LSB/°C | Conversion factor |
| `temp_nominali` | list[float] | °C | Nominal step temperatures |
| `expanded_uncertainties` | list[float] | °C | U(E) per step, k=2 |
| `ref_temp_means` | list[float] | °C | Mean reference temperature per step |
| `dati_raw` | dict | — | Per-step raw arrays `{ref, sensor}` |
| `risultati_elaborati` | dict | — | Per-step statistics |
| `ub_ref_y` | float | Y | Type-B standard uncertainty of reference, in native physical unit |
| `ub_sensor_lsb` | float | LSB | Type-B standard uncertainty of sensor ADC |
| `ub_ref_lsb` | float | LSB | Legacy compat alias: `ub_ref_y * lsb_per_c` |
| `calibration_done` | str | — | `"done"` or `"not_necessary"` (set by accuracy gate) |
| `_sensor_accuracy_check` | dict\|None | — | `{all_in_range, per_point[]}` from the accuracy gate; `None` if `sensorAccuracy` absent |
| `converted` | dict | target unit | Present only when `--convert-units` is set |

### Model-specific keys

**`"linear"`** — `T = A·D + B` (Y domain: both axes in physical units)

| Key | Type | Unit | Description |
|---|---|---|---|
| `A` | float | Y/LSB | OLS gain |
| `B` | float | Y | OLS offset |
| `u_A` | float | Y/LSB | Standard uncertainty of A (GUM) |
| `u_B` | float | Y | Standard uncertainty of B (GUM) |
| `cov_AB` | float | Y²/LSB | Covariance between A and B |
| `old_A` | float\|None | — | Previous A from `sensor.coeffA`; `None` when unset |
| `old_B` | float\|None | Y | Previous B from `sensor.coeffB`; `None` when unset |
| `rmse` | float | Y | Root mean squared error (N−2 corrected) |
| `u_budget_per_step` | list[dict] | — | Per-step GUM budget breakdown |

**`"cubic"`** — `T = a0 + a1·D + a2·D² + a3·D³` (mixed domain: LSB X, Y output)

| Key | Type | Unit | Description |
|---|---|---|---|
| `a0`…`a3` | float | Y / dimensionless | Polynomial coefficients |
| `u_a0`…`u_a3` | float | same | Standard uncertainties (GUM) |
| `cov_theta` | list[list[float]] | — | 4×4 covariance matrix |
| `theta` | list[float] | — | `[a0, a1, a2, a3]` |
| `old_a0` | float\|None | Y | Previous a0 from `sensor.coeffA`; `None` when unset |
| `old_a1` | float\|None | — | Previous a1 from `sensor.coeffB`; `None` when unset |
| `old_a2` | float\|None | — | Previous a2 from `sensor.coeffC`; `None` when unset |
| `old_a3` | float\|None | — | Previous a3 from `sensor.coeffD`; `None` when unset |
| `rmse` | float | Y | Root mean squared error (N−4 corrected) |
| `per_step_budget` | list[dict] | — | Per-step GUM budget breakdown |

---

## `certificato_funzione_filled.json` structure

```
{
  "template_parts": {
    "company_data":                  ← copied verbatim from template
    "organization_data":             ← copied verbatim from template
    "sensor_method_template": {
      ...                            ← template fields +
      "_notes_computed": [...],      ← computed
      "sensor_model": {              ← WAS: "ntc_model"
        ...                          ← template NTC params +
        "_calib_model": "linear"|"cubic",
        "_A_cal": ...,               ← linear only
        "_B_cal": ...,               ← linear only
        "_theta": [...],             ← cubic only
        "_cov_theta": [[...]],       ← cubic only
        ...
      }
    },
    "calibration_specific_data":     ← copied verbatim from template
    "calculated_calibration_values": {
      "measurements": [...],         ← fully computed, 6 floats per row
      "observations": [...],
      "conclusions": "..."
    },
    "pdf_template_data":             ← copied verbatim from template
  },
  "_calibration_result": {
    "_calib_model": "linear"|"cubic",
    "_calibration_procedure": "linear"|"cubic",
    "_lsb_per_c": ...,
    "_expanded_uncertainties_phys": [...],
    "_ref_means_phys": [...],
    "_temp_nominali": [...],
    "_variant": "funzione",
    ...                              ← model-specific coefficient keys
  },
  "_calibration_done": "done"|"not_necessary",
  "_sensor_accuracy_check": {
    "all_in_range": true|false,
    "per_point": [
      {
        "point": 1,
        "T_ref_y": ...,
        "as_found_error_y": ...,
        "max_allowed_error_y": ...,
        "in_range": true|false
      },
      ...
    ]
  }
}
```

Convention: all keys prefixed with `_` are computed at runtime.
Keys without `_` prefix are copied from the human-authored template.

When `_calibration_done = "not_necessary"`:
- Coefficient keys hold the previous (old) values — or identity (`A=1, B=0` /
  `a0=0, a1=1, a2=0, a3=0`) when no old coefficients were available.
- `M_e_post == M_e_pre` for every measurement row.
- `T_c_post = T_ref + M_e_pre` (the uncorrected reading).

---

## Measurement row format

Every measurement row in `calculated_calibration_values.measurements` has
**exactly 6 floats**:

```
[point, T_ref, T_sensor_post, M_e_pre, M_e_post, U_exp]
```

| Index | Name | Description |
|---|---|---|
| 0 | `point` | Sequential integer (1-based) |
| 1 | `T_ref` | Mean reference temperature at step [Y] |
| 2 | `T_sensor_post` | Post-calibration sensor temperature [Y] |
| 3 | `M_e_pre` | As-found error: T_sensor(old coeffs) − T_ref [Y]; raw LSB→Y when no old coefficients |
| 4 | `M_e_post` | As-left error: T_sensor_post − T_ref [Y]; equals M_e_pre when calibration_done=not_necessary |
| 5 | `U_exp` | Expanded uncertainty U(E) [Y], k=2 |

The PDF results table shows all six columns. The DCC XML uses only columns
1, 2, 4, 5 (T_ref, T_sensor_post, M_e_post, U_exp) for the measurement quantities.

---

## Domain convention — mixed domain (sensor LSB, reference Y)

```
Sensor axis (D_out):   [0, 65535] LSB   (raw unsigned 16-bit ADC readings)
Reference axis:         physical Y      (native reference readings, e.g. °C)

Calibration function:  Y = f(D [LSB])
  Linear:         Y = A·D + B           A [Y/LSB], B [Y]
  Cubic:          Y = a0 + a1·D + a2·D² + a3·D³
```

`lsb_per_c` (≈ 451.97 LSB/°C) is computed and stored as an **informational field** only.
It is NOT used to convert uncertainties or calibration coefficients.
Reference readings are stored in native Y; they are never converted to synthetic LSB.
The certificate table and XML already receive values in the physical unit from the engines directly.

---

## Previous-coefficient flow

```
sensor JSON (e.g. ntc_temperature.json)   OR   CLI --old-a/b/c/d (from DB)
calibration.calibrationCoefficients.{A,B,C,D}
        |
        v
_get_calib_coeff() reads each coeff from JSON
        |
        | orchestrator: 0.0 → None  (not-set sentinel)
        | CLI --old-a/b/c/d overrides JSON values when present
        v
old_A / old_B / old_C / old_D  (float or None)
        |
        +-----> passed to engine as old_a / old_b / old_c / old_d
        |       engine stores as old_A/old_B or old_a0..old_a3 in calib_result
        |
        +-----> used in _build_cert_filled() to compute M_e_pre
        |       (T_sensor with old coefficients applied, not raw LSB)
        |
        +-----> used in accuracy gate to compute as-found errors
        |       (same old-coefficient application, consistent with M_e_pre)
        |
        +-----> used in _apply_calibration_skipped() patch block
                (restored as both as-found and as-left when not_necessary)
```
