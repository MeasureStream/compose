# model calibration  —  submodule detail

```mermaid
flowchart TD
    DISP["_run_calibration\nin analisi_calib_data.py\ndispatches by procedure string"]

    subgraph LIN_G["linear_calibration.py"]
        LIN["T = A*D + B\nOLS via np.linalg.lstsq\nu2_yhat = u2_B + D^2*u2_A + 2D*cov_AB\nU = k * u_yhat  k=2\noutput: A B u_A u_B cov_AB\nu_budget_per_step  dati_raw"]
    end

    subgraph CUB_G["cubic_calibration.py"]
        CUB["T = a0 + a1*D + a2*D^2 + a3*D^3\ndesign matrix X = [1 D D^2 D^3]\ncov_theta = sigma^2 * inv(X'X)\nu_yhat = sqrt(g' * cov_theta * g)\noutput: theta cov_theta u_a0..u_a3\nexports cubic_predict()"]
    end

    subgraph UTIL["shared utilities"]
        PLT["calib_plots.py\nunified 5-chart generator\nsave_five_charts() -> PNG"]
        UNT["unit_checks.py\nPint-based DSI checks\ncheck_dsi()  convert_result()"]
    end

    DISP -->|"linear"| LIN
    DISP -->|"cubic"| CUB

    LIN -.-> PLT
    CUB -.-> PLT

    LIN -.-> UNT
    CUB -.-> UNT
```

### common interface — every model exports

| function | role |
|----------|------|
| `calibrate(payload, lsb_scale, sample_size, adc_max, ub_ref_y, ub_sensor_lsb, ...) -> dict` | runs calibration, returns result dict |
| `save_charts(...) -> list[path]` | saves 5 PNG charts to disk |
| `plot_charts(...)` | interactive matplotlib display |
| `main()` | standalone CLI entry |

### common inputs — all models

| param | value |
|-------|-------|
| `payload` | LSB16 JSON dict, per-step raw samples |
| `lsb_scale_sensor_info` | `{minPhysVal, maxPhysVal}` from sensor JSON |
| `sample_size` | 20 |
| `adc_max` | 65535.0  (16-bit ADC) |
| `ub_ref_y` | ref type-B uncertainty in native Y from ref JSON |
| `ub_sensor_lsb` | sensor type-B uncertainty LSB from sensor JSON |
| `risol` | sensor resolution in Y |
