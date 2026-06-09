# Project Structure

## 1. Overall Project Structure

The project root `C:\code\thesis-dcc` contains:

```
.benchmarks/
.cursor/
.git/
.pytest_cache/
.ruff_cache/
.venv/
backend/                              # Main backend code
docs/                                 # Project documentation (6 .md files)
frontend/                             # Three frontend projects
materiale/                            # Reference materials
ntc_calibration_certificate_template.pdf  # Template PDF at root level
old/                                  # Deprecated/old code (interp/ directory)
tesi/                                 # LaTeX thesis document
todo.md                               # Task list
```

### Backend (`backend/`) — 16 subdirectories

| Directory | Purpose |
|-----------|---------|
| `calibration/` | Python calibration pipeline — **THE FOCUS** of this report |
| `calibrator_test/` | Calibrator hardware testing |
| `calibrator-manager/` | Java microservice: MQTT communication with Fluke 9142 |
| `compose/` | Docker compose files |
| `dcc_service/` | Java Spring Boot DCC Service: central orchestrator |
| `fake_lora/` | Fake LoRaWAN simulation |
| `fake_me/` | Fake measurement endpoint |
| `gateway-iam/` | API Gateway + IAM (Keycloak OIDC) |
| `gemimeg-backend/` | GEMIMEG backend (PTB reference DCC tool) |
| `kafka_test/` | Kafka throughput benchmarks |
| `measure/` | Java microservice: measurement storage (MongoDB) |
| `sensor-manager/` | Java microservice: sensor template registry |
| `settings-manager/` | Java microservice: settings management |
| `settings/` | Settings database |
| `sign/` | Cryptographic signing service |
| `verify/` | Signature verification service |

### Frontend (`frontend/`) — 3 projects

- **`frontend_new/`** — React 19 + TypeScript + Vite 6 + MUI 6 (primary operator/inspector UI)
- **`frontend_old/`** — Older frontend
- **`gemimeg-frontend/`** — Angular 16 GEMIMEG reference frontend from PTB

### Thesis (`tesi/`)

| File/Dir | Description |
|----------|-------------|
| `thesis.tex` | Main LaTeX file |
| `content/chapters.tex` | All 8 chapters + appendices (997 lines) |
| `bibliography.bib` | References |
| `images/`, `picture/` | Figures |
| `common/` | Shared LaTeX styles |

---

## 2. Full File Listing of `backend/calibration/`

### Top-level

```
backend/calibration/
  README.md
  docs/
    analisi_calib_data-reference.md
    architecture.md
    calibration-math.md
    calibration-overview.md
    conformity-checks.md
    data-flow.md
    model-calibration-detail.md
    verify_dcc_conformity-reference.md
    verify-dcc-conformity.md
  data_in/
    calib_20_45_30_40.json
    convert22042026_payload_lsb16.json
    export2_tmp126_lsb16.json
    export2_tmp126_lsb16_first.json
    export2_tmp126_lsb16_second.json
    export2_tmp126_millic_from_lsb16.json
    points_40_45_50.json
    points_40_50.json
  certificato_out/
    ntc_calibration_certificate.xml
    ntc_calibration_certificate_test.xml
    ntc_cert_funzione.pdf
    ntc_cert_funzione_test.pdf
    test_cubic_interp.json
    test_linear_interp.json
  images/
    calibration/
      calib_linear_fig1_sample_timeseries.png ... fig5_post_residuals.png
      calib_cubic_fig1_sample_timeseries.png ... fig5_post_residuals.png
    conformity/
      conformity_fig1_residuals.png
      conformity_fig2_asfound.png
      conformity_fig2_gum_budget.png
      conformity_fig3_calibration_curve.png
  logs/
    pt100.txt
  models_in/
    sensors.json
    sensors/
      ntc_temperature.json
      ntc_temperature_kelvin.json
      pt100_temp.json
    references/
      fluke_9142.json
      fluke_old.json
  template_in/
    base_input.json
    build_input_json.py
    calibration_method.json
    certificato_funzione_input.json
    certificato_funzione_input.json.bak
    client_company.json
    gruppone.svg
    job.json
    measurestream_company.json
  scripts/
    __init__.py
    analisi_calib_data.py              # MAIN ORCHESTRATOR
    calib_utils.py                     # Utilities
    certificato_funzione.py            # PDF generator (ReportLab)

    checks_helper.py                   # Conformity checks A-H
    generate_dcc_xml.py                # DCC XML generator (PTB v3.3.0)
    verify_dcc_conformity.py           # Standalone conformity verifier
    model_calibration/
      __init__.py                      # Package init
      calib_plots.py                   # Unified 5-chart PNG generator
      cubic_calibration.py             # Cubic polynomial OLS engine
      linear_calibration.py            # Linear OLS engine
      unit_checks.py                   # Dimensional analysis (pint)
  test/
    test_calibration_pipeline.py       # Integration tests (1256 lines)
    test_features.py                   # Unit tests for linear/cubic
    data_in/
      export2_tmp126_lsb16.json
```

---

## 3. `chapters.tex` File

**Location:** `C:\code\thesis-dcc\tesi\content\chapters.tex` (997 lines)

