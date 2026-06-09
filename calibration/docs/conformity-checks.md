# Conformity Checks — checks_helper.py / verifica_conformita.py

> Keep aligned with `scripts/checks_helper.py` and `scripts/verifica_conformita.py`. Update when check logic changes.

---

## Overview

`checks_helper.py` provides the conformity check library invoked inline by
`analisi_calib_data.py`. `verifica_conformita.py` is a standalone post-pipeline CLI
that reads `certificato_funzione_filled.json` and runs the same checks. Both run
four metrological checks (G, A, B, H), printing a PASS / WARN / FAIL / N/A
report to stdout.

A third module, `verify_dcc_conformity.py`, is a standalone DCC XML verifier that
parses PTB DCC 3.3.0 XML and runs a different subset of checks (G, H, overlap).
See [verify-dcc-conformity.md](verify-dcc-conformity.md).

```
python scripts/verifica_conformita.py \
  --input  certificato_out/certificato_funzione_filled.json \
  --variant funzione \
  --verbose
```

---

## Constants (hardcoded, not in any JSON)

| Constant | Value | Meaning |
|---|---|---|
| `K_COPERTURA` | 2.0 | Coverage factor (GUM, ≈95 % confidence) |
| `CONF_LEVEL_PCT` | 95.0 % | Nominal confidence level |
| `U_PT_DEGC` | 0.065 °C | Expanded uncertainty PT100/Fluke 1502A (k=2) |
| `K_PT` | 2.0 | Coverage factor of the PT100 reference |
| `D_TMP126_DEGC` | 0.30 °C | Half-width of uniform distribution for NTC ADC |
| `ADC_BITS` | 16 | ADC resolution → 65535 LSB full-scale |
| `EPSILON_A` | 1×10⁻⁶ | Numerical tolerance on A comparison |
| `EPSILON_B_DEGC` | 1×10⁻⁴ °C | Numerical tolerance on B comparison |

The uncertainty limit against which Check B is judged is read from
`sensor_json.metrology.Uncertainty[0].absUncertainty` (value: 0.10 °C).

---

## Check H — Probability of False Acceptance (PFA)

**Added in:** current version — runs after G, A, B.

**Purpose:** Quantifies the statistical risk that the true as-found sensor error
exceeds the Maximum Accepted Error (MAE) even when the measured error appears
within spec. This is the ISO 14253-1 / JCGM 106 / ILAC-G8 "simple acceptance"
PFA calculation under the normal distribution assumption, as described in the
reference paper (Carullo et al., IEEE I&M Magazine, June 2024).

### Error used: `M_e_pre` (as-found, pre-calibration) — column 3

Check H uses the **as-found** error `M_e_pre` (the uncalibrated sensor reading minus
the reference temperature), **not** `M_e_post`.

Rationale: the PFA answers "does this sensor need recalibration?" — the same
question the paper asks of each DMM before accepting or rejecting it.
Using `M_e_post` would be wrong: OLS forces `M_e_post ≈ 0` at every training
point by construction, so the PFA from `M_e_post` is driven entirely by the
uncertainty ratio `u_std / MAE` and carries no information about whether the
sensor actually meets spec.

### Normalised quantities (as in the paper)

```
Ein_i   = M_e_pre_i / MAE          (normalised as-found error, dimensionless)
u_Ein_i = u_std_i / MAE            (normalised std uncertainty, dimensionless)
```

Both are stored per point in the result dict.

### Formula (eq. 4 in the paper, normalised form)

```
PFA_i = 1 - NCDF(1; Ein_i, u_Ein_i) + NCDF(-1; Ein_i, u_Ein_i)
```

equivalently in physical units:

```
PFA_i = 1 - NCDF(MAE; M_e_pre_i, u_std_i) + NCDF(-MAE; M_e_pre_i, u_std_i)
```

where `NCDF(x; μ, σ)` is the normal CDF and `u_std_i = U(E)_i / k` (k=2).

### Uncertainty used and conservatism vs. the paper

The paper uses **Type A only** (`s / √n` of repeated readings) because Type B
(datasheet worst-case bounds) overestimates the dispersion of a single test
measurement.

