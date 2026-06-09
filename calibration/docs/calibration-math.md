# Calibration Math

What the pipeline computes, how it gets there, and what ends up in the certificate.

## Domain convention (mixed domain)

Sensor readings (D_out) stay in the **electrical domain [LSB]** — that is what the hardware
delivers. Reference readings (PT100) stay in the **physical domain [°C]** — that is what the
Fluke/PT100 delivers. The calibration function maps LSB → °C directly:

```
Linear:         T [°C]    = A [°C/LSB] · D [LSB] + B [°C]
Cubic:          T [°C]    = a0 + a1·D + a2·D² + a3·D³   (a_k in °C/LSBᵏ)
Steinhart-Hart: 1/T [K⁻¹] = C0 + C1·ln(D) + C3·(ln D)³
```

The quantity `lsb_per_c` (≈ 451.97 for a 16-bit ADC over 145 °C) is computed and stored
as an **informational field only**. It is no longer used to convert uncertainties or
coefficients. Dividing any quantity by `lsb_per_c` to obtain °C is incorrect for the
cubic and Steinhart-Hart models because those models are nonlinear.

Reference uncertainty `ub_ref_y` is expressed in °C (native). Sensor ADC uncertainty
`ub_sensor_lsb` is expressed in LSB (native). At each calibration step the LSB uncertainty
is converted to °C by multiplying by the local sensitivity `|dT/dD|`:

```
uA_ntc_i [°C] = pstd_log_i [LSB] × |dT/dD|_i [°C/LSB]
uB_sensor   [°C] = ub_sensor_lsb [LSB] × |dT/dD|_i [°C/LSB]

Linear:         dT/dD = A              (constant)
Cubic:          dT/dD = a1 + 2·a2·D + 3·a3·D²
Steinhart-Hart: dT/dD = −T²·(C1/D + 3·C3·(ln D)²/D)   [K/LSB]
```

---

## Starting data — instruments and JSON parameters

### Reference instrument (Fluke 1502A + PT100)

Source: hardcoded constants (not from JSON):

```python
_U_pt_c = 0.065    # expanded uncertainty [°C], k=2
_k_pt   = 2.0      # coverage factor
```

Standard uncertainty of the reference (type B, k=1):

```
u_B(Fluke) = U_pt / k = 0.065 / 2 = 0.0325 °C
```

This is used **directly in Y** — no conversion to LSB. The pipeline passes `ub_ref_y` (e.g. 0.15 °C)
to the engines. The legacy value `ub(Fluke) = 0.15 LSB` mentioned by the professor equals
`0.0325 °C × 451.97 LSB/°C` and was used in the old LSB-domain regression; it is no longer relevant.

### Sensor under calibration (NTC via TMP126 ADC)

The four fields in `ntc_temperature.json → metrology → readingUncertainty` are:

```json
{ "varName": "resolution",       "value": 0.1,  "PDF": "uniform"  },
{ "varName": "absUncertainty",   "value": 5.0,  "PDF": "uniform"  },
{ "varName": "uB",               "value": 2.9,  "PDF": "normal"   },
{ "varName": "coverageFactor",   "value": 2.0                     }
```

**What each one does today and what it should do:**

