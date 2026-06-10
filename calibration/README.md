# calibration/

NTC thermistor calibration pipeline: raw LSB measurements → PDF certificate + DCC XML.

See [AGENT.md](AGENT.md) for rules and links to detailed docs.

---

## Requirements

```
pip install numpy reportlab scipy
```

Matplotlib is needed for `--charts` (save PNGs) and `--charts-interactive` (interactive display):

```
pip install matplotlib
```

Pint is optional (only needed for `--check-units` / `--convert-units`):

```
pip install pint
```

---

## Calibration models

Three calibration engines are available, selected via `--procedure`:

| `--procedure` | Model | Coefficients | Use when |
|---|---|---|---|
| `linear` | `T_ref_lsb = A·D + B` | A, B | First-order approximation, wide temperature range, baseline |
| `cubic` | `T_ref_lsb = a0 + a1·D + a2·D² + a3·D³` | a0…a3 | Polynomial correction for NTC nonlinearity in LSB domain |
| `cube-log` | `1/T[K] = C0 + C1·ln(D) + C3·(ln(D))³` | C0, C1, C3 | Steinhart-Hart equation — physically motivated NTC model |

All three engines work entirely in the **16-bit LSB domain** and propagate uncertainties
according to **ISO/IEC Guide 98-3 (GUM)** using analytical sensitivity coefficients.

If `--procedure` is omitted the orchestrator reads `calibration.type` from
the sensor model JSON (e.g. `models_in/sensors/ntc_temperature.json`).
If `--procedure` is provided it overrides the JSON value.
Unknown/invalid procedures fall back gracefully to the JSON default.
The legacy value `"qubic-interpolation"` maps automatically to `"linear"`.

---

## Run the pipeline

Two model inputs are required (both default to files already in `models_in/`):

| Arg | Role | Default |
|---|---|---|
| `--sensor PATH` | NTC sensor model JSON | `models_in/sensors/ntc_temperature.json` |
| `--ref PATH` | Reference calibrator JSON | `models_in/references/fluke_9142.json` |

### Quick run (use procedure from sensor model)

```powershell
python scripts/analisi_calib_data.py `
  --input   data_in/convert22042026_payload_lsb16.json `
  --sensor  models_in/sensors/ntc_temperature.json `
  --ref     models_in/references/fluke_9142.json
```

### With explicit procedure

```powershell
# Linear OLS
python scripts/analisi_calib_data.py `
  --input     data_in/export2_tmp126_millic_from_lsb16.json `
  --sensor    models_in/sensors/ntc_temperature.json `
  --ref       models_in/references/fluke_9142.json `
  --procedure linear

# Cubic polynomial OLS
python scripts/analisi_calib_data.py `
  --input     data_in/export2_tmp126_lsb16.json `
  --sensor    models_in/sensors/ntc_temperature.json `
  --ref       models_in/references/fluke_9142.json `
  --procedure cubic

# Steinhart-Hart (cube-log)
python scripts/analisi_calib_data.py `
  --input     data_in/export2_tmp126_lsb16.json `
  --sensor    models_in/sensors/ntc_temperature.json `
  --ref       models_in/references/fluke_9142.json `
  --procedure cube-log
```

### Full options

```powershell
python scripts/analisi_calib_data.py `
  --input                              data_in/points_40_50.json `
  --sensor                             models_in/sensors/ntc_temperature.json `
  --ref                                models_in/references/fluke_9142.json `
  --cert-input                         template_in/certificato_funzione_input.json `
  --cert-output                        certificato_out/certificato_funzione_filled.json `
  --pdf                                certificato_out/ntc_cert_funzione.pdf `
  --xml                                certificato_out/ntc_calibration_certificate.xml `
  --conformity-output                  certificato_out/conformity_results.json `
  --images-dir                         my_run/images `
  --procedure                          linear `
  --update-parameters                  if-out-of-tolerance `
  --check-units `
  --convert-units `
  --charts `
  --charts-interactive `
  --verbose
```
```powershell
python scripts/analisi_calib_data.py `
   --input                              data_in/export2_tmp126_lsb16.json `
   --sensor                             models_in/sensors/pt100_temp.json `
   --ref                                models_in/references/fluke_old.json `
   --cert-input                         template_in/certificato_funzione_input.json `
   --cert-output                        certificato_out/certificato_funzione_filled.json `
   --pdf                                certificato_out/ntc_cert_funzione.pdf `
   --xml                                certificato_out/ntc_calibration_certificate.xml `
   --conformity-output                  certificato_out/conformity_results.json `
   --images-dir                         ./images/calibration `
   --procedure                          linear `
   --charts `
   --convert-units `
   --old-a 0.0025246 --old-b -40.20 `
   --verbose

