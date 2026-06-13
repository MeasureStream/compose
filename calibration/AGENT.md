# calibration/ — Agent Reference

> All modifications to the Python calibration scripts must use the **canonical path**:  
> `backend/compose/calibration/` (not `backend/calibration/`).  
> Keep this file and all files under `docs/` aligned with the code.
> When you change a script, data format, or folder layout, update the relevant doc.

The calibration pipeline lives at `backend/compose/calibration/` and contains:

| Directory / file | Purpose |
|---|---|
| `scripts/` | 7 Python scripts: `analisi_calib_data.py`, `calib_utils.py`, `certificato_funzione.py`, `checks_helper.py`, `evaluation_formula.py`, `generate_dcc_xml.py`, `verify_dcc_conformity.py` |
| `scripts/model_calibration/` | Calibration engine package: `linear_calibration.py`, `cubic_calibration.py`, `calib_plots.py`, `unit_checks.py` |
| `models_in/sensors/` | 5 sensor JSON models |
| `models_in/references/` | 3 reference JSON models |
| `template_in/` | 8 template files (JSON, SVG, helper script) |
| `test/` | 2 test files + `data_in/` with 1 reference dataset |
| `images/` | Pre-generated output PNGs |
| `logs/` | Runtime log files |
| `docs/` | 9 documentation files |

---

## Quick orientation

| What | Where |
|---|---|
| Architecture & key files | [docs/architecture.md](docs/architecture.md) |
| Pipeline data flow | [docs/data-flow.md](docs/data-flow.md) |
| Conformity checks | [docs/conformity-checks.md](docs/conformity-checks.md) — checks G, A–F, H |
| DCC XML verification | [docs/verify-dcc-conformity.md](docs/verify-dcc-conformity.md) |
| Calibration math reference | [docs/calibration-math.md](docs/calibration-math.md) |
| Pipeline overview (Mermaid) | [docs/calibration-overview.md](docs/calibration-overview.md) |
| Model detail (Mermaid) | [docs/model-calibration-detail.md](docs/model-calibration-detail.md) |
| Orchestrator quick reference | [docs/analisi_calib_data-reference.md](docs/analisi_calib_data-reference.md) |
| DCC verifier quick reference | [docs/verify_dcc_conformity-reference.md](docs/verify_dcc_conformity-reference.md) |
| How to run | [README.md](README.md) |

---

## Folder layout

```
calibration/
├── AGENT.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── data-flow.md
│   ├── conformity-checks.md
│   ├── calibration-math.md
│   ├── calibration-overview.md            (Mermaid pipeline flowchart)
│   ├── model-calibration-detail.md        (Mermaid submodule detail)
│   ├── analisi_calib_data-reference.md    orchestrator quick reference
│   ├── verify-dcc-conformity.md           (Mermaid DCC verifier flow)
│   └── verify_dcc_conformity-reference.md DCC verifier quick reference
├── scripts/
│   ├── analisi_calib_data.py           orchestrator (entry point)
│   │                                   — CONFORMITY_MAE_DEGC and CONFORMITY_PFA_THRESHOLD_PCT
│   │                                     hardcoded at top of main(); passed to check_H
│   │                                   — --images-dir overrides IMAGES_CALIB_DIR/IMAGES_CONFORM_DIR
│   │                                     (used by dcc_service when running per-request calibrations)
│   ├── model_calibration/              calibration engine package
│   │   ├── __init__.py
│   │   ├── linear_calibration.py       GUM OLS linear engine       (--procedure linear)
│   │   ├── cubic_calibration.py        GUM OLS cubic polynomial     (--procedure cubic)
│   │   ├── calib_plots.py              unified 5-chart PNG generator (all procedures)
│   │   └── unit_checks.py              dimensional analysis via pint (--check-units, --convert-units)
│   ├── calib_utils.py                  _lookup, SensorAccuracyChecker, conversion helpers
│   ├── checks_helper.py               conformity check library (checks G, A–F, H; invoked inline)
│   ├── verify_dcc_conformity.py        standalone DCC XML verifier (checks G, H, overlap)
│   ├── certificato_funzione.py         PDF certificate generator
│   ├── generate_dcc_xml.py             DCC XML generator
│   └── evaluation_formula.py           safe eval of formula strings from sensor JSON
│
├── models_in/
│   ├── sensors.json                    aggregated sensor listing
│   ├── sensors/                        sensor template files — served by dcc_service as dropdown options
│   │   ├── ntc_temperature.json            NTC sensor model  (--sensor, default)
│   │   ├── ntc_temperature_kelvin.json     NTC sensor model (Kelvin variant)
│   │   ├── ntc_temperature_formula.json    NTC sensor model with formula-based calibration
│   │   ├── pt100_temp.json                 PT100 RTD sensor model
│   │   └── pressure_sensor.json            pressure sensor model
│   └── references/                     reference template files — served by dcc_service as dropdown options
│       ├── fluke_9142.json             reference calibrator model  (--ref, default)
│       ├── fluke_old.json
│       └── pressure_ref.json           pressure reference model
├── template_in/
│   ├── certificato_funzione_input.json   human-authored base template, never overwritten
│   ├── calibration_method.json           calibration method definition
│   ├── base_input.json                   alternative base certificate template
│   ├── client_company.json               client company data
│   ├── measurestream_company.json        Measurestream company data
│   ├── job.json                          job/assignment metadata
│   ├── gruppone.svg                      group logo
│   └── build_input_json.py               utility to assemble certificate input
├── test/
│   ├── test_calibration_pipeline.py       end-to-end pipeline tests
│   ├── test_features.py                   unit tests for linear/cubic + conformity
│   └── data_in/export2_tmp126_lsb16.json  6-step LSB16 reference dataset
├── images/                         pre-generated output images from pipeline runs
│   ├── calibration/                 calibration 5-chart PNGs
│   │   ├── calibration/             linear PNGs (calib_linear_fig1..fig5)
│   │   └── conformity/              conformity PNGs (residuals, asfound)
│   └── conformity/                  conformity PNGs (residuals, asfound)
└── logs/                           log files (e.g. pt100.txt)
```