| varName | Current use in code | Meaning | Future use |
|---|---|---|---|
| `resolution` | Read from sensor JSON `metrology.readingUncertainty[resolution].value`; fallback default is 1 LSB. Converted to Y as `risol` via `risol_lsb / lsb_per_c` before being used in `u_res = RISOL/√12`. | Digital resolution of the sensor output in Y. With `PDF=uniform`, the standard uncertainty is `RISOL/√12`. |
| `absUncertainty` | Read into `sensor.absUncertainty` (= 5.0 LSB). Used only in the interpolation uncertainty printout: `ntc_abs_lsb = sensor.absUncertainty`. | Absolute uncertainty of the sensor in LSB (sum of all type-B sources from datasheet: offset, gain, INL). This is the wide-range bound before calibration. | Currently treated as a raw bound. Should become an input to the `evaluationFormula` for u_B computation, not used standalone. |
| `uB` | Read from sensor JSON `metrology.readingUncertainty[uB].value` (= 0.30 LSB). This is the value actually used in the regression as `ub_sensor_lsb`. | Type-B standard uncertainty of the ADC reading in LSB (k=1). Combines offset, gain, INL contributions after dividing by k. | This is the correct input to the uncertainty budget. |
| `coverageFactor` | Read into `sensor.K` (= 2.0). Not directly used in regression or U(E) computation today; k=2 is hardcoded in the `U_E = 2·u(E)` line. | Coverage factor for the sensor type-B uncertainty. | Should be used to divide `uB` before passing it to the regression: `u_B_standard = uB_json / coverageFactor`. Currently the JSON `uB` is already /k=2, so this is consistent, but the code does not perform the division explicitly — it just takes the value as-is. |

**Important note on units.** The `elec` range in the JSON is `[0, 65535]` (LSB). The prof said:
"Le unità di misura associate a ogni contributo di incertezza sono le stesse della sezione elec
per ora." So `resolution`, `absUncertainty`, and `uB` are all in LSB when the sensor outputs
in the electrical domain. The code is consistent with this: `ub_sensor_lsb = 0.30 LSB`.

**What `evaluationFormula` means.** Currently `evaluationFormula = "uB"` — meaning the total
type-B uncertainty is just taken to be the `uB` field directly. The prof noted that in future
it may look like `A*reading + B*absUncertainty`, i.e. a reading-dependent formula. When that
happens, `absUncertainty` becomes a parameter inside the formula rather than a standalone bound.
The architecture already has the field; the code just needs to parse and evaluate the expression.

---

## Data acquisition per step (common to all methods)

At each temperature setpoint the hardware acquires N readings from the sensor and N from the
PT100. Readings are grouped in blocks of `sample_size = 20`.

For each block of 20 readings, the mean is computed. For `n_blocks` blocks at step i:

```
pmean_ref_i = mean of block means (PT100, already mapped to LSB)
pstd_ref_i  = std(block means) / √n_blocks          ← type-A standard unc, reference

pmean_ntc_i = mean of block means (raw ADC LSB)
pstd_ntc_i  = std(block means) / √n_blocks          ← type-A standard unc, sensor
```

Combined standard uncertainties at step i (type A + type B, quadrature):

```
uc_ref_i = √( pstd_ref_i²  + u_B_ref_lsb² )     [LSB]
uc_ntc_i = √( pstd_ntc_i²  + uB_lsb² )           [LSB]   where uB_lsb = sensor.uB = 2.9
```

These are the inputs to all three regression engines.

---

## Method 1 — Linear (OLS)

**Code:** `scripts/model_calibration/linear_calibration.py`

### Regression

Model: `T_ref [°C] = A [°C/LSB] · D_out [LSB] + B [°C]`

With n steps, x_i = pmean_ntc_i [LSB] and y_i = pmean_ref_i [°C]:

```
A = Σ[(x_i − x̄)(y_i − ȳ)] / Σ[(x_i − x̄)²]
B = ȳ − A · x̄
```

### GUM propagation for u(A), u(B), cov(A,B)

Sensitivity coefficients (derived analytically from OLS):

```
∂A/∂y_i = (x_i − x̄) / S_xx          where S_xx = Σ(x_i − x̄)²
∂A/∂x_i = [(y_i − ȳ) − 2A·(x_i − x̄)] / S_xx
∂B/∂y_i = 1/n − x̄ · (∂A/∂y_i)
∂B/∂x_i = −A/n − x̄ · (∂A/∂x_i)
```

GUM law of propagation (independent steps):

```
u²(A)    = Σ[ (∂A/∂x_i · uc_ntc_i)² + (∂A/∂y_i · uc_ref_i)² ]
u²(B)    = Σ[ (∂B/∂x_i · uc_ntc_i)² + (∂B/∂y_i · uc_ref_i)² ]
cov(A,B) = Σ[ ∂A/∂x_i·∂B/∂x_i·uc_ntc_i² + ∂A/∂y_i·∂B/∂y_i·uc_ref_i² ]
```

