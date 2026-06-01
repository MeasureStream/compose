# Data Flow

> Keep aligned with the code. Update when input/output formats change.

---

## End-to-end flow

```
[hardware measurement session]
        |
        v
export2_tmp126_lsb16.json          ntc_temperature.json     fluke_9142.json
(--input, data_in/ or test/data_in/) (--sensor, models_in/) (--ref, models_in/)
        |                               |                         |
        +---------------+---------------+-------------------------+
                        |
                        v
          analisi_calib_data.py
                        |
                        | loads models via VAR_REF_SENSOR.from_json()
                        v
              models_in/VAR_REF_SENSOR.py
              (SENSOR_model ← --sensor JSON)
              (RIFERIMENTO_model ← --ref JSON)
              (VAR_extra ← hardcoded ADC/PT100 constants)
                        |
                        | resolves previous coefficients (sensor.coeffA/B/C/D)
                        | (0.0 treated as "not set" → None)
                        |
                        | computes uncertainty parameters (ub_pt_lsb, ub_tmp_lsb, lsb_per_c)
                        |
                        | dispatches on --procedure / calibrationProcedure
                        |
              +---------+---------+---------+
              |                   |         |
    "linear"  |         "cubic"   |  "cube-log"
              v                   v         v
  model_calibration/   model_calibration/   model_calibration/
  linear_calibration   cubic_calibration    cube_log_calibration
  .calibrate()         .calibrate()         .calibrate()
  (old_a, old_b)       (old_a..old_d)
              |  [--check-units]  |         |
              |  unit_checks.check_dsi()    |
              |  → PASS: continue           |
              |  → FAIL: ValueError         |
              |          → orchestrator     |
              |            catches, exits 1 |
              |                   |         |
              |  [--convert-units]|         |
              |  unit_checks.convert_result()|
              |  → adds 'converted' sub-dict|
              |                   |         |
              +---------+---------+---------+
                        |
                        | returns calib_result dict
                        | (includes old_A/old_B or old_a0..old_a3 when supplied)
                        |
                        v
          sensor-accuracy gate
          _check_sensor_accuracy_in_range()
                        |
                        | reads metrology.sensorAccuracy from sensor JSON
                        | computes as-found errors using old coefficients (if present)
                        |   or raw LSB→°C (if no old coefficients)
                        | finds most restrictive maxError for each T_ref
                        |
          [--update-parameters-if-out-range-error]
                        |
              +---------+---------+
              |                   |
    all in range           at least one out of range
              |                   |
    calibration_done =    calibration_done = "done"
    "not_necessary"       (normal path)
    certificate keeps             |
    old / identity coeffs         |
    M_e_post = M_e_pre            |
              +---------+---------+
                        |
                        v
          _build_certificato_filled()
          M_e_pre = T_sensor(old coeffs) − T_ref
          M_e_post = T_sensor(new coeffs) − T_ref
                        |
                        | reads (read-only)
                        +---> template_in/certificato_funzione_input.json
                        |
                        | writes
                        v
          certificato_out/certificato_funzione_filled.json
          (_calibration_done, _sensor_accuracy_check embedded)
                        |
            +-----------+-----------+
            |                       |
            v                       v
  certificato_funzione.py    generate_dcc_xml.py
            |                       |
            v                       v
  certificato_out/           certificato_out/
  ntc_cert_funzione.pdf      ntc_calibration_certificate.xml

          [inline, always runs]
                        |
                        v
          verifica_conformita (inline call)
          check G (as-found sensor accuracy) → PASS/FAIL/WARN/N/A
          check A–F (post-calibration residuals, uncertainty budget, etc.)
          → conformity_summary embedded in pipeline output
          [optional: --conformity-output PATH]
          → conformity JSON written separately

          [optional, standalone]
                        |
                        v
          verifica_conformita.py (standalone script)
          reads: certificato_funzione_filled.json
          writes: stdout PASS/FAIL/WARN/N/A report
                  (optional) PNG figures in images/conformity/
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

Defaults to `models_in/ntc_temperature.json`. Schema: `schemaVersion 1.0.x`, `type: "temperature"`.

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

Defaults to `models_in/fluke_9142.json`. Schema: `schemaVersion 1.0.0`, `type: "temperature_calibrator"`.

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
| `model` | str | — | Procedure identifier: `"linear"`, `"cubic"`, `"cube-log"` |
| `lsb_per_c` | float | LSB/°C | Conversion factor |
| `temp_nominali` | list[float] | °C | Nominal step temperatures |
| `expanded_uncertainties` | list[float] | °C | U(E) per step, k=2 |
| `ref_temp_means` | list[float] | °C | Mean PT100 temperature per step |
| `dati_raw` | dict | — | Per-step raw arrays `{rtd, log}` |
| `risultati_elaborati` | dict | — | Per-step statistics |
| `ub_pt_lsb` | float | LSB | Type-B standard uncertainty PT100 |
| `ub_tmp_lsb` | float | LSB | Type-B standard uncertainty NTC ADC |
| `calibration_done` | str | — | `"done"` or `"not_necessary"` (set by accuracy gate) |
| `_sensor_accuracy_check` | dict\|None | — | `{all_in_range, per_point[]}` from the accuracy gate; `None` if `sensorAccuracy` absent |
| `converted` | dict | target unit | Present only when `--convert-units` is set |

### Model-specific keys

**`"linear"`** — `T_ref_lsb = A·D + B`

| Key | Type | Unit | Description |
|---|---|---|---|
| `A` | float | — | OLS gain (dimensionless) |
| `B` | float | LSB | OLS offset |
| `u_A` | float | — | Standard uncertainty of A (GUM) |
| `u_B` | float | LSB | Standard uncertainty of B (GUM) |
| `cov_AB` | float | LSB | Covariance between A and B |
| `old_A` | float\|None | — | Previous A from `sensor.coeffA`; `None` when unset |
| `old_B` | float\|None | LSB | Previous B from `sensor.coeffB`; `None` when unset |

**`"cubic"`** — `T_ref_lsb = a0 + a1·D + a2·D² + a3·D³`

| Key | Type | Unit | Description |
|---|---|---|---|
| `a0`…`a3` | float | LSB / dimensionless | Polynomial coefficients |
| `u_a0`…`u_a3` | float | same | Standard uncertainties (GUM) |
| `cov_theta` | list[list[float]] | — | 4×4 covariance matrix |
| `theta` | list[float] | — | `[a0, a1, a2, a3]` |
| `old_a0` | float\|None | LSB | Previous a0 from `sensor.coeffA`; `None` when unset |
| `old_a1` | float\|None | — | Previous a1 from `sensor.coeffB`; `None` when unset |
| `old_a2` | float\|None | — | Previous a2 from `sensor.coeffC`; `None` when unset |
| `old_a3` | float\|None | — | Previous a3 from `sensor.coeffD`; `None` when unset |

**`"cube-log"`** — `1/T[K⁻¹] = C0 + C1·ln(D) + C3·(ln(D))³`

| Key | Type | Unit | Description |
|---|---|---|---|
| `C0`, `C1`, `C3` | float | K⁻¹ | Steinhart-Hart coefficients |
| `u_C0`, `u_C1`, `u_C3` | float | K⁻¹ | Standard uncertainties (GUM) |
| `cov_theta` | list[list[float]] | K⁻² | 3×3 covariance matrix |
| `theta` | list[float] | — | `[C0, C1, C3]` |
| `per_step_budget` | list[dict] | — | Per-step uncertainty budget including `u_SH_K` |

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
      "ntc_model": {
        ...                          ← template NTC params +
        "_calib_model": "linear"|"cubic"|"cube-log",
        "_A_cal": ...,               ← linear only
        "_B_cal": ...,               ← linear only
        "_C0": ...,                  ← cube-log only
        "_theta": [...],             ← cubic / cube-log
        "_cov_theta": [[...]],       ← cubic / cube-log
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
    "_calib_model": "linear"|"cubic"|"cube-log",
    "_lsb_per_c": ...,
    "_expanded_uncertainties_degC": [...],
    "_ref_temp_means_degC": [...],
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
        "T_ref_degC": ...,
        "as_found_error_degC": ...,
        "max_allowed_error_degC": ...,
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
[point, T_ref_degC, T_c_post_degC, M_e_pre_degC, M_e_post_degC, U_exp_degC]
```