The pipeline uses the **full GUM** `U(E)` (Type A + Type B). This is conservative:
`u_B(sensor) ≈ 0.173 °C` inflates the uncertainty far beyond the mean-of-20
repeatability, making PFA appear worse than it would be with Type A alone.
The `u_Ein` ratio is reported per point so the conservatism can be quantified.

**Parameters (hardcoded in `analisi_calib_data.py::main()`):**

| Parameter | Default | Meaning |
|---|---|---|
| `CONFORMITY_MAE_DEGC` | 0.10 °C | Maximum Accepted Error — symmetric interval ±MAE |
| `CONFORMITY_PFA_THRESHOLD_PCT` | 20.0 % | Check H passes only if PFA ≤ this threshold at every point |

**Status logic:**

| Condition | Status |
|---|---|
| PFA_i ≤ threshold for ALL points | **PASS** |
| At least one point has PFA_i > threshold | **FAIL** |

**Expected result with current NTC hardware:**

`u(E_in) = u_std / MAE ≈ 0.175 / 0.10 = 1.75 >> 1`.
Even at `M_e_pre = 0`: `PFA = 2·Φ(-1/1.75) ≈ 57 %`.
Check H will **always FAIL** at MAE = 0.10 °C. To pass: need `u_std < 0.078 °C`
(24-bit ADC) or relax MAE to ≥ 0.23 °C.

**CLI flags (verifica_conformita.py standalone):**

```
python scripts/verifica_conformita.py \
  --input certificato_out/certificato_funzione_filled.json \
  --mae 0.10 \
  --pfa-threshold 20.0
```

---

## Check G — sensorAccuracy as-found conformity (runs first)

**Source:** `sensorAccuracy` list in `ntc_temperature.json` (metrology block).

The check runs **before** all other checks and has two ordered sub-checks:

### G1 — as-found error within declared maxError

**Criterion:** `|M_e_pre_i| ≤ maxError` for every calibration point, where
`maxError` is the *most restrictive* (smallest) limit among all `sensorAccuracy`
entries whose `[tempMin, tempMax]` interval contains `T_ref_i`.

- For **interpolation** procedures (`linear`, `qubic-interpolation`) the
  calibration points are by construction inside the declared physical range and
  will always be covered by at least one `sensorAccuracy` entry.
- For **regression** procedures (`cubic`) extrapolation is possible:
  a point outside every declared range gets `maxError = +inf` and is treated as
  uncovered (G2 WARN, G1 unevaluable).

### G2 — sensorAccuracy range covers every calibration point

**Criterion:** every `T_ref_i` must fall inside at least one
`sensorAccuracy[].tempMin..tempMax` interval.

For interpolation this is always satisfied by construction.
For regression it may not hold for points outside the declared physical range.

### Overall check G status

| Condition | Status |
|---|---|
| `sensorAccuracy` absent or empty | **N/A** |
| all G1 pass AND all G2 pass | **PASS** |
| at least one G1 fails (error > limit) | **FAIL** |
| all G1 pass but at least one G2 not covered | **WARN** |

### Relation to `--update-parameters-if-out-range-error`

When the flag `--update-parameters-if-out-range-error` is passed to
`analisi_calib_data.py`, the calibration step is **skipped** if all as-found
errors are within the `sensorAccuracy` limits (equivalent to G1 PASS for all
points).  In that case:

- The certificate reports the initial (identity) coefficients as both
  as-found and as-left (`A=1, B=0` for linear; `a0=0, a1=1, a2=0, a3=0` for cubic).
- `M_e_post == M_e_pre` for every row (no correction applied).
- The result JSON carries `"calibration_done": "not_necessary"`.
- A console message is printed:
  ```
  [INFO] --update-parameters-if-out-range-error:
         ALL as-found errors are within the declared sensorAccuracy limits.
         Calibration parameter update is NOT necessary.
  ```

---

## Check A — Residuals within U(E)

**Criterion:** `|M_e_i| ≤ U(E)_i` for every calibration point.

`M_e` is recalculated as `T_sensor − T_ref` from the table (not taken from the
`error_degC` column directly, as a cross-check).

**Expected result (6-point dataset):** PASS — post-calibration residuals are at
the level of floating-point rounding (~10⁻¹³ °C) because T_sensor in the
filled JSON is already the post-calibrated value A·D+B.

---

## Check B — U(E) ≤ declared limit