### U(E) per step

For each calibration step i the error is E_i = T_sensor_cal_i − T_ref_i.
Both T_sensor and T_ref are measured quantities with their own uncertainties.
All quantities are in °C. The local sensitivity `sens_i = |A| = |dT/dD|` [°C/LSB]
converts the LSB-domain sensor uncertainties to °C.

```
uA_ref_i  = pstd_rtd_i                       [°C]   type-A of reference (directly in °C)
uB_ref    = ub_ref_y                          [Y]    type-B of reference (0.15 °C typ.)
sens_i    = |A|                               [°C/LSB]  local sensitivity (= A for linear)
uA_ntc_i  = pstd_log_i × sens_i              [°C]   type-A of sensor
uB_ntc    = sensor.uB × sens_i               [°C]   type-B of NTC ADC
u_res     = resolution_degC / √12            [°C]   quantisation (uniform PDF)

u(T_ref)_i = √( uA_ref_i² + uB_ref² )
u(T_i)_i   = √( uA_ntc_i² + uB_ntc² + u_res² )

u(E)_i     = √( u(T_ref)_i² + u(T_i)_i² )
U(E)_i     = 2 · u(E)_i                      k=2, ≈95 % confidence
```

### What goes in the certificate — linear method

**Page 3 — results table** (one row per calibration step):

| Column | Source | How it is computed |
|---|---|---|
| Point | step index | sequential 1…n |
| T_ref / °C | `ref_temp_means[i]` | `pmean_ref_i` converted to °C via `lsb16_to_phys` |
| T_c / °C | post-calibration sensor reading | `A · pmean_ntc_i + B` → converted to °C |
| M_e pre / °C | pre-calibration error | `lsb16_to_phys(pmean_ntc_i) − T_ref_i` |
| M_e post / °C | post-calibration error | `lsb16_to_phys(A·pmean_ntc_i + B) − T_ref_i` |
| U(E) / °C | expanded uncertainty | `2 · u(E)_i` as above |

**Page 4 — calibration function table:**

| Row | Value | Source |
|---|---|---|
| Interpolation uncertainty | `ub_ref_y + sensor.absUncertainty/lsb_per_c` (2 sig figs) | `_interp_unc_fixed_2sig` from `_build_cert_filled` |
| A / (°C/LSB) | regression coefficient A | `_A_cal` (in °C/LSB) |
| B / °C | regression offset B | `_B_cal` = `_B_cal_degC` (already in °C — no division needed) |

Note: u(A), u(B), cov(A,B) are written into the filled JSON under `_u_A`, `_u_B`, `_cov_AB`
but are not currently printed in the PDF table — they are available for the DCC XML.
`u_B` is now directly in °C (no `/ lsb_per_c` conversion).

The interpolation uncertainty shown on page 4 is computed as:

```python
fluke_abs_c = ub_ref_y                            # type-B reference in native unit
ntc_abs_c   = sensor.absUncertainty / lsb_per_c   # = 5.0 LSB / 451.97 ≈ 0.011 °C
interp_sum  = fluke_abs_c + ntc_abs_c
interp_fixed = round_2sig(interp_sum)
```

`absUncertainty` (5.0 LSB) is still converted via `lsb_per_c` here because it is a pre-
calibration datasheet bound, not a calibrated quantity. The `lsb_per_c` division here is an
informational approximation, not a metrological conversion of a calibration coefficient.

---

## Method 2 — Cubic polynomial (OLS)

**Code:** `scripts/model_calibration/cubic_calibration.py`

### Regression

Model: `T_ref_lsb = a0 + a1·D + a2·D² + a3·D³`

Build design matrix X (n×4), row i = `[1, D_i, D_i², D_i³]`, response vector y:

```
theta = [a0, a1, a2, a3] = (X'X)⁻¹ X'y
```

