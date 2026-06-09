# verify_dcc_conformity.py  —  quick reference

> standalone dcc xml verifier. parses a ptb dcc 3.3.0 xml certificate and runs 3 checks + charts.
> file: `backend\calibration\scripts\verify_dcc_conformity.py` (559 lines)

---

## what it does

1. parse the dcc xml → extract `t_ref`, `t_sensor`, `me_pre`, `me_post`, `u_exp`
2. load sensor accuracy ranges from `models_in\ntc_temperature.json`
3. run **check g** (sensor accuracy as-found), **check h** (pfa), **overlap check** (uncertainties compatibility)
4. print a formatted console report
5. save 4 png charts (if `--images-dir` given)

---

## functions

### `main()` (line 506)
cli entry. hardcoded defaults:

| param | default | meaning |
|-------|---------|---------|
| `--sensor` | `models_in\ntc_temperature.json` | sensor model |
| `--mae` | 0.10 °c | max acceptable error for check h |
| `--pfa-threshold` | 20.0% | pfa acceptance threshold |
| `--u-ref` | 0.065 °c | ref expanded uncertainty (pt100/fluke, k=2) |

---

### `parse_dcc_xml(xml_path)` (line 35)
parses the dcc xml. looks for `<quantity>` elements with `refType` attribute:

| refType | extracts → | variable |
|---------|-----------|----------|
| `basic_referenceValue` | reference temperature per point | `t_ref` |
| `basic_measuredValue` | sensor temperature per point | `t_sensor` |
| `gp_measurementErrorPreCalibration` | pre-calibration error per point | `me_pre` |
| `basic_measurementError` | post-calibration error per point | `me_post` |
| `uncertaintyXMLList` (inside `basic_measurementError`) | expanded uncertainty per point | `u_exp` |

**fallbacks:**
- if `me_pre` missing → derives `t_sensor − t_ref`
- if `me_post` missing → derives `t_sensor − t_ref`
- if `u_exp` missing → defaults to 0.0 for all points
- mismatched lengths → padded/truncated with warnings

---

### `load_sensor_accuracy_ranges(sensor_path)` (line 103)
loads `metrology.sensorAccuracy` from the sensor json. each entry is:
```
{ "tempMin": float, "tempMax": float, "maxError": float }
```
returns `[]` if file missing or parse fails.

---

### `run_checks(t_ref, t_sensor, me_pre, u_sensor, accuracy_ranges, mae, pfa_threshold_pct, u_ref)` (line 115)
the core. runs three independent checks.

#### check g — sensor accuracy as-found conformity

**formula (per point):**
```
for each point i:
  applicable = { maxError | tempMin ≤ t_ref[i] ≤ tempMax }
  max_err   = min(applicable)  or  ∞ if none
  G1_pass   = |me_pre[i]| ≤ max_err
  G2_pass   = max_err < ∞   (t_ref covered by any range)
```

**verdict:**
- `FAIL` if any G1 fails
- `WARN` if any G2 fails (point outside declared ranges)
- `PASS` if all G1+G2 pass
- `N/A` if no accuracy_ranges loaded

---

#### check h — probability of false acceptance (pfa)

**formula (per point):**
```
u_std  = u_sensor[i] / 2        (std uncertainty from expanded)
u_ein  = u_std / MAE            (normalised uncertainty)
ein    = me_pre[i] / MAE        (normalised error)

PFA = 1 − Φ(1; μ=ein, σ=u_ein) + Φ(−1; μ=ein, σ=u_ein)
```

where `Φ(x; μ, σ)` is the normal gaussian cdf:
```
normal_cdf(x, μ, σ) = ½ · [1 + erf((x − μ) / (σ · √2))]
```

uses **pure** `math.erf` — no scipy dependency. verifies that PFA ≤ threshold%.

**verdict:**
- `PASS` if all points have PFA ≤ pfa_threshold
- `FAIL` otherwise

---

#### overlap check — uncertainties compatibility

**formula (per point):**
```
diff     = |t_sensor[i] − t_ref[i]|
u_sns    = u_sensor[i]

simple_pass = diff ≤ (u_sns + u_ref)        ← linear sum
rss_pass    = diff ≤ √(u_sns² + u_ref²)     ← rss combination
```

**verdict:**
- `PASS` if both simple + rss satisfied
- `FAIL` otherwise

---

### `print_results_report(...)` (line 230)
formatted console output with:
- extracted data table (t_ref, t_sensor, me_pre, me_post, u_sensor)
- per-point check g table (limit, G1, G2)
- per-point check h table (u_std, PFA%)
- per-point overlap table (diff, simple, rss)
- overall verdict: `CONFORMING` if G+PASS/NA, H=PASS, overlap=PASS

---

### `save_charts(...)` (line 318)
generates 4 png files via matplotlib:

| file | content |
|------|---------|
| `fig1_pfa_chart.png` | bar chart of PFA% per point, red threshold line |
| `fig2_error_bars.png` | pre/post calibration errors ± U_exp, MAE bands |
| `fig3_overlap_check.png` | grouped bars: diff vs simple sum vs rss |
| `fig4_check_g_accuracy.png` | scatter of as-found errors, ±maxError limits per point |

all saved to `--images-dir` (default: none, skipped). dpi=150.

---

### `normal_cdf(x, mu=0.0, sigma=1.0)` (line 29)
pure-python gaussian cdf. used by check h to avoid scipy import.

```
return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))
```

if `sigma ≤ 0` → step function (1.0 if x ≥ mu else 0.0).

---

## formulas summary

| check | formula | function |
|-------|---------|----------|
| G | `|me_pre| ≤ maxError(tempMin..tempMax)` | `run_checks()` inline |
| H | `PFA = 1 − Φ(1; ein, u_ein) + Φ(−1; ein, u_ein)` | `run_checks()` → `normal_cdf()` |
| H | `Φ(x; μ, σ) = ½·[1 + erf((x−μ)/(σ·√2))]` | `normal_cdf()` |
| overlap (simple) | `|T_sns − T_ref| ≤ U_sns + U_ref` | `run_checks()` inline |
| overlap (rss) | `|T_sns − T_ref| ≤ √(U_sns² + U_ref²)` | `run_checks()` inline |

---

## difference from checks_helper.py

| aspect | checks_helper.py | verify_dcc_conformity.py |
|--------|------------------------|--------------------------|
| input | filled cert json (`certificato_funzione_filled.json`) | dcc xml (`ntc_calibration_certificate.xml`) |
| checks | G, A, B, H (4 checks) | G, H, overlap (3 checks) |
| check A/B (post-cal error vs U_exp/limit) | yes | no |
| overlap check | no | yes — simple + rss |
| normal_cdf | scipy.stats.norm.cdf | pure math.erf |
| charts | none (removed; docs instead) | 4 (pfa bars, error bars, overlap, accuracy scatter) |
| hardcoded u_ref | `U_PT_DEGC = 0.065` | `--u-ref` cli (default 0.065) |
| hardcoded MAE | `DEFAULT_MAE_DEGC = 0.30` | `--mae` cli (default 0.10) |
