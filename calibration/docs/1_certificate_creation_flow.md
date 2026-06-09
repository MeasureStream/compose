1. CalibrationRequest arrives (from Kafka/IoT device)
        ↓
2. User clicks "Compila Certificato" → CertificatoWizard.tsx opens
        ↓
3. Wizard initialized via POST /api/calibrations/requests/{requestId}/wizard/init
   → CalibrationWizardService.initWizard() creates Calibration entity
   → pre-fills all 5 step JSONs from classpath templates (calibration_templates/)
   → auto-generates job.json from CalibrationRequest data (IDs, dates, sensor info)
        ↓
4. User fills 6 wizard steps → each saved individually via PUT /api/calibrations/wizard/{id}/step

        Step 0 - Base Input: review/edit base_input.json (PDF labels, structural placeholders)
        Step 1 - Calibration Method: select from anagrafica registry + adaptive JSON form
        Step 2 - Lab Company (Measurestream): select from anagrafica registry + adaptive JSON form
        Step 3 - Client Company: select from anagrafica registry + adaptive JSON form
        Step 4 - Job: review/edit auto-generated job.json (order IDs, dates, sensor info, personnel)
        Step 5 - Review & Build: read-only preview of the merged certificato_in output

5. User clicks "Build" → POST /api/calibrations/wizard/{id}/build
   → CalibrationWizardService.buildCertificatoIn() asserts all 5 JSONs present
   → PythonBridgeService invokes: python build_input_json.py --method --client --job --out
   → build_input_json.py merges 5 JSONs → certificato_funzione_input.json (cettificato_in)
   → stored in Calibration.certificatoIn, sets calibrated = true
        ↓
6. User configures run → GET /api/calibrations/wizard/{id}/run-config
   → returns available sensors, references, and procedures for selection
        ↓
7. User clicks "Run Calibration" → POST /api/calibrations/wizard/{id}/run
   → CalibrationRunService.runCalibration() resolves Sensor, creates runs/{runId}/ directory
   → CalibrationRunConfig: sensor model, reference model, procedure, charts, verbose, unit options
   → PythonBridgeService invokes: python analisi_calib_data.py
        --input export.json --sensor <sensor.json> --ref <ref.json>
        --cert-input certificato_in.json --cert-output <out.json> --pdf <out.pdf>
        --xml <out.xml> --conformity-output conformity.json --images-dir <images/>
   → analisi_calib_data.py runs: OLS regression + fills template + generates PDF + generates DCC XML
   → produces: certificato_funzione_filled.json, DCC XML, PDF, conformity.json, calibration charts (PNG)
   → new calibration coefficients written back to Sensor entity (via SensorCoefficientUpdater)
        ↓
8. User clicks "Save DCC" → POST /api/calibrations/wizard/{id}/save-dcc
   → DCC XML converted to JSON via gemimeg-backend (POST /api/v1/dcc/xsd/dcc/json)
   → Dcc entity created in database (linked to Sensor + CalibrationRequest)
        ↓
9. User clicks "Validate & Sign" → POST /api/dcc/{id}/validate
   → DccService.validateDcc(): JSON → XML/PDF via gemimeg
   → DccSigningService.performSigningAndVerification(): signs with private key + X.509 cert
   → signed files uploaded to S3 (hashXml, hashPdf stored)

```mermaid
flowchart TD
    A[CalibrationRequest arrives<br>from Kafka/IoT device] --> B[User clicks 'Compila Certificato'<br>CertificatoWizard.tsx opens]

    B --> C[POST /api/calibrations/requests/:requestId/wizard/init<br>creates Calibration entity<br>pre-fills all 5 step JSONs from templates<br>auto-generates job.json]

    C --> D[User fills 6 wizard steps<br>each saved via PUT /api/calibrations/wizard/:id/step]
    D --> D0[Step 0 - Base Input<br>review/edit base_input.json]
    D --> D1[Step 1 - Calibration Method<br>select from anagrafica registry<br>+ adaptive JSON form]
    D --> D2[Step 2 - Lab Company Measurestream<br>select from anagrafica registry<br>+ adaptive JSON form]
    D --> D3[Step 3 - Client Company<br>select from anagrafica registry<br>+ adaptive JSON form]
    D --> D4[Step 4 - Job<br>review/edit auto-generated job.json]
    D --> D5[Step 5 - Review and Build<br>read-only preview of<br>merged certificato_in]

    D5 --> E[User clicks Build<br>POST /api/calibrations/wizard/:id/build]
    E --> F[build_input_json.py<br>merges 5 JSONs<br>produces certificato_funzione_input.json]
    F --> G[Certificate administrative data compiled]
```

**Graph 2 — Calibration Run, DCC Save and Signing**

*Continues from: certificatoIn stored, calibrated = true*

```mermaid
flowchart TD
    G[Certificate administrative data compiled]

    G --> H[User configures run<br>GET /api/calibrations/wizard/:id/run-config<br>select sensor, reference, procedure]

    H --> I[User clicks Run Calibration<br>POST /api/calibrations/wizard/:id/run]
    I --> J[analisi_calib_data.py runs<br>OLS regression + fill template<br>+ PDF generation + DCC XML generation<br>+ conformity checks]
    J --> K[Outputs:<br>certificato_funzione_filled.json<br>DCC XML + PDF<br>conformity.json + charts PNG<br>Sensor coefficients updated]

    K --> L[User clicks Save DCC<br>POST /api/calibrations/wizard/:id/save-dcc]
    L --> M[XML to JSON via gemimeg-backend<br>Dcc entity created in DB<br>linked to Sensor + CalibrationRequest]

    M --> N[User clicks Validate and Sign<br>POST /api/dcc/:id/validate]
    N --> O[JSON to XML/PDF via gemimeg<br>signed with private key + X.509 cert<br>uploaded to S3]
```