### GUM propagation for cov(theta)

For each step i, define:
- `x_i` = regressor row `[1, D_i, D_i², D_i³]`
- `g_i` = Jacobian of x_i w.r.t. D_i = `[0, 1, 2D_i, 3D_i²]`

Sensitivity of theta w.r.t. reference response y_i:

```
∂theta/∂y_i = (X'X)⁻¹ x_i
```

Sensitivity of theta w.r.t. sensor reading x_i (only i-th row of X changes):

```
∂(X'y)/∂D_i  = g_i · y_i
∂(X'X)/∂D_i  = x_i ⊗ g_i + g_i ⊗ x_i
∂theta/∂D_i  = (X'X)⁻¹ [ g_i·y_i − (x_i⊗g_i + g_i⊗x_i)·theta ]
```

Covariance accumulation:

```
cov(theta) = Σ_i [  ∂theta/∂y_i ⊗ ∂theta/∂y_i · uc_ref_i²
                   + ∂theta/∂D_i ⊗ ∂theta/∂D_i · uc_ntc_i² ]

u(a_k) = √( cov(theta)[k,k] )
```

### U(E) per step

Identical formula to the linear case:

```
u(E)_i = √( u(T_ref)_i² + u(T_i)_i² )
U(E)_i = 2 · u(E)_i
```

The code also computes the propagated model uncertainty at each calibration point
(`cubic_uncertainty`) via GUM Eq. 13:

```
u²(T_cal_lsb) = x · cov(theta) · x'  +  (∂f/∂D · uc_ntc_i)²

where  x = [1, D, D², D³]  and  ∂f/∂D = a1 + 2·a2·D + 3·a3·D²
```

This `U_poly = 2·u(T_cal_lsb)/lsb_per_c` is reported alongside U(E) in the internal budget
table and in the filled JSON, but is **not** the value printed in the certificate.

### What goes in the certificate — cubic method

**Page 3 — results table**: same columns as linear. T_c post is computed by evaluating the
cubic polynomial at `pmean_ntc_i`:

```python
calibrated_lsb = a0 + a1·D + a2·D² + a3·D³   # D = pmean_ntc_i
T_c_degC       = lsb16_to_phys(calibrated_lsb)
```