## Integration with dcc_service (calibration run flow)

When the frontend triggers "Calibrate" on a `CalibrationRequest` row:

1. `dcc_service` (`CalibrationRunService`) creates a per-run directory at `<CALIBRATION_RUNS_PATH>/<calibrationId>/` with:
   - `input/export.json`        ← processedJson from the CalibrationRequest
   - `input/certificato_in.json` ← certificatoIn from the Calibration wizard
   - `output/`                  ← filled JSON, PDF, DCC XML, conformity JSON
   - `images/calibration/`      ← plot PNGs from calibration step
   - `images/conformity/`       ← plot PNGs from conformity step

2. `PythonBridgeService.runCalibration()` launches:
   ```
   python analisi_calib_data.py
     --input    <run>/input/export.json
     --sensor   models_in/sensors/<sensorJson>
     --ref      models_in/references/<refJson>
     --cert-input  <run>/input/certificato_in.json
     --cert-output <run>/output/certificato_funzione_filled.json
     --pdf      <run>/output/ntc_cert_funzione.pdf
     --xml      <run>/output/ntc_calibration_certificate.xml
     --conformity-output <run>/output/conformity.json
     --images-dir <run>/images
     [--procedure <proc>] [--no-charts] [--no-pdf] [--no-xml] ...
   ```

3. Results are persisted in the `Calibration` entity:
   - `runStatus` (SUCCESS / FAILED)
   - `runLog` (full stdout+stderr)
   - `resultJson` (certificato_funzione_filled.json content)
   - `conformityJson` (conformity.json content)
   - `dccXml` (ntc_calibration_certificate.xml content)
   - `pdfOutputUrl` (/api/calibrations/static/runs/<runId>/output/ntc_cert_funzione.pdf)
   - `images` (JSON array of /api/calibrations/static/runs/<runId>/images/... URLs)

4. Static files are served by `CalibrationWizardController.serveStaticFile()` at
   `GET /api/calibrations/static/runs/**`

**Configuration properties** (application.properties):
- `CALIBRATION_SCRIPT_PATH` → path to `analisi_calib_data.py`
- `CALIBRATION_MODELS_PATH` → path to `calibration/models_in/`
- `CALIBRATION_RUNS_PATH`   → base directory for run output (default: `./calibration-runs`)

---

## Hard rules