**Criterion:** `U(E)_i ≤ limit` for every point, where `limit = 0.10 °C`.

**Expected result:** **FAIL** — this is a structural, expected FAIL with the
current hardware setup.

Explanation of the FAIL:

- The dominant uncertainty component is `u_B(sensor) = 0.30/√3 ≈ 0.173 °C` (type B,
  NTC ADC half-width modelled as uniform distribution).
- Even with zero type-A contribution, `U(E) = 2 × √(0.0325² + 0.173² + 0.00289²)
  ≈ 0.352 °C`, which exceeds the 0.10 °C limit by 3.5×.
- The limit `uncertainty_limit = "within 0.10 C"` in `ntc_temperature.json` reflects
  the **target specification**, not what the current ADC hardware achieves.
- To reach U(E) < 0.10 °C: reduce `d_NTC` (better ADC or signal conditioning) or
  tighten the PT100 reference uncertainty.

---

## Check C — A/B consistency (OLS recomputation)

**Criterion:** The filled JSON contains `T_sensor` **post-calibration** (i.e.
`A·D_raw+B` has already been applied). Therefore `T_sensor ≈ T_ref` for all
points by construction, and a naive OLS refit would give A′=1, B′=0 regardless
of the real coefficients.

The check therefore does **not** attempt to refit A and B. Instead it verifies:

- `|T_sensor − T_ref| < 1×10⁻⁶ °C` for every point (internal consistency of
  the post-calibration table).

**Expected result:** PASS (with a WARN note printed explaining the limitation).

To perform a genuine A/B round-trip check, raw LSB data from
`export2_tmp126_lsb16.json` would be required alongside the filled JSON.

---

## Check D — GUM budget decomposition

**Criterion:** Decomposes `u(E) = U(E)/2` into known components and verifies
internal consistency.

Components:

| Component | Formula | Value |
|---|---|---|
| `u_B(ref)` | `U_PT / k_PT = 0.065/2` | 0.0325 °C |
| `u_B(sensor)` | `D_TMP126 / √3 = 0.30/√3` | 0.1732 °C |
| `u_res` | `resolution / √12 = 0.01/√12` | 0.00289 °C |
| `u_A(est)` | `√(max(0, u(E)² − u_B²_sum))` | residual (not in JSON) |

The check passes if:
- `u_A_est² ≥ 0` (type-B components alone do not exceed u(E)) — algebraically
  guaranteed by the `max(0, ...)` clamp.
- `u_B_only = √(u_B²_sum) ≤ u(E)` — the known type-B components do not by
  themselves exceed the total standard uncertainty.

Note: `u_A_est` is a residual estimate derived from `U(E)` and the known type-B
terms. It is not independently available in the filled JSON.

**Expected result:** PASS.

---

## Check E — Coverage factor k=2

**Criterion:**
1. The text `"k = 2"` or `"k=2"` appears in the certificate notes.
2. `u(E) = U(E)/2` is in the physically plausible range [0.01 °C, 2.0 °C].

**Expected result:** PASS.

---

## Check F — Numerical formula consistency

Two sub-checks:

**F1 (both variants):** `M_e_calc = T_sensor − T_ref` matches `M_e` stored in
the table to within 1×10⁻¹⁰ °C.

**F2 (funzione variant):** Coefficient plausibility:
- A > 0 (monotone increasing calibration function).
- B/lsb_per_c in (−300, +300) °C (offset physically reasonable).
- The function `T = A·D+B` maps the full ADC range [0, 65535] onto a temperature
  interval that overlaps the physical calibration range [−40, 105] °C.

Note: a full round-trip check (reconstruct D_raw from T_ref, apply A·D+B,
compare to T_sensor) is not possible from the filled JSON alone because D_raw is
not stored there. Only plausibility checks are performed.

**Expected result:** PASS.

---

## Expected outcomes (ideal 6-point dataset)

Eight checks are run:

| Check | Expected | Notes |
|---|---|---|
| G — sensorAccuracy as-found | **PASS** (interpolation) / **WARN** (regression extrapolation) | G1 always passes if as-found error ≤ maxError; G2 warns for out-of-range regression points |
| A — Residuals within U(E) | **PASS** | Post-cal residuals ~10⁻¹³ °C |
| B — U(E) ≤ limit | **FAIL** | Structural: NTC ADC dominates (0.173 °C >> 0.10 °C limit) |
| C — A/B consistency | **PASS** + WARN | Genuine check impossible without raw LSB data |
| D — GUM budget | **PASS** | Algebraic identity; shows variance decomposition |
| E — Coverage factor | **PASS** | k=2 declared in notes, u(E) in plausible range |
| F — Formula consistency | **PASS** | M_e arithmetic + coefficient plausibility |
| H — PFA ≤ 20 % (MAE=±0.10 °C) | **FAIL** | Structural: u_std ≈ 0.175 °C >> MAE/1.28 → PFA ≈ 57 % at every point |

**Theoretical verdict:** NON CONFORME (structural FAILs on Check B and Check H).
Both document the gap between the target specification (0.10 °C)
and current hardware capability (~0.35 °C U(E)).

---

## Matplotlib figures (--charts)

| Figure | Content |
|---|---|
| Fig 1 | Residuals M_e with U(E) error bars and ±limit dashed lines |
| Fig 2 | Stacked bar variance budget per calibration point |
| Fig 3 | Calibration curve T=A·D+B with GUM uncertainty band ±U(ŷ), k=2 |

The uncertainty band in Fig 3 is: `U(ŷ) = 2 × √(u_B² + D²·u_A² + 2·D·cov(A,B))`.
It widens at the extremes due to the negative correlation between A and B (typical
of OLS with centred data).

---

## GUM vs. JCGM 106 — context note

`verifica_conformita.py` implements the **JCGM 100 (GUM)** paradigm: single sensor,
full variance budget (type A + type B), simple acceptance rule (`U(E) ≤ limit`).

The professor's MATLAB conformity script implements the **JCGM 106** paradigm: batch of
instruments, normalised error `E_n = error / MAE`, guard-banding based on Probability
of False Accept (PFA).

The two approaches are not interchangeable. This pipeline uses the GUM approach because
it characterises a custom hardware setup whose type-B uncertainty (NTC ADC ≈ 0.173 °C)
is the dominant unknown. The PFA/guard-band approach requires `u_c < limit` to produce
a useful guard band; with the current 16-bit ADC, `u_c ≈ 0.176 °C > 0.10 °C` (the
declared limit), making guard-banding inapplicable — which is consistent with Check B
returning FAIL.

To make the sensor compliant under either approach the options are:
- Replace the 16-bit ADC with one of higher resolution to reduce `u_c`.
- Relax the declared limit from 0.10 °C to ≥ 0.25 °C
  (minimum for PFA ≤ 20 % with perfect zero residual and current `u_c = 0.176 °C`).

---

## Uncertainty budget in DCC XML output

The per-step GUM uncertainty budget is written into the DCC XML certificate (not the PDF)
as four additional `dcc:quantity` elements inside the calibration table list:

| refType | Content |
|---|---|
| `gp_uncertaintyTypeA_reference` | Type A standard uncertainty of the PT100 reference: `u_A,ref = σ_ref / √n` [°C] |
| `gp_uncertaintyTypeA_sensor` | Type A standard uncertainty of the NTC sensor: `u_A,sensor = σ_sensor / √n` [°C] |
| `gp_combinedStandardUncertainty` | Combined standard uncertainty `u_c(E) = √(u(T_ref)² + u(T_i)²)` [°C] |
| `gp_coverageFactor` | Coverage factor `k = 2` (dimensionless, `\one`) |

These are emitted for the `linear` and `cubic` calibration models.
When the budget is absent (no `_u_budget_per_step` in the filled
JSON) the four quantities are silently omitted.

The relationship to the existing Quantity 4 (M_e_post + `si:expandedUncXMLList`) is:
```
U_exp = k * u_c(E)         (same value as reported in Quantity 4's expandedUncXMLList)
u_c(E) = √(u_T_ref² + u_T_i²)
u_T_ref = √(u_A,ref² + u_B,ref²)   where u_B,ref = U_PT / k_PT = 0.0325 °C
u_T_i   = √(u_A,sensor² + u_B,sensor² + u_res²)
                                    where u_B,sensor = D_TMP126 / √3 ≈ 0.173 °C
                                          u_res = RISOL / √12 ≈ 0.029 °C
```