```



> `--charts` and `--verbose` default to **`True`**; pass `--no-charts` or `--no-verbose` to suppress them.
> `--charts-interactive` is **off by default** and requires a GUI display.

### CLI argument reference

| Argument | Default | Description |
|---|---|---|
| `--input PATH` | `data_in/export2_tmp126_lsb16.json` | LSB16 measurement payload JSON |
| `--sensor PATH` | `models_in/sensors/ntc_temperature.json` | NTC sensor model JSON |
| `--ref PATH` | `models_in/references/fluke_9142.json` | Reference calibrator JSON |
| `--cert-input PATH` | `template_in/certificato_funzione_input.json` | Certificate template (read-only) |
| `--cert-output PATH` | `certificato_out/certificato_funzione_filled.json` | Filled certificate JSON output |
| `--pdf PATH` | `certificato_out/ntc_cert_funzione.pdf` | PDF certificate output |
| `--xml PATH` | `certificato_out/ntc_calibration_certificate.xml` | DCC XML output |
| `--conformity-output PATH` | _(none)_ | Optional path to write conformity results JSON |
| `--images-dir PATH` | _(none)_ | Override base directory for plot PNGs. Subfolders `calibration/` and `conformity/` are created inside. When omitted, defaults to `images/calibration/` and `images/conformity/` relative to the calibration root. |
| `--procedure` | _(from sensor JSON)_ | Force calibration model: `linear`, `cubic`, `cube-log`, `linear_interp`, `cubic_interp` |
| `--old-a FLOAT` | _(none)_ | Previous coefficient A — overrides sensor JSON `coeffA`. Used as `old_A` by the calibration engine (as-found baseline). Injected automatically by `dcc_service` from the sensor DB record. |
| `--old-b FLOAT` | _(none)_ | Previous coefficient B — overrides sensor JSON `coeffB`. |
| `--old-c FLOAT` | _(none)_ | Previous coefficient C / a2 / C3 — overrides sensor JSON `coeffC`. |
| `--old-d FLOAT` | _(none)_ | Previous coefficient D / a3 — overrides sensor JSON `coeffD`. |
| `--charts` / `--no-charts` | `True` | Save calibration and conformity plot PNGs to `--images-dir` (or default `images/` subdirs). |
| `--charts-interactive` | `False` | **Open plots interactively** via matplotlib. The script blocks until the user closes each window. Runs after `--charts` if both are set. **Not suitable for headless/Docker environments** — requires a GUI display (e.g. local Python terminal, Jupyter, or X forwarding). |
| `--verbose` / `--no-verbose` | `True` | Print detailed progress to stdout |
| `--no-pdf` | `False` | Skip PDF generation |
| `--no-xml` | `False` | Skip DCC XML generation |
| `--update-parameters` | `none` | Parameter update strategy: `none` (do not adjust), `always` (adjust regardless), `if-out-of-tolerance` (skip when all as-found errors are within `sensorAccuracy` limits) |
| `--check-units` | `False` | Enable dimensional unit analysis via pint |
| `--convert-units` | `False` | Convert results to the preferred output unit declared in the sensor JSON |

Outputs written to `--cert-output` parent directory (default `certificato_out/`):

| File | Description |
|---|---|
| `certificato_funzione_filled.json` | Merged certificate data (template + computed) |
| `ntc_cert_funzione.pdf` | 4-page A4 PDF calibration certificate |
| `ntc_calibration_certificate.xml` | PTB DCC v3.3.0 XML |
| `conformity_results.json` | Conformity check results (only with `--conformity-output`) |

Charts are saved automatically (PNG) to:

| Directory | Contents |
|---|---|
| `<--images-dir>/calibration/` | Calibration fit and residuals |
| `<--images-dir>/conformity/` | Conformity check plots (linear model only) |

When `--images-dir` is not set, charts default to:

| Directory | Contents |
|---|---|
| `images/calibration/` | Calibration fit and residuals |
| `images/conformity/` | Conformity check plots (linear model only) |

> Conformity checks (A, B, C, D, E, F, G, H) are run automatically at the end of
> every orchestrator run.  The summary is printed to stdout with `--verbose` and
> optionally written as JSON via `--conformity-output`.

**Skip PDF, XML, or charts:**
```powershell
python scripts/analisi_calib_data.py ... --no-pdf --no-xml --no-charts
```

**Interactive chart display (local development):**
```powershell
python scripts/analisi_calib_data.py `
  --input   data_in/export2_tmp126_lsb16.json `
  --procedure linear `
  --charts-interactive
```
Opens matplotlib windows for calibration plots (all procedures), then conformity plots (linear only).
The script **blocks** at each window set until the user closes it — there is no timeout.
Useful during local development to inspect residuals, uncertainty bands, and fit quality before generating the certificate.