| Index | Name | Description |
|---|---|---|
| 0 | `point` | Sequential integer (1-based) |
| 1 | `T_ref_degC` | Mean PT100 reference temperature at step [°C] |
| 2 | `T_c_post_degC` | Post-calibration NTC temperature [°C] |
| 3 | `M_e_pre_degC` | As-found error: T_sensor(old coeffs) − T_ref [°C]; raw LSB→°C when no old coefficients |
| 4 | `M_e_post_degC` | As-left error: T_c_post − T_ref [°C]; equals M_e_pre when calibration_done=not_necessary |
| 5 | `U_exp_degC` | Expanded uncertainty U(E) [°C], k=2 |

The PDF results table shows all six columns. The DCC XML uses only columns
1, 2, 4, 5 (T_ref, T_c_post, M_e_post, U_exp) for the measurement quantities.

Backwards compatibility: `certificato_funzione.py` accepts rows with 5 elements
(old format without M_e_pre) and renders them in 5-column mode.

---

## Domain convention — mixed domain (sensor LSB, reference °C)

```
Sensor axis (D_out):   [0, 65535] LSB   (raw unsigned 16-bit ADC readings)
Reference axis (PT100): [−40 … 125] °C  (native PT100 / Fluke readings)

Calibration function:  T [°C] = f(D [LSB])
  Linear:         T = A·D + B           A [°C/LSB], B [°C]
  Cubic:          T = a0 + a1·D + a2·D² + a3·D³
  Steinhart-Hart: 1/T [K⁻¹] = C0 + C1·ln(D) + C3·(ln D)³
```

`lsb_per_c` (≈ 451.97 LSB/°C) is computed and stored as an **informational field** only.
It is NOT used to convert uncertainties or calibration coefficients.
Reference readings are stored in native °C; they are never converted to synthetic LSB.
The certificate table and XML already receive values in °C from the engines directly.

---

## Previous-coefficient flow

```
ntc_temperature.json
calibration.calibrationCoefficients.{A,B,C,D}
        |
        v
SENSOR_model.coeffA / coeffB / coeffC / coeffD
        |
        | orchestrator: 0.0 → None  (not-set sentinel)
        v
_old_A / _old_B / _old_C / _old_D
        |
        +-----> passed to engine as old_a / old_b / old_c / old_d
        |       engine stores as old_A/old_B or old_a0..old_a3 in calib_result
        |
        +-----> used in _build_certificato_filled() to compute M_e_pre
        |       (T_sensor with old coefficients applied, not raw LSB)
        |
        +-----> used in accuracy gate to compute as-found errors
        |       (same old-coefficient application, consistent with M_e_pre)
        |
        +-----> used in calibration_skipped patch block
                (restored as both as-found and as-left when not_necessary)
```