**Page 4**: the coefficient table currently still shows A, B, interpolation uncertainty
(inherited from linear layout). For cubic, the relevant coefficients are [a0..a3] stored in
`_a0`.._a3` in the filled JSON, but the PDF page 4 layout does not yet render them in a
dedicated table. This is a known gap — the PDF builder only has a linear-specific page 4.

---

## Method 3 — Steinhart-Hart / cube-log (OLS) — REMOVED

> The cube-log model was removed in the May 2026 refactoring. This section is retained
> for historical reference only. The current pipeline supports only `linear` and `cubic`.

**Code:** `scripts/model_calibration/cube_log_calibration.py` (deleted)

### Regression

Model: `1/T [K⁻¹] = C0 + C1·ln(D) + C3·(ln D)³`

Build design matrix X (n×3), row i = `[1, ln(D_i), (ln D_i)³]`.
Build response vector y_inv where `y_inv_i = 1 / T_ref_i [K]`.

T_ref in Kelvin comes from `pmean_ref_i` (LSB) → °C → + 273.15.

```
theta = [C0, C1, C3] = (X'X)⁻¹ X' y_inv
```

To get temperature from a reading D:

```
T [K] = 1 / (C0 + C1·ln(D) + C3·(ln D)³)
T [°C] = T [K] − 273.15
```

### GUM propagation for cov(theta)

The chain rule adds one step because the response is 1/T, not T:

```
∂y_inv_i/∂y_lsb_i = − (°C/LSB) / T_ref_i [K]²      (sensitivity of 1/T to PT100 LSB)

∂theta/∂y_lsb_i   = (X'X)⁻¹ x_i · ∂y_inv_i/∂y_lsb_i
```

For the sensor reading, the regressor row is `[1, ln(D), (ln D)³]`, so:

```
∂x_i/∂D_i = [0,  1/D_i,  3·(ln D_i)²/D_i]

∂(X'y_inv)/∂D_i = (∂x_i/∂D_i) · y_inv_i
∂(X'X)/∂D_i     = x_i ⊗ (∂x_i/∂D_i) + (∂x_i/∂D_i) ⊗ x_i
∂theta/∂D_i      = (X'X)⁻¹ [ ∂(X'y_inv)/∂D_i − ∂(X'X)/∂D_i · theta ]
```

Covariance accumulation (same pattern as cubic):

```
cov(theta) = Σ_i [ ∂theta/∂y_lsb_i ⊗ ∂theta/∂y_lsb_i · uc_ref_i²
                  + ∂theta/∂D_i     ⊗ ∂theta/∂D_i     · uc_ntc_i² ]
```

### U(E) per step

Same direct budget as the other two methods (measurement-level, not model-propagated):

```
u(E)_i = √( u(T_ref)_i² + u(T_i)_i² )
U(E)_i = 2 · u(E)_i
```

The full model-propagated uncertainty `u_SH` through the Steinhart-Hart function is also
computed (`steinhart_hart_uncertainty`) for informational use:

```
∂f/∂C_j = −f² · ∂g/∂C_j     where g = C0 + C1·ln(D) + C3·(ln D)³,  f = 1/g
∂f/∂D   = −f² · (C1/D + 3·C3·(ln D)²/D)

u²(T_cal) = df_dC · cov(theta) · df_dC'  +  (∂f/∂D · uc_ntc_i)²    [K²]
```

### What goes in the certificate — Steinhart-Hart method

**Page 3**: same table as linear. T_c post comes from evaluating the S-H model at `pmean_ntc_i`:

```python
T_K   = 1 / (C0 + C1·ln(D) + C3·(ln D)³)
T_degC = T_K − 273.15
```

**Page 4**: same limitation as cubic — the PDF page 4 shows the linear coefficient layout.
The S-H coefficients [C0, C1, C3] and their uncertainties are stored in the filled JSON
under `_C0`, `_C1`, `_C3`, `_u_C0`, `_u_C1`, `_u_C3`, `_cov_theta`, but the PDF does not
render them in a dedicated table. This needs to be extended.

---

## Dimensional analysis — `unit_checks.py` (optional)

**Code:** `scripts/model_calibration/unit_checks.py`

Enabled with `--check-units`. Uses `pint` for dimensional analysis.
Disabled by default; `pint` is not a required dependency.

### DSI string mapping

The `dsi` fields in the JSON files follow the LaTeX SI unit notation
(Système international d'unités). The module maps them to pint unit strings:

| DSI string | pint string | Dimension |
|---|---|---|
| `\\degreeCelsius` | `degC` | temperature |
| `\\kelvin` | `kelvin` | temperature |
| `\\degreefahrenheit` | `degF` | temperature |
| `\\one` | `dimensionless` | dimensionless |
| `\\pascal` | `pascal` | pressure (example of a wrong unit) |

### Dimensional rules per model

All three models share the same three checks:

| Check | Rule | Error if violated |
|---|---|---|
| Sensor electrical unit | `ranges.elec.dsi` must be dimensionless | ADC counts must not carry a physical dimension |
| Sensor physical unit | `ranges.phys.dsi` must be a temperature | The calibrated quantity must be a temperature |
| Reference physical unit | `ranges.phys.dsi` (ref JSON) must be a temperature | The reference measurement must be a temperature |

The `cube-log` model (removed) had one additional constraint: the reference unit must be
convertible to kelvin (required because the Steinhart-Hart response variable is
`1/T [K⁻¹]`, which is not defined for relative temperature scales that include
negative absolute values). In practice `\\degreeCelsius` satisfies this because
pint converts it to kelvin for offset arithmetic.

### Blocking behaviour

- Hard errors (wrong dimensionality) → `check_dsi()` returns `ok=False`;
  the engine raises `ValueError`; the orchestrator catches it, prints the
  error message, and calls `sys.exit(1)`.
- Warnings (unusual but not wrong units) → printed to stdout; calibration
  continues.

### Unit conversion — `convert_result()`

Enabled with `--convert-units`. Converts numeric outputs to the target unit
declared in `ranges.phys.dsi` of the sensor JSON.

| Quantity | Conversion type | Notes |
|---|---|---|
| `B` (linear offset) or `a0` (cubic offset) | Absolute temperature offset | Uses pint's offset-aware `Quantity.to()` |
| `u_B` / `u_a0` (standard uncertainty of offset) | Delta temperature | Magnitude-only: ×1 for °C↔K, ×1.8 for °F |
| `ref_temp_means` | Absolute temperature | pint offset conversion |
| `expanded_uncertainties` | Delta temperature | Magnitude-only |
| `A` (gain, dimensionless) | Not converted | Stays dimensionless |
| `C0`, `C1`, `C3` (Steinhart-Hart) | Not converted | Defined in K⁻¹ by model; not unit-sensitive to the output scale |

Results are stored in a `converted` sub-dict in `calib_result` and printed
in the `=== Unit conversion results ===` verbose block. They do not affect
the certificate JSON, PDF, or DCC XML output — those always use the native
LSB/°C values.

---

## Interpolation uncertainty (all methods)

After fitting, the certificate page 4 shows one fixed interpolation uncertainty value.
This answers the question: "between calibration points, how uncertain is a reading?"

### Current computation (analisi_calib_data.py lines ~487-492, ~635-641)

```python
fluke_abs_c  = ub_ref_lsb / lsb_per_c             # type-B reference in Y
sensor_abs_c  = sensor.absUncertainty / lsb_per_c   # absUncertainty in Y (5.0/451.97 ≈ 0.011)
interp_sum   = fluke_abs_c + sensor_abs_c           # conservative sum (not RSS)
interp_fixed = round_2sig(interp_sum)              # 2 significant figures
```

Printed in the PDF as "Interpolation uncertainty = {interp_fixed} °C".

The code also computes and prints (verbose only):

```python
calc_interp = max(expanded_uncertainties)          # worst U(E) across all steps
diff        = calc_interp − interp_fixed
```

So there are two numbers: the fixed conservative bound from instrument specs, and the
worst-case U(E) from the actual measurement. The certificate shows only the fixed bound.

### What `absUncertainty` does here

`sensor.absUncertainty = 5.0` LSB is the "absolute uncertainty" of the sensor before
calibration (datasheet bound combining offset, gain, INL errors). In this computation it is
converted to °C and added to the Fluke contribution. With 5.0 LSB / 452 LSB/°C ≈ 0.011 °C,
its contribution is small compared to Fluke's 0.0325 °C.

### Future: model-propagated interpolation uncertainty

The physically correct approach is to propagate the coefficient covariance to an arbitrary
point D using `cubic_uncertainty` or `steinhart_hart_uncertainty`. These functions exist and
are already called per step. Connecting them to the certificate output would replace the
conservative fixed bound with a point-by-point (or worst-case) model uncertainty.

---

## The readingUncertainty fields — current vs future

Summary of all four fields, their current wiring, and what they should do:

### `resolution` = 0.1 (uniform PDF)

**Current wiring:**
- Read from sensor JSON `metrology.readingUncertainty[resolution].value`; fallback 1 LSB.
- Converted to Y as `risol_lsb / lsb_per_c`. Used in: `u_res = risol / √12`.

**Problem:** JSON says 0.1, code uses 0.01. These are inconsistent. Either the JSON is wrong
(should be 0.01 to match the hardware resolution) or the code default is wrong (should be 0.1).

**Future:** Add `resolution_degC = data["metrology"]["readingUncertainty"]["resolution"]`
to `_load_from_json`. Apply `u_res = resolution_degC / √12` consistently.

### `absUncertainty` = 5.0 (uniform PDF)

**Current wiring:**
- Parsed by `_load_from_json` → `self.absUncertainty = 5.0` [LSB].
- Used in the interpolation uncertainty: `ntc_abs_c = 5.0 / lsb_per_c ≈ 0.011 °C`.
- Not used in the regression or in U(E) computation.

**What it represents:** Combined absolute uncertainty from the ADC datasheet (offset +
non-linearity + gain error, rectangular distribution). This is the sensor's pre-calibration
accuracy bound, not a per-reading noise term.

**Future:** When `evaluationFormula` becomes expression-based, `absUncertainty` appears as
a named variable: e.g. `A*reading + B*absUncertainty`. The code would evaluate this expression
at each step to get u_B for that step, replacing the current fixed `uB` value.

### `uB` = 2.9 (normal PDF)

**Current wiring:**
- Parsed by `_load_from_json` → `self.uB = 2.9` [LSB].
- Passed to the regression engines as `ub_sensor_lsb = 0.30 LSB` (standard unc, k=1).
- Used in U(E): `uB_ntc = sensor.uB / lsb_per_c = 2.9 / 452 ≈ 0.00642 °C`.
- Used in uc_ntc_i: `uc_ntc_i = √(pstd_ntc_i² + uB_lsb²)`.

**What it represents:** The type-B standard uncertainty of a single ADC reading in LSB.
The prof confirmed this value: `ub(ADC_NTC) = 2.9 LSB`, already divided by k=2.
`PDF=normal` is consistent with it being a standard uncertainty (k=1 Gaussian).

**Future:** If u_B becomes reading-dependent, this field becomes one term in the expression.
If it remains a fixed value, `coverageFactor` should be used to divide it:
`u_B_standard = uB / coverageFactor` — currently this division is implicit (the JSON value
is already /k) but the code does not perform it explicitly.

### `coverageFactor` = 2.0

**Current wiring:**
- Parsed by `_load_from_json` → `self.K = 2.0`.
- **Not used anywhere in the regression or U(E) computation.** The value k=2 for U(E) is
  hardcoded as `U_E = 2.0 * mu_E` in all three calibration engines.

**Future:** Should be used to convert `uB` from its stored form to a standard uncertainty:
`u_B_standard = sensor.uB / sensor.K`. Also should parameterise the final expansion:
`U_E = sensor.K * mu_E` instead of hardcoded 2.

---

## Certificate data flow — from raw reading to printed value

```
JSON payload (LSB16)
    │
    ├─ reference_temperature_samples  → PT100 readings [°C] — stored directly in °C
    └─ sensor_raw_samples             → NTC ADC readings [LSB]
    │
    ▼
Per-step statistics (_compute_step_statistics)
    pmean_ref [°C],  pstd_ref [°C]    ← reference, native °C
    pmean_ntc [LSB], pstd_ntc [LSB]   ← sensor, native LSB
    │
    ├─ uc_ref = √(pstd_ref² + ub_ref_y²)     [Y]   ← u_B from ref JSON [Y]
    └─ uc_sensor = √(pstd_sensor² + ub_sensor_lsb²)  [LSB] ← u_B from sensor JSON [LSB]
    │
    ▼
Regression  (linear / cubic)
    Coefficients:  A [°C/LSB], B [°C]  or  [a0 °C, a1 °C/LSB, …]  or  [C0,C1,C3 K⁻¹]
    Uncertainties: u(A) [°C/LSB], u(B) [°C], cov(A,B)  or  cov(theta)
    │
    ▼
U(E) per step  (direct measurement budget — all in °C)
    sens_i    = |dT/dD|_i              [°C/LSB]  (local sensitivity)
    uA_ref    = pstd_ref_i             [°C]
    uA_ntc    = pstd_ntc_i × sens_i   [°C]
    uB_ref    = ub_ref_y               [Y]
    uB_ntc    = sensor.uB × sens_i    [°C]
    u_res     = sensor.resolution_degC / √12  [°C]
    u(E)      = √( (√(uA_ref²+uB_ref²))² + (√(uA_ntc²+uB_ntc²+u_res²))² )
    U(E)      = 2 · u(E)
    │
    ▼
_build_certificato_filled  (analisi_calib_data.py)
    Merges template JSON + computed values
    Writes certificato_funzione_filled.json
    │
    ├─ template_parts.calculated_calibration_values._measurements
    │      [[point, T_ref, T_c_post, M_e_pre, M_e_post, U(E)], ...]
    │
    └─ _calibration_result
           _A, _B, _u_A, _u_B, _cov_AB              (linear)
           _a0.._a3, _u_a0.._u_a3, _cov_theta        (cubic)
           _C0, _C1, _C3, _u_C0.._u_C3, _cov_theta   (cube-log)
           _expanded_uncertainties_degC
           _interp_unc_sum_abs_degC
           _interp_unc_fixed_2sig_degC
    │
    ▼
certificato_funzione.py  (PDF)
    Page 3: results table reads _measurements
    Page 4: reads _A_cal, _B_cal_degC, _interp_unc_fixed_2sig_degC

generate_dcc_xml.py  (DCC XML)
    Reads _calibration_result for coefficient + uncertainty export
```

---

## Annotated certificate image — point by point

Annotations from Image 6 and the professor's messages:

| Annotation / statement | Assessment |
|---|---|
| `u(E) = √(u²(T_ref) + u²(T_i))` | Correct and implemented exactly. |
| `u(T_ref) = √(uA_ref² + uB_ref²)` | Correct. uA_ref = pstd_ref/lsb_per_c, uB_ref = 0.0325 °C. |
| `u(T_i) = √(uA_i² + uB_i² + ...)` | Correct. Includes u_res = RISOL/√12 (rectangular). |
| `U(E) = u(E) · 2` | Correct, k=2. |
| `RISOL = 0.1°C` on image | **Inconsistency.** JSON has `resolution=0.1`, code uses `resolution_degC=0.01`. One is wrong. The hardware TMP126 has 0.01 °C resolution; the JSON value of 0.1 appears to be outdated. |
| `CALCOLATO come T_sens − T_ref = E` | Correct. Code computes `error_post_degc = t_sensor_degc − ref_t`. |
| `ub(Fluke) = 0.15 LSB`, `ub(ADC_NTC) = 2.9 LSB` both /k=2 | Correct. Code uses them as standard uncertainties (already /k). |
| "Non devi convertire l'incertezza tipo da LSB a °C, la funzione di taratura se ne occupa" | Correct. Regression stays in LSB; conversion to °C happens only at output. Code is consistent with this. |
| `T = A·Dout + B` with A in °C/LSB and B in °C | The formula is correct. In the code, A is dimensionless and B is in LSB; the PDF labels them "A / (°C/LSB)" and "B / °C" using the converted value `B/lsb_per_c`. This is a display convention, not a code inconsistency. |
| "uB può talvolta dipendere dalla lettura" | Not yet implemented. `evaluationFormula` field exists but is parsed as a fixed string `"uB"`. The reading-dependent case needs expression evaluation. |
| "Le unità di misura associate a ogni contributo sono quelle della sezione elec per ora" | Consistent with code: `uB = 2.9 LSB`, `absUncertainty = 5.0 LSB`, all in the electrical domain. |
| `NON METTIAMO` on page 3 | Correct: intermediate nominal resistance values (from Steinhart-Hart inverse) are not in the final certificate. |
| Image annotation `RISOL/√n` | **Imprecision in annotation.** The correct formula for a uniform distribution is `RISOL/√12`, not `RISOL/√n` (n is sample size, unrelated). The code uses `√12`. |
| Rows 7–15 blank | Correct. Only 6 active calibration steps; the PDF does not pad with empty rows. |
| "IN LSB la taratura è analoga a quella di una termoresistenza" | Accurate analogy: same regression structure, different domain (LSB vs Ohm). |