This is the main thesis content file containing all 8 chapters plus appendices:

| Chapter | Title | Key Content |
|---------|-------|-------------|
| 1 | Introduction | Context, problem definition, objectives, thesis structure |
| 2 | State of the Art | MeasureStream analysis, microservices, Kafka, DCC standards |
| 3 | System Architecture | 20-container Docker infrastructure, DCC Service, Python pipeline |
| 4 | Calibration Process | Operator/inspector roles, wizard, GEMIMEG integration, conformity |
| 5 | Calibration and Conformity Assessment | Mathematical foundation — linear/cubic interpolation, linear regression, GUM uncertainty propagation, PFA |
| 6 | User Interface | PDF viewer, XML viewer, React walkthrough, GEMIMEG tool |
| 7 | Validation, Testing and Results | Testing methodology, calibration accuracy, RMSE comparison |
| 8 | Conclusions and Future Developments | Summary, 24-bit ADC, blockchain, ML, additional sensors |
| A | DCC XML Example | Full PTB v3.3.0 XML output |
| B | Sensor and Reference JSON Models | `ntc_temperature.json` and `fluke_9142.json` |
| C | REST API Endpoints | Endpoint listing |

---

## 4. Python Scripts that Generate Certificates/PDFs for Calibration

### A. `analisi_calib_data.py` — The Main Orchestrator

**Path:** `C:\code\thesis-dcc\backend\calibration\scripts\analisi_calib_data.py` (852 lines)

The central orchestrator invoked by the Java DCC Service. It:

- Dispatches to one of 2 calibration engines (`linear`, `cubic`)
- Calls `_build_cert_filled()` to merge calibration results into the certificate JSON template
- Optionally calls `certificato_funzione.py` to generate the PDF
- Optionally calls `generate_dcc_xml.py` to generate the DCC XML
- Runs conformity checks G, A, B, H via `checks_helper.py`
- Generates 5 diagnostic PNG charts per calibration

### B. `certificato_funzione.py` — PDF Certificate Generator

**Path:** `C:\code\thesis-dcc\backend\calibration\scripts\certificato_funzione.py` (733 lines)

Generates a 4-page A4 PDF calibration certificate using ReportLab:

| Page | Content |
|------|---------|
| 1 | General data (certificate title, issue date, instrument identification) |
| 2 | Procedure details (calibration method, traceability, environmental conditions) |
| 3 | Measurement results table (5–6 columns: Point, T_ref, T_c, M_e_pre, M_e_post, U(E)) |
| 4 | Calibration function coefficients + approval signature block |

### C. `generate_dcc_xml.py` — DCC XML Generator

**Path:** `C:\code\thesis-dcc\backend\calibration\scripts\generate_dcc_xml.py` (661 lines)

Generates a PTB DCC v3.3.0 XML certificate using Python's `ElementTree`:

- **`administrativeData`:** unique identifier, dates, instrument identification, laboratory contact
- **`measurementResults`:** per-point measurement table with D-SI hybrid units
- **8 quantity columns:** reference temperature, calibrated temperature, M_e pre/post, expanded uncertainty, type-A uncertainties, combined u_c, coverage factor k
- **Namespaces:** `dcc`, `si`, `ds`, `xades`

### Supporting Scripts

| Script | Lines | Purpose |
|--------|-------|---------|
| `calib_plots.py` | 870 | Unified 5-chart PNG generator (600 dpi) |
| `checks_helper.py` | 544 | Conformity checks A through H |
| `verify_dcc_conformity.py` | — | Standalone DCC XML conformity verifier |

---

## 5. Cubic/Polynomial Fitting Implementation

**Path:** `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\cubic_calibration.py` (557 lines)

### Mathematical Model (mixed domain)

```
T_ref [°C] = a0  +  a1·D  +  a2·D²  +  a3·D³
```

Fits directly in °C vs. LSB (mixed domain), not LSB-to-LSB.

**Polynomial degree:** `_POLY_DEGREE = 3`, `_N_COEFFS = 4`

### Core Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `_regressor_row(d)` | 22–23 | Builds `[1, d, d², d³]` row vector |
| `_build_design_matrix(x_lsb)` | 30–34 | Builds full n×4 design matrix |
| `_fit_cubic(x_lsb, y_lsb)` | 37–45 | OLS fit: `θ = (XᵀX)⁻¹Xᵀy`, returns `θ`, `(XᵀX)⁻¹`, `X` |
| `_gum_propagation_cubic(x, y, u_x, u_y)` | 48–71 | Full GUM analytical propagation: sensitivity of each coefficient to x and y uncertainties, element-by-element covariance accumulation |
| `cubic_predict(d_lsb, theta)` | 74–75 | Evaluate cubic at any LSB value |
| `cubic_predict_y(...)` | 78–87 | Wrapper (backward compatible) |
| `cubic_uncertainty(d_lsb, u_d_lsb, theta, cov_theta)` | 90–102 | GUM uncertainty: `u²(T) = xᵀ·cov_θ·x + (df_dD · u_d)²` |
| `run_prechecks(...)` | 105–154 | Validates ≥4 steps, optional pint unit checks |
| `calibrate(...)` | 157–283 | Main calibration: prechecks, fit, per-step GUM budget |
| `save_charts(...)` | 391–475 | Saves 5 PNG diagnostic plots via `calib_plots` |