Can be combined with `--charts` to both display and save:
```powershell
python scripts/analisi_calib_data.py ... --charts --charts-interactive
```
In this case PNG files are written first, then the interactive windows open.

**Previous calibration coefficients (`--old-a/b/c/d`):**

The orchestrator uses as-found coefficients (from the previous calibration) to compute `M_e_pre` — the sensor reading before the new calibration is applied. When running via `dcc_service`, these are injected automatically from the sensor database record after each successful run. When running from the command line, pass them explicitly:

```powershell
python scripts/analisi_calib_data.py `
  --input   data_in/export2_tmp126_lsb16.json `
  --procedure linear `
  --old-a   0.0025248564 `
  --old-b  -40.1191245600
```

If omitted (or `0.0` in the sensor JSON — the "not set" sentinel), the engines assume a first calibration and use identity defaults for the as-found baseline.

| Procedure | `--old-a` | `--old-b` | `--old-c` | `--old-d` |
|---|---|---|---|---|
| `linear` / `linear_interp` | gain A | offset B [°C] | — | — |
| `cubic` / `cubic_interp` | a0 | a1 | a2 | a3 |
| `cube-log` | C0 | C1 | C3 | — |

After a successful run via `dcc_service`, the new coefficients are automatically written back to `sensor.coeffA/B/C/D` in the database and will be used as `--old-a/b/c/d` on the next calibration.

**Conditional parameter update:**
```powershell
# Skip coefficients update when all as-found errors are within sensorAccuracy limits
python scripts/analisi_calib_data.py ... --update-parameters if-out-of-tolerance

# Always update coefficients regardless of as-found errors (default: none)
python scripts/analisi_calib_data.py ... --update-parameters always
```
`--update-parameters` accepts three values:
- `none` (default) — do not adjust parameters regardless of errors
- `always` — force calibration parameter update even when within spec
- `if-out-of-tolerance` — skip update when ALL as-found errors are within `sensorAccuracy.maxError`; proceed normally if any point is out of range

When `if-out-of-tolerance` is set and all points are in range, the coefficient update is skipped (`calibration_done = "not_necessary"`) and the certificate reports initial coefficients for both as-found and as-left.

