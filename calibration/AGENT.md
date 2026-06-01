# calibration/ — Agent Reference

> Keep this file and all files under `docs/` aligned with the code.
> When you change a script, data format, or folder layout, update the relevant doc.

---

## Quick orientation

| What | Where |
|---|---|
| Architecture & key files | [docs/architecture.md](docs/architecture.md) |
| Pipeline data flow | [docs/data-flow.md](docs/data-flow.md) |
| Conformity checks (verifica_conformita) | [docs/conformity-checks.md](docs/conformity-checks.md) — checks G, A–F, H |
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
│   └── conformity-checks.md
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
│   │   └── cube_log_calibration.py     GUM Steinhart-Hart engine    (--procedure cube-log)
│   ├── NTC_linear_calibration.py       LEGACY — kept for backward compat, not used by pipeline
│   ├── certificato_funzione.py         PDF certificate generator
│   ├── generate_dcc_xml.py             DCC XML generator
│   ├── certificato-copy.py             legacy base (unused by pipeline)
│   └── verifica_conformita.py          conformity checker (checks G, A–F, H)
├── models_in/
│   ├── VAR_REF_SENSOR.py               SENSOR_model, RIFERIMENTO_model, VAR_extra
│   ├── ntc_temperature.json            NTC sensor model  (--sensor, default; also in sensors/)
│   ├── fluke_9142.json                 reference calibrator model  (--ref, default; also in references/)
│   ├── sensors/                        sensor template files — served by dcc_service as dropdown options
│   │   ├── ntc_temperature.json
│   │   ├── ntc_temperature_kelvin.json
│   │   └── pt100_temp.json
│   └── references/                     reference template files — served by dcc_service as dropdown options
│       ├── fluke_9142.json
│       └── fluke_old.json
├── template_in/
│   └── certificato_funzione_input.json   human-authored, never overwritten
├── data_in/                        real hardware measurement payloads
├── test/
│   ├── data_in/export2_tmp126_lsb16.json   6-step LSB16 reference dataset
│   └── test_calibration_pipeline.py
└── certificato_out/                generated outputs (PDF, XML, filled JSON)
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
2. **`VAR_REF_SENSOR.py` stays in `models_in/`** — its `BASE_DIR` resolves relative to itself. Use `SENSOR_model.from_json(path)` / `RIFERIMENTO_model.from_json(path)` when loading from a CLI-supplied path.
3. **No CWD-relative paths** — all paths use `Path(__file__).resolve().parent` chains.
4. **Never overwrite `template_in/certificato_funzione_input.json`** — read it, write the filled copy to `certificato_out/`.
5. **Measurement rows = exactly 6 floats**: `[point, T_ref_degC, T_c_post_degC, M_e_pre_degC, M_e_post_degC, U_exp_degC]`.
6. **Mixed-domain regression** — sensor readings (D_out) stay in LSB; reference readings (PT100) stay in °C. The calibration function maps D [LSB] → T [°C] directly. `lsb_per_c` is retained as an informational field only; it must not be used to convert uncertainties or coefficients. The reference uncertainty `ub_pt_degc` is passed in °C; the NTC ADC uncertainty `ub_tmp_lsb` is passed in LSB and multiplied by the local sensitivity `|dT/dD|` at each step to obtain °C.
7. **Two model inputs**: `--sensor` (NTC JSON, e.g. `ntc_temperature.json`) and `--ref` (calibrator JSON, e.g. `fluke_9142.json`). Do NOT restore a single `--sensors` flag.
8. **New calibration procedure** → new module in `scripts/model_calibration/` + new branch in `analisi_calib_data.py` dispatch via `--procedure`.
9. **New pipeline stage** → new test class in `test/test_calibration_pipeline.py`.
10. **Doc alignment** — any change to scripts, formats, or folder layout must be reflected in the relevant `docs/` file.
11. **`NTC_linear_calibration.py`** in `scripts/` is legacy — do not import from it in the pipeline. Use `model_calibration.linear_calibration` instead.
12. **Check H parameters** — `CONFORMITY_MAE_DEGC` and `CONFORMITY_PFA_THRESHOLD_PCT` live at the top of `main()` in `analisi_calib_data.py`. They are also accepted by `run_variant()` and `check_H()` in `verifica_conformita.py`. Do NOT hardcode them anywhere else; pass them down through the call chain.
13. **`scipy` required** — `verifica_conformita.py` imports `scipy.stats` for the normal CDF used in Check H. Ensure `scipy` is present in the virtualenv.
14. **Per-step GUM uncertainty budget** — `linear_calibration.calibrate()` returns `u_budget_per_step` (list of dicts with keys `t_nom_degC`, `uA_ref_degC`, `uA_i_degC`, `u_T_ref_degC`, `u_T_i_degC`, `u_c_degC`, `U_exp_degC`, `k`). The orchestrator stores it in `_calibration_result._u_budget_per_step` in the filled JSON. `generate_dcc_xml.py` reads it and emits four extra `quantity` elements (Quantities 5–8) in the DCC list: `gp_uncertaintyTypeA_reference`, `gp_uncertaintyTypeA_sensor`, `gp_combinedStandardUncertainty`, `gp_coverageFactor`. These quantities appear **only in the XML**, not in the PDF. cubic/cube-log models do not produce a budget yet.
15. **Check H `u_std_mode`** — controls which uncertainty is used as the spread of the error distribution in the PFA formula. `CONFORMITY_PFA_U_STD_MODE` in `analisi_calib_data.py main()` is the single place to change it. Valid values: `"combined"` (default, full GUM `u_c = U_exp/k`) or `"type_a"` (NTC sensor Type A only, `uA_i_degC` from the budget, matching Carullo et al. 2024). Exposed as `--pfa-u-std-mode` CLI flag in `verifica_conformita.py`. Falls back to `"combined"` silently when `"type_a"` is requested but no budget is available.