### GUM Uncertainty Propagation (lines 48–71)

The `_gum_propagation_cubic` function analytically computes the covariance matrix of all 4 coefficients by accumulating contributions from each measurement's x (sensor LSB) and y (reference °C) uncertainties:

```
for each measurement i:
    dθ/dy_i = (XᵀX)⁻¹ · x_i
        # sensitivity to y uncertainty
    dθ/dx_i = (XᵀX)⁻¹ · (g_i·y_i - d(XᵀX)/dx_i · θ)
        # sensitivity to x uncertainty
    cov_θ += (dθ/dy_i)(dθ/dy_i)ᵀ · u_y[i]²
    cov_θ += (dθ/dx_i)(dθ/dx_i)ᵀ · u_x[i]²
```

### Per-Step Sensitivity (lines 229–250)

Local sensitivity at each calibration point:

```
s_i = |a1 + 2·a2·D_i + 3·a3·D_i²|  [°C/LSB]
```

Used to convert LSB-side uncertainties to °C.

---

## 6. Regression / RMSE / Uncertainty Search Results

225 matches across Python files for `regression|RMSE|uncertainty`.

### Regression

| File | Location | Description |
|------|----------|-------------|
| `linear_calibration.py` | `_compute_gum_ols_coefficients()` (line 169) | GUM OLS linear regression with analytical sensitivity |
| `cubic_calibration.py` | `_fit_cubic()` | Cubic polynomial OLS regression |
| `calib_plots.py` | lines 468, 517, 527 | RMSE computation |
| `test_features.py` | line 249 | "Full calibration output (linear) — numeric regression test" |
| `__init__.py` | — | `linear_calibration` / `cubic_calibration` docstrings |

### RMSE

| File | Line(s) | Code / Content |
|------|---------|----------------|
| `calib_plots.py` | 468 | `label=f"RMSE = {rmse:.4f} {bundle.unit_symbol}"` |
| `calib_plots.py` | 527 | `label=f"RMSE{suffix} = {rmse:.4f} {bundle.unit_symbol}"` |
| `chapters.tex` | 946 | RMSE comparison table across all 5 methods |
| `chapters.tex` | 443 | `\mathrm{RMSE}_{\mathrm{int}}` (linear interpolation) |
| `chapters.tex` | 546 | `\mathrm{RMSE}` (cubic interpolation) |

### Uncertainty (GUM)

| File | Location | Description |
|------|----------|-------------|
| `linear_calibration.py` | `_compute_gum_ols_coefficients()` (lines 169–203) | Full analytical propagation |
| `cubic_calibration.py` | `_gum_propagation_cubic()` (lines 48–71) | Full analytical propagation |
| `cubic_calibration.py` | `cubic_uncertainty()` (lines 90–102) | Per-point prediction uncertainty |
| `calib_plots.py` | docstring (lines 1–36) | GUM per-point combined standard uncertainty in all 5 figures |
| `generate_dcc_xml.py` | — | Per-step GUM budget in XML (quantities 5–8) |
| `checks_helper.py` | Check H | PFA via `u_E = U(E)/k` (JCGM 106) |
| `test_calibration_pipeline.py` | lines 770–1093 | Uncertainty budget validation, PFA, GUM quantities in XML |

---

## Summary of Key File Paths

| Purpose | Absolute Path |
|---------|---------------|
| Thesis chapters | `C:\code\thesis-dcc\tesi\content\chapters.tex` |
| Main orchestrator | `C:\code\thesis-dcc\backend\calibration\scripts\analisi_calib_data.py` |
| Cubic calibration engine | `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\cubic_calibration.py` |
| Linear calibration engine | `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\linear_calibration.py` |
| PDF certificate generator | `C:\code\thesis-dcc\backend\calibration\scripts\certificato_funzione.py` |
| DCC XML generator | `C:\code\thesis-dcc\backend\calibration\scripts\generate_dcc_xml.py` |
| Chart/plot generator | `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\calib_plots.py` |
| Conformity checks | `C:\code\thesis-dcc\backend\calibration\scripts\checks_helper.py` |
| Certificate JSON template | `C:\code\thesis-dcc\backend\calibration\template_in\certificato_funzione_input.json` |
| Sensor model (NTC) | `C:\code\thesis-dcc\backend\calibration\models_in\sensors\ntc_temperature.json` |
| Reference model (Fluke) | `C:\code\thesis-dcc\backend\calibration\models_in\references\fluke_9142.json` |
| Integration tests | `C:\code\thesis-dcc\backend\calibration\test\test_calibration_pipeline.py` |
| Unit tests | `C:\code\thesis-dcc\backend\calibration\test\test_features.py` |
| README | `C:\code\thesis-dcc\backend\calibration\README.md` |
| Dimensional checks | `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\unit_checks.py` |
| Model package init | `C:\code\thesis-dcc\backend\calibration\scripts\model_calibration\__init__.py` |