**Dimensional analysis (unit checks):**
```powershell
python scripts/analisi_calib_data.py ... --check-units
```
Reads the `dsi` fields from `--sensor` and `--ref` JSON files and verifies
dimensional consistency with the calibration model equation using
[pint](https://pint.readthedocs.io).  Prints a PASS/FAIL report; on hard
errors (incompatible dimensions) the calibration is blocked and the script
exits with an error.

**Unit conversion of results:**
```powershell
python scripts/analisi_calib_data.py ... --convert-units
```
After calibration, converts numeric results (offsets, uncertainties, reference
temperatures) to the preferred output unit declared in the `unit` field of the
sensor JSON (e.g. `\\degreeCelsius` → `\\kelvin`).  Adds `converted` and
`units` sub-dicts to the verbose output.  Does not affect the certificate or
PDF — those always use the native °C/LSB values.

Both flags can be used together or independently. They are disabled by default
(`--no-check-units`, `--no-convert-units`).

---

## Run a calibration engine stand-alone

Each engine in `scripts/model_calibration/` has its own `__main__` entry point:

```powershell
# Linear
python scripts/model_calibration/linear_calibration.py --verbose

# Cubic polynomial
python scripts/model_calibration/cubic_calibration.py --verbose

# Steinhart-Hart
python scripts/model_calibration/cube_log_calibration.py --verbose
```

Default input: `test/data_in/export2_tmp126_lsb16.json`.
Pass `--input PATH` to use a different payload.
Pass `--charts` to show/save diagnostic plots.

---

## Run conformity checks

Conformity checks are run **automatically** at the end of every orchestrator run
(steps A, B, C, D, E, F, G, H).  Results are printed to stdout when `--verbose`
is active and can be saved via `--conformity-output`.

To run the standalone conformity script against an already-generated JSON certificate:

```powershell
python scripts/verifica_conformita.py `
  --input   certificato_out/certificato_funzione_filled.json `
  --variant funzione `
  --verbose
```

With charts:
```powershell
python scripts/verifica_conformita.py `
  --input   certificato_out/certificato_funzione_filled.json `
  --variant funzione `
  --charts
```

Expected outcome: 5 PASS, 1 FAIL (Check B — see [docs/conformity-checks.md](docs/conformity-checks.md)).

### DCC XML Conformity Verification (Direct XML check)

You can also run direct conformity checks directly against a generated PTB DCC XML certificate (instead of JSON). This script performs as-found sensor accuracy checking (Check G), Probability of False Acceptance (Check H), and direct uncertainties overlap/compatibility analysis.

To launch the direct DCC XML conformity script:

```powershell
python scripts/verify_dcc_conformity.py `
  --xml    certificato_out/ntc_calibration_certificate.xml `
  --sensor models_in/sensors/ntc_temperature.json `
  --mae    0.10 `
  --pfa-threshold 20.0 `
  --u-ref  0.065
```

Supported arguments:
- `--xml PATH`: Required path to the DCC XML certificate file.
- `--sensor PATH`: Path to the sensor model JSON containing `sensorAccuracy` (defaults to `models_in/sensors/ntc_temperature.json`).
- `--mae FLOAT`: Maximum Accepted Error [°C] for Check H (default: `0.10` °C).
- `--pfa-threshold FLOAT`: PFA acceptance threshold [%] (default: `20.0` %).
- `--u-ref FLOAT`: Expanded uncertainty [°C] (k=2) of the reference instrument (default: `0.065` °C).

> **Note:** Conformity checks C and F (which compare against a linear A/B function)
> are skipped automatically when the certificate was produced with `--procedure cubic`
> or `--procedure cube-log`.  Conformity charts are also skipped for non-linear models.

---

## Run tests

```powershell
# From repo root
pytest backend/calibration/test/test_calibration_pipeline.py -v
```

---

## Where data comes from

| Data | CLI arg | Default source | Editable? |
|---|---|---|---|
| Measurement payload | `--input` | `data_in/export2_tmp126_lsb16.json` | yes (swap for real HW data) |
| Sensor/ADC model (NTC) | `--sensor` | `models_in/sensors/ntc_temperature.json` | yes |
| Reference calibrator model (Fluke) | `--ref` | `models_in/references/fluke_9142.json` | yes |
| Hardware constants (ADC bits, reference uncertainty) | — | `models_in/sensors/ntc_temperature.json` (`ranges.elec.adcBits`) + ref JSON (`metrology.Uncertainty[0].ub`) | yes |
| Company / lab / procedure data | `--cert-input` | `template_in/certificato_funzione_input.json` | yes (human-authored) |
| Computed outputs (coefficients, U(E), …) | `--cert-output` | `certificato_out/certificato_funzione_filled.json` | no (generated) |
| Plot image directory | `--images-dir` | `images/calibration/` + `images/conformity/` (relative to calibration root) | yes — when set, subfolders `calibration/` and `conformity/` are created inside the given path |
| Previous coefficients (A, B) | `--old-a`, `--old-b` | sensor JSON `calibrationCoefficients.A/B` (0.0 = not set); overridden by `dcc_service` from DB | injected by system; override manually for CLI runs |
| Previous coefficients (C, D) | `--old-c`, `--old-d` | sensor JSON `calibrationCoefficients.C/D` (0.0 = not set) | cubic / cube-log only |

---

## Calibration engine API contract

Every module in `scripts/model_calibration/` must expose:

| Symbol | Signature | Description |
|---|---|---|
| `calibrate(payload, lsb_scale_sensor_info, sample_size, adc_max, ub_pt_lsb, ub_tmp_lsb, verbose, risol_degc, **kwargs)` | `-> dict` | Run calibration; return result dict with `"model"` key |
| `build_report(...)` | `-> str` | Return a markdown text report |
| `plot_charts(...)` | `-> None` | Show matplotlib figures interactively |
| `save_charts(..., output_dir, prefix)` | `-> list[Path]` | Save figures as PNG |

The `calibrate()` return dict must always contain:

```
model                   str          procedure identifier
temp_nominali           list[float]  nominal step temperatures [°C]
dati_raw                dict         per-step raw arrays {rtd, log}
risultati_elaborati     dict         per-step statistics
expanded_uncertainties  list[float]  U(E) per step [°C], k=2
ref_temp_means          list[float]  mean PT100 temperature per step [°C]
lsb_per_c               float        LSB/°C conversion factor
ub_pt_lsb               float        PT100 type-B standard uncertainty [LSB]
ub_tmp_lsb              float        NTC type-B standard uncertainty [LSB]
```
