# calibration  —  high-level flow

```mermaid
flowchart LR
    subgraph INPUTS
        LSB["LSB16 payload\ndata_in/*.json"]
        SNJ["Sensor JSON\nmodels_in/ntc_temperature.json"]
        RFJ["Reference JSON\nmodels_in/fluke_9142.json"]
        TMJ["Template JSON\ntemplate_in/certificato_funzione_input.json"]
        VRS["VAR_REF_SENSOR.py\nSENSOR_model + RIFERIMENTO_model\nVAR_extra: adc=16, U_pt=0.065, d_tmp=0.30"]
    end

    subgraph ORCH["ORCHESTRATOR"]
        MAIN["analisi_calib_data.py\n1 load models\n2 resolve procedure + old coeffs\n3 _run_calibration dispatch\n4 sensor accuracy gate\n5 _build_cert_filled\n6 PDF + XML generation\n7 calib charts\n8 conformity checks"]
    end

    subgraph MODELS["MODEL CALIBRATION"]
        LIN["linear_calibration.py\nT = A*D + B\nLinear OLS GUM"]
        CUB["cubic_calibration.py\nT = a0+a1*D+a2*D^2+a3*D^3\nCubic OLS GUM"]
    end

    subgraph OUTPUTS
        FJS["certificato_funzione_filled.json"]
        PDF["ntc_cert_funzione.pdf"]
        XML["ntc_calibration_certificate.xml\nDCC 3.3.0"]
        CNF["conformity.json\nchecks G A B C D E F H"]
        CHT["PNG charts\nimages/calibration/\nimages/conformity/"]
    end

    subgraph VERIFIERS["STANDALONE VERIFIERS"]
        VCF["verifica_conformita.py\n8 checks from filled JSON"]
        DCC["verify_dcc_conformity.py\n3 checks from DCC XML"]
    end

    LSB --> MAIN
    SNJ --> MAIN
    RFJ --> MAIN
    TMJ --> MAIN
    VRS --> MAIN

    MAIN --> LIN
    MAIN --> CUB

    MAIN --> FJS
    MAIN --> PDF
    MAIN --> XML
    MAIN --> CNF
    MAIN --> CHT

    FJS --> VCF
    XML --> DCC
```
