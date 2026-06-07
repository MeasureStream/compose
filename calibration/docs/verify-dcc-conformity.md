# verify_dcc_conformity.py  —  flow

```mermaid
flowchart TD
    CLI["main() — CLI entry\n--xml  required DCC XML path\n--sensor  models_in/ntc_temperature.json\n--mae  0.10 degC\n--pfa-threshold  20.0 pct\n--u-ref  0.065 degC\n--images-dir  optional"]

    PARSE["parse_dcc_xml()\nextract from DCC XML 3.3.0\n  basic_referenceValue     -> t_ref\n  basic_measuredValue      -> t_sensor\n  gp_measurementError...   -> me_pre\n  basic_measurementError   -> me_post\n  uncertaintyXMLList       -> u_exp\nfallbacks: derive missing  pad/truncate mismatches"]

    SENSOR["load_sensor_accuracy_ranges()\nreads metrology.sensorAccuracy\nfrom sensor JSON\neach entry: tempMin  tempMax  maxError"]

    MATH["normal_cdf(x, mu, sigma)\nPhi = 0.5 * (1 + erf((x-mu)/(sigma*sqrt(2))))\npure math.erf  no scipy"]

    CORE["run_checks()\nt_ref  t_sensor  me_pre  u_sensor\naccuracy_ranges  mae  pfa_threshold  u_ref"]

    subgraph CHECK_G["CHECK G — Sensor Accuracy As-Found"]
        G1["for each point:\n  applicable = maxError where tempMin <= t_ref <= tempMax\n  G1: abs(me_pre) <= min(applicable)\n  G2: t_ref covered by at least one range\nverdict: PASS / WARN uncovered / FAIL out-of-limit"]
    end

    subgraph CHECK_H["CHECK H — Probability of False Acceptance"]
        H1["u_std = u_exp / 2\nu_ein = u_std / MAE\nein   = me_pre / MAE\nPFA = 1 - Phi(1; ein, u_ein) + Phi(-1; ein, u_ein)\nPFA <= threshold pct  ->  PASS"]
    end

    subgraph CHECK_OVL["OVERLAP — uncertainties compatibility"]
        O1["diff = abs(t_sensor - t_ref)\nsimple:  diff <= u_sns + u_ref\nRSS:     diff <= sqrt(u_sns^2 + u_ref^2)\nboth must pass"]
    end

    REPORT["print_results_report()\nextracted data table per point\ncheck G per-point details\ncheck H PFA table\noverlap table\nOVERALL: CONFORMING / NON-CONFORMING"]

    CHARTS["save_charts()\n4 PNG via matplotlib  dpi=150\n  fig1_pfa_chart.png       PFA bar chart\n  fig2_error_bars.png      pre/post errors +- U\n  fig3_overlap_check.png   diff vs simple/RSS bands\n  fig4_check_g_accuracy.png accuracy scatter"]

    CLI --> PARSE
    CLI --> SENSOR
    MATH --> CORE
    PARSE --> CORE
    SENSOR --> CORE
    CORE --> CHECK_G
    CORE --> CHECK_H
    CORE --> CHECK_OVL
    CORE --> REPORT
    CORE --> CHARTS
```

## formulas

| check | formula |
|-------|---------|
| G | `abs(me_pre) <= maxError(tempMin..tempMax)` |
| H | `PFA = 1 - Phi(1; ein, u_ein) + Phi(-1; ein, u_ein)` |
| H normal cdf | `Phi(x; mu, sigma) = 0.5 * (1 + erf((x - mu) / (sigma * sqrt(2))))` |
| overlap simple | `abs(T_sns - T_ref) <= U_sns + U_ref` |
| overlap RSS | `abs(T_sns - T_ref) <= sqrt(U_sns^2 + U_ref^2)` |