1. **No `certificato_centigradi`** — dropped, do not reintroduce.
2. **Model data loaded directly from JSON** — sensor and reference models are loaded via `json.loads()` from `models_in/sensors/*.json` and `models_in/references/*.json`. Use the `_lookup` helper in `calib_utils.py` to search lists of dicts by key=value. Do NOT reintroduce `VAR_REF_SENSOR.py` or its dataclass wrappers.
3. **No CWD-relative paths** — all paths use `Path(__file__).resolve().parent` chains.
4. **Never overwrite `template_in/certificato_funzione_input.json`** — read it, write the filled copy to `certificato_out/`.
5. **Measurement rows = exactly 6 floats**: `[point, T_ref_degC, T_c_post_degC, M_e_pre_degC, M_e_post_degC, U_exp_degC]`.
6. **Mixed-domain regression** — sensor readings (D_out) stay in LSB; reference readings (PT100) stay in °C. The calibration function maps D [LSB] → T [°C] directly. `lsb_per_c` is retained as an informational field only; it must not be used to convert uncertainties or coefficients. The reference uncertainty `ub_pt_degc` is passed in °C; the NTC ADC uncertainty `ub_tmp_lsb` is passed in LSB and multiplied by the local sensitivity `|dT/dD|` at each step to obtain °C.
7. **Two model inputs**: `--sensor` (NTC JSON, e.g. `ntc_temperature.json`) and `--ref` (calibrator JSON, e.g. `fluke_9142.json`). Do NOT restore a single `--sensors` flag.
8. **New calibration procedure** → new module in `scripts/model_calibration/` + new branch in `analisi_calib_data.py` dispatch via `--procedure`. Valid procedures: `linear`, `cubic`.
9. **New pipeline stage** → new test class in the relevant test file (`test_calibration_pipeline.py` for pipeline, `test_features.py` for unit tests).
10. **Doc alignment** — any change to scripts, formats, or folder layout must be reflected in the relevant `docs/` file.
11. **`NTC_linear_calibration.py` is gone** — the source .py file has been removed (only .pyc cache remains). Do not reintroduce; use `model_calibration.linear_calibration` instead.
12. **Check H parameters** — `CONFORMITY_MAE_DEGC` and `CONFORMITY_PFA_THRESHOLD_PCT` live at the top of `main()` in `analisi_calib_data.py`. They are also accepted by `run_variant()` and `check_H()` in `checks_helper.py`. Do NOT hardcode them anywhere else; pass them down through the call chain.
13. **`scipy` required** — `checks_helper.py` imports `scipy.stats` for the normal CDF used in Check H. `verify_dcc_conformity.py` uses pure `math.erf` (no scipy dependency). Ensure `scipy` is present in the virtualenv.
14. **Per-step GUM uncertainty budget** — `linear_calibration.calibrate()` returns `u_budget_per_step` (list of dicts with keys `t_nom_degC`, `uA_ref_degC`, `uA_i_degC`, `u_T_ref_degC`, `u_T_i_degC`, `u_c_degC`, `U_exp_degC`, `k`). The orchestrator stores it in `_calibration_result._u_budget_per_step` in the filled JSON. `generate_dcc_xml.py` reads it and emits four extra `quantity` elements (Quantities 5–8) in the DCC list: `gp_uncertaintyTypeA_reference`, `gp_uncertaintyTypeA_sensor`, `gp_combinedStandardUncertainty`, `gp_coverageFactor`. These quantities appear **only in the XML**, not in the PDF. Both `linear` and `cubic` models produce budgets.
15. **Check H `u_std_mode`** — controls which uncertainty is used as the spread of the error distribution in the PFA formula. `CONFORMITY_PFA_U_STD_MODE` in `analisi_calib_data.py main()` is the single place to change it. Valid values: `"combined"` (default, full GUM `u_c = U_exp/k`) or `"type_a"` (NTC sensor Type A only, `uA_i_degC` from the budget, matching Carullo et al. 2024). Exposed as `--pfa-u-std-mode` CLI flag in `checks_helper.py` and `verify_dcc_conformity.py`. Falls back to `"combined"` silently when `"type_a"` is requested but no budget is available.
16. **`calib_utils.py`** — shared utilities (`_lookup`, `SensorAccuracyChecker`, `lsb_to_degc`, `degc_to_lsb`, `round_to_significant_figures`). Import from here, do not duplicate these helpers in other modules.
17. **`calib_plots.py`** — unified chart generator at 600 dpi producing 5 standard figures per procedure. All calibration engines use this single module for plot generation. Do not add plot code to individual engine modules.
18. **`unit_checks.py`** — dimensional analysis via `pint` (lazy import, optional dependency). Provides `check_dsi()` and `convert_result()`. Controlled by `--check-units` and `--convert-units` CLI flags in `analisi_calib_data.py`.
19. **Conformity check modules** — `checks_helper.py` is the primary conformity check library, invoked inline by the orchestrator (checks G, A–F, H). `verify_dcc_conformity.py` is a separate DCC XML verifier (checks G, H, overlap).
20. **PDF page 4** — currently only renders linear model coefficients in a dedicated table. The cubic model produces coefficients in the filled JSON but the PDF builder does not yet render them on page 4. This is a known gap.
