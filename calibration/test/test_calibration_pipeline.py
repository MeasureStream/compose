"""
test_calibration_pipeline.py
============================
Integration tests for the calibration pipeline.

Pipeline under test
-------------------
    1. analisi_calib_data  (orchestrator)
       -> model_calibration.linear_calibration.calibrate()
       -> _build_cert_filled()
       -> certificato_funzione (PDF)
       -> generate_dcc_xml (DCC XML)

Run from repo root
------------------
    pytest backend/calibration/test/test_calibration_pipeline.py -v

Or run directly
---------------
    python backend/calibration/test/test_calibration_pipeline.py
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest


# Path bootstrap — give every test access to calibration/scripts/ and
# calibration/models_in/ without needing an installed package.

TESTS_DIR   = Path(__file__).resolve().parent          # calibration/test/
CALIB_ROOT  = TESTS_DIR.parent                         # calibration/
SCRIPTS_DIR = CALIB_ROOT / "scripts"
MODELS_DIR  = CALIB_ROOT / "models_in"
TEMPLATE_DIR = CALIB_ROOT / "template_in"
DATA_DIR    = TESTS_DIR / "data_in"
MODELS_TEST_DIR = TESTS_DIR / "models_in"
OUT_DIR     = CALIB_ROOT / "certificato_out"

for p in (str(SCRIPTS_DIR),):
    if p not in sys.path:
        sys.path.insert(0, p)


# Fixtures / constants

INPUT_JSON      = DATA_DIR / "export2_tmp126_lsb16.json"
SENSOR_JSON     = MODELS_DIR / "sensors" / "ntc_temperature.json"
REF_JSON        = MODELS_DIR / "references" / "fluke_9142.json"
CERT_INPUT_JSON = TEMPLATE_DIR / "certificato_funzione_input.json"
CERT_OUT_JSON   = OUT_DIR / "certificato_funzione_filled_test.json"
PDF_OUT         = str(OUT_DIR / "ntc_cert_funzione_test.pdf")
XML_OUT         = OUT_DIR / "ntc_calibration_certificate_test.xml"

ADC_BITS = 16
ADC_MAX  = float((1 << ADC_BITS) - 1)   # 65535.0
LSB_MIN  = -40.0
LSB_MAX  = 105.0
LSB_SPAN = LSB_MAX - LSB_MIN            # 145.0
LSB_PER_C = ADC_MAX / LSB_SPAN

SAMPLE_SIZE = 20

U_EXP_REL_TOL = 0.05


# Helpers


def _lsb_scale_sensor_info():
    return {"minPhysVal": LSB_MIN, "maxPhysVal": LSB_MAX}


def _load_payload():
    return json.loads(INPUT_JSON.read_text(encoding="utf-8"))


def _load_calib_result():
    from model_calibration.linear_calibration import calibrate

    ub_ref_y = 0.0325   # reference type-B std uncertainty [°C]
    sensor_json = json.loads(SENSOR_JSON.read_text(encoding="utf-8"))
    _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
    ub_sensor_lsb = float(next((it["value"] for it in _sensor_ru if it.get("varName") == "uB"), 0.30))

    payload = _load_payload()
    return calibrate(
        payload=payload,
        lsb_scale_sensor_info=_lsb_scale_sensor_info(),
        sample_size=SAMPLE_SIZE,
        adc_max=ADC_MAX,
        ub_ref_y=ub_ref_y,
        ub_sensor_lsb=ub_sensor_lsb,
        verbose=False,
    )


# ===========================================================================
# 1. Input data sanity
# ===========================================================================

class TestInputData:
    def test_input_json_exists(self):
        assert INPUT_JSON.exists(), f"Test data file missing: {INPUT_JSON}"

    def test_sensors_json_exists(self):
        assert SENSOR_JSON.exists(), f"ntc_temperature.json missing: {SENSOR_JSON}"
        assert REF_JSON.exists(), f"fluke_9142.json missing: {REF_JSON}"

    def test_cert_input_template_exists(self):
        assert CERT_INPUT_JSON.exists(), f"Certificate template missing: {CERT_INPUT_JSON}"

    def test_payload_has_required_keys(self):
        payload = _load_payload()
        for key in ("steps", "reference_temperature_samples", "sensor_raw_samples"):
            assert key in payload, f"Missing key in payload: {key}"

    def test_payload_has_six_steps(self):
        payload = _load_payload()
        assert len(payload["steps"]) == 6

    def test_payload_step_format(self):
        """Each step must be parseable as '(float, int)'."""
        import re
        payload = _load_payload()
        pattern = re.compile(r"\(\s*[-+]?\d*\.?\d+\s*,\s*\d+\s*\)")
        for step in payload["steps"]:
            assert pattern.match(step), f"Bad step format: {step!r}"

    def test_sensor_json_defaults(self):
        sensor_json = json.loads(SENSOR_JSON.read_text(encoding="utf-8"))
        assert sensor_json.get("calibration", {}).get("calibrationCoefficients", {}).get("A", {}).get("dsi") == "\\degreeCelsius"
        _threshold = sensor_json.get("ranges", {}).get("threshold", {})
        assert _threshold.get("min") == pytest.approx(-40.0)
        assert _threshold.get("max") == pytest.approx(105.0)


# ===========================================================================
# 2. Calibration module (model_calibration.linear_calibration)
# ===========================================================================

# Mixed-domain: X [LSB] sensor, Y [physical unit] reference.
# A [unit/LSB] ≈ LSB_SPAN / ADC_MAX ≈ 0.00221 °C/LSB
# B [unit]     ≈ LSB_MIN + small offset ≈ near -40 °C
_NOMINAL_A = LSB_SPAN / ADC_MAX   # expected gain ~0.002213 °C/LSB


class TestLinearCalibration:
    @pytest.fixture(scope="class")
    def result(self):
        return _load_calib_result()

    def test_returns_required_keys(self, result):
        required = {"A", "B", "u_A", "u_B", "cov_AB",
                    "temp_nominali", "dati_raw", "risultati_elaborati",
                    "expanded_uncertainties", "ref_temp_means", "lsb_per_y",
                    "ub_ref_y", "ub_sensor_lsb"}
        assert required.issubset(result.keys())

    def test_six_nominal_temps(self, result):
        assert len(result["temp_nominali"]) == 6

    def test_nominal_temps_are_expected(self, result):
        expected = [0.0, 25.0, 50.0, 75.0, 100.0, 125.0]
        assert result["temp_nominali"] == pytest.approx(expected, abs=0.1)

    def test_A_is_near_nominal(self, result):
        # A [°C/LSB] ≈ LSB_SPAN / ADC_MAX; allow ±20% for sensor nonlinearity
        assert result["A"] == pytest.approx(_NOMINAL_A, rel=0.20)

    def test_B_is_near_lsb_min(self, result):
        # B [°C] ≈ LSB_MIN for a well-behaved sensor; allow ±5 °C
        assert abs(result["B"] - LSB_MIN) < 5.0, f"B={result['B']:.4f} far from LSB_MIN={LSB_MIN}"

    def test_u_A_positive_and_small(self, result):
        assert result["u_A"] > 0.0
        assert result["u_A"] < _NOMINAL_A * 0.01   # < 1 % of gain

    def test_u_B_positive(self, result):
        assert result["u_B"] > 0.0

    def test_lsb_per_y_correct(self, result):
        expected = ADC_MAX / LSB_SPAN
        assert result["lsb_per_y"] == pytest.approx(expected, rel=1e-6)

    def test_expanded_uncertainties_count(self, result):
        assert len(result["expanded_uncertainties"]) == 6

    def test_expanded_uncertainties_positive(self, result):
        for u in result["expanded_uncertainties"]:
            assert u > 0.0, f"Non-positive U(E): {u}"

    def test_expanded_uncertainties_reasonable(self, result):
        # U(E) at each step should be < 1 physical unit
        for u in result["expanded_uncertainties"]:
            assert u < 1.0, f"U(E)={u} exceeds 1 unit — check uncertainty budget"

    def test_ref_temp_means_count(self, result):
        assert len(result["ref_temp_means"]) == 6

    def test_ref_temp_means_within_physical_range(self, result):
        # ref means native °C — within physical sensor range with buffer
        for t in result["ref_temp_means"]:
            assert LSB_MIN - 5 <= t <= 135.0, f"ref_temp_mean {t} implausible"

    def test_risultati_elaborati_has_all_steps(self, result):
        for t in result["temp_nominali"]:
            assert t in result["risultati_elaborati"]

    def test_per_step_pmean_ref_and_log_not_zero(self, result):
        for t, r in result["risultati_elaborati"].items():
            assert r["pmean_ref"] != 0.0, f"pmean_ref is zero at {t}"
            assert r["pmean_sensor"] != 0.0, f"pmean_sensor is zero at {t}"


# ===========================================================================
# 3. Certificate JSON builder (_build_certificato_filled via orchestrator)
# ===========================================================================

class TestCertificatoFilledJSON:
    @pytest.fixture(scope="class")
    def cert_filled(self):
        """Run the orchestrator with --no-pdf --no-xml to produce only the JSON."""
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "analisi_calib_data.py"),
                "--input",   str(INPUT_JSON),
                "--sensor", str(SENSOR_JSON),
                "--ref", str(REF_JSON),
                "--cert-input",  str(CERT_INPUT_JSON),
                "--cert-output", str(CERT_OUT_JSON),
                "--pdf",  PDF_OUT,
                "--xml",  str(XML_OUT),
                "--no-pdf",
                "--no-xml",
                "--no-verbose",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"analisi_calib_data.py failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert CERT_OUT_JSON.exists(), "Certificate JSON not written"
        return json.loads(CERT_OUT_JSON.read_text(encoding="utf-8"))

    def test_template_parts_present(self, cert_filled):
        assert "template_parts" in cert_filled

    def test_calibration_result_present(self, cert_filled):
        assert "_calibration_result" in cert_filled

    def test_variant_is_funzione(self, cert_filled):
        assert cert_filled["_calibration_result"]["_variant"] == "funzione"

    def test_measurements_present(self, cert_filled):
        tp = cert_filled["template_parts"]
        calc = tp["calculated_calibration_values"]
        measurements = calc.get("measurements", calc.get("_measurements"))
        assert measurements is not None
        assert len(measurements) == 6

    def test_each_measurement_row_has_six_columns(self, cert_filled):
        tp = cert_filled["template_parts"]
        calc = tp["calculated_calibration_values"]
        measurements = calc.get("measurements", calc.get("_measurements"))
        for i, row in enumerate(measurements):
            assert len(row) == 6, f"Row {i} has {len(row)} columns, expected 6"

    def test_measurement_point_numbers_are_sequential(self, cert_filled):
        tp = cert_filled["template_parts"]
        calc = tp["calculated_calibration_values"]
        measurements = calc.get("measurements", calc.get("_measurements"))
        for i, row in enumerate(measurements):
            assert int(row[0]) == i + 1

    def test_t_ref_within_range(self, cert_filled):
        # T_ref is now native PT100 °C — must be within a physically plausible range.
        # The ADC LSB_MAX (105°C) is the sensor threshold, not a hard cap on reference
        # temperatures; calibration points may go up to the nominal step maximum (125°C).
        tp = cert_filled["template_parts"]
        calc = tp["calculated_calibration_values"]
        measurements = calc.get("measurements", calc.get("_measurements"))
        for row in measurements:
            t_ref = row[1]
            assert LSB_MIN - 5 <= t_ref <= 135.0, f"T_ref={t_ref} implausible"

    def test_u_exp_positive(self, cert_filled):
        tp = cert_filled["template_parts"]
        calc = tp["calculated_calibration_values"]
        measurements = calc.get("measurements", calc.get("_measurements"))
        for i, row in enumerate(measurements):
            assert row[5] > 0.0, f"U(E) non-positive at row {i}: {row[5]}"

    def test_calibration_coefficients_populated(self, cert_filled):
        cr = cert_filled["_calibration_result"]
        assert "_A" in cr and "_B" in cr
        # A is now in [°C/LSB]; for a 16-bit ADC over a 145°C range it is ~0.00221 °C/LSB.
        # Allow ±20 % of the nominal scale value.
        nominal_a = (LSB_MAX - LSB_MIN) / ADC_MAX   # ≈ 0.00221 °C/LSB
        assert cr["_A"] == pytest.approx(nominal_a, rel=0.20)

    def test_sensor_model_in_sensor_method_template(self, cert_filled):
        smt = cert_filled["template_parts"]["sensor_method_template"]
        assert "sensor_model" in smt
        m = smt["sensor_model"]
        assert "_A_cal" in m
        assert "_B_cal" in m


# ===========================================================================
# 4. PDF generation (certificato_funzione)
# ===========================================================================

class TestPDFGeneration:
    @pytest.fixture(scope="class")
    def pdf_path(self):
        """Generate the PDF from the already-produced filled JSON."""
        assert CERT_OUT_JSON.exists(), (
            "Run TestCertificatoFilledJSON first to produce the filled JSON"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "certificato_funzione.py"),
                "--input",  str(CERT_OUT_JSON),
                "--output", PDF_OUT,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"certificato_funzione.py failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        return Path(PDF_OUT)

    def test_pdf_exists(self, pdf_path):
        assert pdf_path.exists(), f"PDF not found at: {pdf_path}"

    def test_pdf_non_empty(self, pdf_path):
        assert pdf_path.stat().st_size > 1024, "PDF file is suspiciously small"

    def test_pdf_starts_with_pdf_magic_bytes(self, pdf_path):
        header = pdf_path.read_bytes()[:4]
        assert header == b"%PDF", f"File does not start with PDF header: {header!r}"


# ===========================================================================
# 5. DCC XML generation (generate_dcc_xml)
# ===========================================================================

class TestDCCXMLGeneration:
    @pytest.fixture(scope="class")
    def xml_path(self):
        """Generate the DCC XML from the already-produced filled JSON."""
        assert CERT_OUT_JSON.exists(), (
            "Run TestCertificatoFilledJSON first to produce the filled JSON"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "generate_dcc_xml.py"),
                "--input",  str(CERT_OUT_JSON),
                "--output", str(XML_OUT),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"generate_dcc_xml.py failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        return XML_OUT

    def test_xml_exists(self, xml_path):
        assert xml_path.exists(), f"XML not found at: {xml_path}"

    def test_xml_non_empty(self, xml_path):
        assert xml_path.stat().st_size > 512, "XML file is suspiciously small"

    def test_xml_is_parseable(self, xml_path):
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        assert root is not None

    def test_xml_root_tag_is_dcc(self, xml_path):
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        assert "digitalCalibrationCertificate" in root.tag

    def test_xml_has_administrative_data(self, xml_path):
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = "https://ptb.de/dcc"
        admin = root.find(f"{{{ns}}}administrativeData")
        assert admin is not None, "administrativeData element missing"

    def test_xml_has_measurement_results(self, xml_path):
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = "https://ptb.de/dcc"
        meas = root.find(f"{{{ns}}}measurementResults")
        assert meas is not None, "measurementResults element missing"

    def test_xml_schema_version(self, xml_path):
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        version = root.get("schemaVersion")
        assert version == "3.3.0", f"Unexpected schemaVersion: {version}"


# ===========================================================================
# 6. Full end-to-end via orchestrator CLI (analisi_calib_data.py)
# ===========================================================================

class TestEndToEnd:
    """
    Runs analisi_calib_data.py with --no-pdf --no-xml and verifies that
    the filled JSON was produced with correct structure and sane numeric values.
    """

    @pytest.fixture(scope="class")
    def e2e_result(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "analisi_calib_data.py"),
                "--input",       str(INPUT_JSON),
                "--sensor",      str(SENSOR_JSON),
                "--ref",         str(REF_JSON),
                "--cert-input",  str(CERT_INPUT_JSON),
                "--cert-output", str(CERT_OUT_JSON),
                "--pdf",  PDF_OUT,
                "--xml",  str(XML_OUT),
                "--no-pdf",
                "--no-xml",
                "--no-verbose",
            ],
            capture_output=True,
            text=True,
        )
        return result

    def test_orchestrator_exits_zero(self, e2e_result):
        assert e2e_result.returncode == 0, (
            f"Orchestrator non-zero exit:\nSTDOUT:\n{e2e_result.stdout}"
            f"\nSTDERR:\n{e2e_result.stderr}"
        )

    def test_cert_output_written(self, e2e_result):
        assert CERT_OUT_JSON.exists()

    def test_cert_json_valid(self, e2e_result):
        data = json.loads(CERT_OUT_JSON.read_text(encoding="utf-8"))
        assert "template_parts" in data
        assert "_calibration_result" in data

    def test_six_measurement_rows(self, e2e_result):
        data = json.loads(CERT_OUT_JSON.read_text(encoding="utf-8"))
        calc = data["template_parts"]["calculated_calibration_values"]
        meas = calc.get("measurements", calc.get("_measurements"))
        assert len(meas) == 6

    def test_error_degC_reasonable(self, e2e_result):
        """Post-calibration error at each point should be < 10.0 °C."""
        data = json.loads(CERT_OUT_JSON.read_text(encoding="utf-8"))
        calc = data["template_parts"]["calculated_calibration_values"]
        meas = calc.get("measurements", calc.get("_measurements"))
        for row in meas:
            error = abs(row[4])
            assert error < 10.0, f"Post-cal error {error:.4f} °C exceeds 10.0 °C"

    def test_u_exp_bounded(self, e2e_result):
        """U(E) should be < 1 °C at every calibration point."""
        data = json.loads(CERT_OUT_JSON.read_text(encoding="utf-8"))
        calc = data["template_parts"]["calculated_calibration_values"]
        meas = calc.get("measurements", calc.get("_measurements"))
        for row in meas:
            assert row[5] < 1.0, f"U(E)={row[5]:.4f} °C exceeds 1 °C"


# ===========================================================================
# 7. Conformity Check H — PFA (Probability of False Acceptance)
# ===========================================================================

class TestConformityCheckH:
    """
    Unit tests for verifica_conformita.check_H — PFA of the as-found error.

    Check H uses the AS-FOUND (pre-calibration) error M_e_pre (column 3)
    and the full GUM expanded uncertainty U_exp (column 5).  The normalised
    quantities are:

        Ein   = M_e_pre / MAE          (normalised as-found error)
        u_Ein = (U_exp/k) / MAE        (normalised standard uncertainty)

    PFA_i = 1 - NCDF(1; Ein_i, u_Ein_i) + NCDF(-1; Ein_i, u_Ein_i)
          = 1 - NCDF(MAE; M_e_pre_i, u_std_i) + NCDF(-MAE; M_e_pre_i, u_std_i)

    where k = 2 and NCDF is the normal CDF.

    Row format: [punto, T_ref, T_c_post, M_e_pre, M_e_post, U_exp]
                  [0]    [1]    [2]       [3]       [4]       [5]
    """

    
    # Helpers
    

    @staticmethod
    def _row(punto: int, me_pre: float, u_exp: float, t_ref: float = 25.0) -> list:
        """
        Build a synthetic measurement row.
        me_pre goes into column 3 (as-found error); column 4 (M_e_post)
        is set to 0.0 (irrelevant for Check H).
        """
        return [float(punto), t_ref, t_ref, me_pre, 0.0, u_exp]

    @staticmethod
    def _run(rows, mae_y: float, pfa_threshold_pct: float):
        from checks_helper import check_H
        return check_H(rows, mae_y=mae_y, pfa_threshold_pct=pfa_threshold_pct, verbose=False)

    
    # Return structure
    

    def test_returns_tuple_status_list(self):
        rows = [self._row(1, me_pre=0.0, u_exp=0.10)]
        status, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        assert isinstance(status, str)
        assert isinstance(detail, list)

    def test_detail_has_one_entry_per_row(self):
        rows = [self._row(i, me_pre=0.0, u_exp=0.05) for i in range(1, 5)]
        _, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        assert len(detail) == 4

    def test_detail_keys_present(self):
        rows = [self._row(1, me_pre=0.0, u_exp=0.05)]
        _, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        expected_keys = {
            "punto", "T_ref_y", "M_e_pre_y", "Ein", "U_exp_y",
            "u_std_y", "u_Ein", "MAE_y", "PFA_pct", "PFA_threshold_pct", "pass",
        }
        assert expected_keys.issubset(detail[0].keys())

    
    # Numerical correctness
    

    def test_pfa_zero_error_small_uncertainty_is_low(self):
        """M_e_pre=0, U_exp << MAE → PFA ≈ 0."""
        from scipy.stats import norm
        mae = 0.10
        u_exp = 0.01   # u_std = 0.005, u_Ein = 0.05 → very narrow distribution
        _, detail = self._run([self._row(1, me_pre=0.0, u_exp=u_exp)], mae, 20.0)
        u_std = u_exp / 2.0
        expected_pfa = (
            1.0 - norm.cdf(mae, loc=0.0, scale=u_std)
            + norm.cdf(-mae, loc=0.0, scale=u_std)
        ) * 100.0
        assert detail[0]["PFA_pct"] == pytest.approx(expected_pfa, abs=0.01)

    def test_pfa_zero_error_large_uncertainty_matches_formula(self):
        """M_e_pre=0, U_exp >> MAE → PFA is large (close to 100 %)."""
        from scipy.stats import norm
        mae = 0.10
        u_exp = 0.70   # u_std = 0.35, u_Ein = 3.5
        _, detail = self._run([self._row(1, me_pre=0.0, u_exp=u_exp)], mae, 20.0)
        u_std = u_exp / 2.0
        expected_pfa = (
            1.0 - norm.cdf(mae, loc=0.0, scale=u_std)
            + norm.cdf(-mae, loc=0.0, scale=u_std)
        ) * 100.0
        assert detail[0]["PFA_pct"] == pytest.approx(expected_pfa, abs=0.01)

    def test_pfa_error_at_mae_boundary(self):
        """M_e_pre = MAE → upper tail ≈ 50 %; lower tail ≈ 0 for small uncertainty."""
        from scipy.stats import norm
        mae = 0.10
        u_exp = 0.04   # u_std = 0.02
        _, detail = self._run([self._row(1, me_pre=mae, u_exp=u_exp)], mae, 20.0)
        u_std = u_exp / 2.0
        expected_pfa = (
            1.0 - norm.cdf(mae, loc=mae, scale=u_std)
            + norm.cdf(-mae, loc=mae, scale=u_std)
        ) * 100.0
        assert detail[0]["PFA_pct"] == pytest.approx(expected_pfa, abs=0.1)

    def test_pfa_stored_mae_matches_parameter(self):
        mae = 0.25
        _, detail = self._run([self._row(1, 0.0, 0.05)], mae_y=mae, pfa_threshold_pct=20.0)
        assert detail[0]["MAE_y"] == pytest.approx(mae)

    def test_pfa_stored_threshold_matches_parameter(self):
        _, detail = self._run([self._row(1, 0.0, 0.05)], mae_y=0.10, pfa_threshold_pct=15.0)
        assert detail[0]["PFA_threshold_pct"] == pytest.approx(15.0)

    def test_u_std_is_u_exp_over_two(self):
        u_exp = 0.352
        _, detail = self._run([self._row(1, 0.0, u_exp)], 0.10, 20.0)
        assert detail[0]["u_std_y"] == pytest.approx(u_exp / 2.0, rel=1e-9)

    def test_u_ein_is_u_std_over_mae(self):
        """u_Ein = u_std / MAE (normalised uncertainty, dimensionless)."""
        mae = 0.10
        u_exp = 0.352
        u_std = u_exp / 2.0
        _, detail = self._run([self._row(1, 0.0, u_exp)], mae_y=mae, pfa_threshold_pct=20.0)
        assert detail[0]["u_Ein"] == pytest.approx(u_std / mae, rel=1e-9)

    def test_ein_is_me_pre_over_mae(self):
        """Ein = M_e_pre / MAE (normalised as-found error, dimensionless)."""
        mae = 0.10
        me_pre = 0.07
        _, detail = self._run([self._row(1, me_pre=me_pre, u_exp=0.05)], mae_y=mae, pfa_threshold_pct=20.0)
        assert detail[0]["Ein"] == pytest.approx(me_pre / mae, rel=1e-9)

    def test_me_pre_stored_correctly(self):
        """The stored M_e_pre_y must equal the as-found error passed in column 3."""
        me_pre = 0.063
        _, detail = self._run([self._row(1, me_pre=me_pre, u_exp=0.05)], 0.10, 20.0)
        assert detail[0]["M_e_pre_y"] == pytest.approx(me_pre)

    
    # PASS / FAIL logic
    

    def test_pass_when_pfa_below_threshold(self):
        """Very small uncertainty, zero as-found error → PFA ≈ 0 → PASS."""
        status, detail = self._run(
            [self._row(1, me_pre=0.0, u_exp=0.01)],
            mae_y=0.10,
            pfa_threshold_pct=20.0,
        )
        assert detail[0]["pass"] is True
        assert status == "PASS"

    def test_fail_when_pfa_above_threshold(self):
        """U_exp >> MAE, zero error → PFA >> 20 % → FAIL."""
        status, detail = self._run(
            [self._row(1, me_pre=0.0, u_exp=0.70)],
            mae_y=0.10,
            pfa_threshold_pct=20.0,
        )
        assert detail[0]["pass"] is False
        assert status == "FAIL"

    def test_large_as_found_error_causes_fail(self):
        """As-found error >> MAE → PFA high even with small uncertainty → FAIL."""
        status, detail = self._run(
            [self._row(1, me_pre=0.50, u_exp=0.04)],   # error = 5× MAE
            mae_y=0.10,
            pfa_threshold_pct=20.0,
        )
        assert detail[0]["PFA_pct"] > 95.0
        assert detail[0]["pass"] is False
        assert status == "FAIL"

    def test_overall_fail_if_any_point_fails(self):
        """One point passes (small error+unc), one fails (large unc) → overall FAIL."""
        rows = [
            self._row(1, me_pre=0.0, u_exp=0.01),   # low PFA → PASS
            self._row(2, me_pre=0.0, u_exp=0.70),   # high PFA → FAIL
        ]
        status, _ = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        assert status == "FAIL"

    def test_overall_pass_all_points_pass(self):
        rows = [self._row(i, me_pre=0.0, u_exp=0.01) for i in range(1, 4)]
        status, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        assert status == "PASS"
        assert all(r["pass"] for r in detail)

    def test_pfa_bounded_between_0_and_100(self):
        """PFA_pct must always be in [0, 100]."""
        rows = [
            self._row(1, me_pre=0.0, u_exp=0.001),    # nearly zero PFA
            self._row(2, me_pre=0.0, u_exp=100.0),    # nearly 100 % PFA
            self._row(3, me_pre=50.0, u_exp=0.001),   # error >> MAE → PFA ≈ 100 %
        ]
        _, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        for r in detail:
            assert 0.0 <= r["PFA_pct"] <= 100.0

    def test_zero_uncertainty_within_mae_passes(self):
        """u_std=0, |M_e_pre| < MAE → PFA = 0 → PASS."""
        status, detail = self._run(
            [self._row(1, me_pre=0.05, u_exp=0.0)],   # |0.05| < MAE=0.10
            mae_y=0.10,
            pfa_threshold_pct=20.0,
        )
        assert detail[0]["PFA_pct"] == pytest.approx(0.0)
        assert detail[0]["pass"] is True
        assert status == "PASS"

    def test_zero_uncertainty_outside_mae_fails(self):
        """u_std=0, |M_e_pre| > MAE → PFA = 100 % → FAIL."""
        status, detail = self._run(
            [self._row(1, me_pre=0.20, u_exp=0.0)],   # |0.20| > MAE=0.10
            mae_y=0.10,
            pfa_threshold_pct=20.0,
        )
        assert detail[0]["PFA_pct"] == pytest.approx(100.0)
        assert detail[0]["pass"] is False
        assert status == "FAIL"

    def test_threshold_boundary_neighbourhood(self):
        """
        Verify correct PASS/FAIL on either side of the 20 % threshold.

        For M_e_pre=0, PFA = 2*NCDF(-1; 0, u_Ein) = 2*NCDF(-MAE; 0, u_std).
        The boundary u_std satisfies: u_std = MAE / norm.ppf(1 - threshold/2).
        We test 1 % either side so the result is unambiguous.
        """
        from scipy.stats import norm
        mae = 0.10
        threshold_pct = 20.0
        threshold = threshold_pct / 100.0
        u_std_boundary = mae / norm.ppf(1.0 - threshold / 2.0)

        # 1 % below boundary → PFA < threshold → PASS
        u_exp_low = u_std_boundary * 0.99 * 2.0
        _, detail_low = self._run(
            [self._row(1, me_pre=0.0, u_exp=u_exp_low)],
            mae_y=mae, pfa_threshold_pct=threshold_pct,
        )
        assert detail_low[0]["PFA_pct"] < threshold_pct
        assert detail_low[0]["pass"] is True

        # 1 % above boundary → PFA > threshold → FAIL
        u_exp_high = u_std_boundary * 1.01 * 2.0
        _, detail_high = self._run(
            [self._row(1, me_pre=0.0, u_exp=u_exp_high)],
            mae_y=mae, pfa_threshold_pct=threshold_pct,
        )
        assert detail_high[0]["PFA_pct"] > threshold_pct
        assert detail_high[0]["pass"] is False

    def test_six_point_dataset_structure(self):
        """Six-row dataset (real calibration size) returns six per-point dicts."""
        rows = [self._row(i, me_pre=float(i) * 0.005, u_exp=0.352) for i in range(1, 7)]
        status, detail = self._run(rows, mae_y=0.10, pfa_threshold_pct=20.0)
        assert len(detail) == 6
        for i, r in enumerate(detail):
            assert r["punto"] == i + 1


# ===========================================================================
# 7. Uncertainty budget in linear_calibration return dict
# ===========================================================================

class TestLinearCalibUBudget:
    """
    Verify that linear_calibration.calibrate() returns the per-step GUM
    uncertainty budget in the result dict under 'u_budget_per_step'.
    """

    @staticmethod
    def _load_payload_and_info():
        """Load the reference dataset and sensor info for a real calibration run."""
        payload = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
        lsb_scale = {"minPhysVal": LSB_MIN, "maxPhysVal": LSB_MAX}
        return payload, lsb_scale

    @pytest.fixture(scope="class")
    def calib_result(self):
        payload, lsb_scale = self._load_payload_and_info()
        from model_calibration.linear_calibration import calibrate
        ub_ref_y = 0.0325   # reference type-B std uncertainty [°C]
        sensor_json = json.loads(SENSOR_JSON.read_text(encoding="utf-8"))
        _sensor_ru = sensor_json.get("metrology", {}).get("readingUncertainty", [])
        ub_sensor_lsb = float(next((it["value"] for it in _sensor_ru if it.get("varName") == "uB"), 0.30))
        return calibrate(
            payload=payload,
            lsb_scale_sensor_info=lsb_scale,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_ref_y=ub_ref_y,
            ub_sensor_lsb=ub_sensor_lsb,
            verbose=False,
        )

    def test_budget_key_present(self, calib_result):
        assert "u_budget_per_step" in calib_result

    def test_budget_length_matches_steps(self, calib_result):
        n_steps = len(calib_result["temp_nominali"])
        assert len(calib_result["u_budget_per_step"]) == n_steps

    def test_budget_entry_keys(self, calib_result):
        expected = {
            "t_nom", "uA_ref", "uA_sensor",
            "ub_uso", "u_fitting",
            "u_ref", "u_sensor", "u_c", "U_exp", "k",
        }
        for entry in calib_result["u_budget_per_step"]:
            assert expected.issubset(entry.keys()), f"Missing keys in: {entry.keys()}"

    def test_k_is_two(self, calib_result):
        for entry in calib_result["u_budget_per_step"]:
            assert entry["k"] == pytest.approx(2.0)

    def test_u_exp_matches_expanded_uncertainties(self, calib_result):
        """U_exp in budget must equal expanded_uncertainties list."""
        for entry, u_exp in zip(
            calib_result["u_budget_per_step"],
            calib_result["expanded_uncertainties"],
        ):
            assert entry["U_exp"] == pytest.approx(u_exp, rel=1e-9)

    def test_u_c_times_k_equals_u_exp(self, calib_result):
        """k * u_c must equal U_exp (definition of expanded uncertainty)."""
        for entry in calib_result["u_budget_per_step"]:
            assert entry["k"] * entry["u_c"] == pytest.approx(
                entry["U_exp"], rel=1e-9
            )

    def test_u_c_combines_T_ref_and_T_i(self, calib_result):
        """u_c = sqrt(u_T_ref^2 + u_T_i^2) must hold per entry."""
        import math
        for entry in calib_result["u_budget_per_step"]:
            expected_uc = math.sqrt(
                entry["u_ref"] ** 2 + entry["u_sensor"] ** 2
            )
            assert entry["u_c"] == pytest.approx(expected_uc, rel=1e-9)

    def test_uA_ref_is_non_negative(self, calib_result):
        for entry in calib_result["u_budget_per_step"]:
            assert entry["uA_ref"] >= 0.0

    def test_uA_i_is_non_negative(self, calib_result):
        for entry in calib_result["u_budget_per_step"]:
            assert entry["uA_sensor"] >= 0.0


# ===========================================================================
# 8. Uncertainty budget quantities in generated DCC XML
# ===========================================================================

class TestDCCXMLUncertaintyBudget:
    """
    Verify that generate_dcc_xml.build_dcc_tree() emits the four extra
    uncertainty-budget quantity elements (Quantities 5–8) when the input
    data contains '_u_budget_per_step'.

    Tests use build_dcc_tree() directly with a minimal synthetic data dict
    so no file fixtures are required.
    """

    N_STEPS = 4   # arbitrary number of calibration steps for the synthetic data

    @staticmethod
    def _make_budget(n: int):
        """Synthetic per-step budget consistent with GUM: U = k * u_c."""
        import math
        budget = []
        for i in range(n):
            uA_ref = 0.002 + i * 0.001
            uA_i   = 0.003 + i * 0.001
            u_T_ref = math.sqrt(uA_ref**2 + 0.0325**2)
            u_T_i   = math.sqrt(uA_i**2 + 0.173**2)
            u_c     = math.sqrt(u_T_ref**2 + u_T_i**2)
            U_exp   = 2.0 * u_c
            budget.append({
                "t_nom":   float(-20 + i * 40),
                "uA_ref":  uA_ref,
                "uA_sensor":    uA_i,
                "u_ref": u_T_ref,
                "u_sensor":   u_T_i,
                "u_c":     u_c,
                "U_exp":   U_exp,
                "k":            2.0,
            })
        return budget

    @pytest.fixture(scope="class")
    def xml_root(self):
        """Build the DCC XML tree from a minimal synthetic data dict and return the root."""
        import xml.etree.ElementTree as ET
        import sys
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        from generate_dcc_xml import build_dcc_tree

        n = self.N_STEPS
        budget = self._make_budget(n)
        # Minimal measurements rows (6-element funzione format)
        meas = [
            [float(i + 1), float(-20 + i * 40), float(-20 + i * 40), 0.05, 0.001, budget[i]["U_exp"]]
            for i in range(n)
        ]
        data = {
            "cert": {
                "certificate_title": "Test",
                "certificate_number": "TEST-001",
                "issue_date": "2026-01-01",
                "customer": "Test Customer",
                "request_date": "2026-01-01",
                "receipt_date": "2026-01-01",
                "measurement_dates": "2026-01-01",
                "item": "NTC",
                "device_type": "NTC thermistor",
                "manufacturer": "Acme",
                "model": "NTC-1",
                "serial_number": "SN-001",
                "asset_id": "A-001",
                "lab_reference": "LAB-001",
                "procedure_code": "PROC-001",
                "calibration_method": "comparison",
                "traceability": "PTB",
                "conditions": {},
                "environment": {"temperature": "23 ± 1.5", "relative_humidity": "50 ± 10"},
                "authorised_by": "Alice",
                "executed_by": "Bob",
                "reproduction_conditions": "normal",
                "traceability_statement": "traceable",
                "starting_uncertainties": "see text",
            },
            "org": {
                "org_name": "Test Lab",
                "address_lines": ["Via Test 1"],
                "phone": "+39 000",
                "email": "test@example.com",
                "website": "https://example.com",
            },
            "measurements": meas,
            "sensor_model": {},
            "_u_budget_per_step": budget,
        }
        tree = build_dcc_tree(data)
        return tree.getroot()

    @pytest.fixture(scope="class")
    def dcc_list(self, xml_root):
        """Return the dcc:list element that holds all quantity columns."""
        ns_dcc = "https://ptb.de/dcc"
        meas_results = xml_root.find(f"{{{ns_dcc}}}measurementResults")
        meas_result  = meas_results.find(f"{{{ns_dcc}}}measurementResult")
        results      = meas_result.find(f"{{{ns_dcc}}}results")
        result       = results.find(f"{{{ns_dcc}}}result")
        data_elem    = result.find(f"{{{ns_dcc}}}data")
        return data_elem.find(f"{{{ns_dcc}}}list")

    @pytest.fixture(scope="class")
    def quantities(self, dcc_list):
        """Return all dcc:quantity elements inside the list."""
        ns_dcc = "https://ptb.de/dcc"
        return dcc_list.findall(f"{{{ns_dcc}}}quantity")

    def test_budget_quantities_present(self, quantities):
        """When u_budget_per_step is supplied there must be at least 4 + 4 = 8 quantities."""
        # 4 standard (ref_temp, measured, me_pre, me_post) + 4 budget = 8
        assert len(quantities) >= 8, (
            f"Expected ≥ 8 quantity elements, got {len(quantities)}"
        )

    def _find_quantity_by_reftype(self, quantities, ref_type: str):
        """Return the first quantity element matching refType, or None."""
        ns_dcc = "https://ptb.de/dcc"
        for q in quantities:
            if q.get("refType") == ref_type:
                return q
        return None

    def test_uA_ref_quantity_present(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_uncertaintyTypeA_reference")
        assert q is not None, "gp_uncertaintyTypeA_reference quantity missing"

    def test_uA_sensor_quantity_present(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_uncertaintyTypeA_sensor")
        assert q is not None, "gp_uncertaintyTypeA_sensor quantity missing"

    def test_combined_unc_quantity_present(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_combinedStandardUncertainty")
        assert q is not None, "gp_combinedStandardUncertainty quantity missing"

    def test_coverage_factor_quantity_present(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_coverageFactor")
        assert q is not None, "gp_coverageFactor quantity missing"

    def _read_value_list(self, quantity_elem) -> list:
        """Parse the si:valueXMLList text of a quantity into a list of floats."""
        ns_si  = "https://ptb.de/si"
        ns_dcc = "https://ptb.de/dcc"
        hybrid = quantity_elem.find(f"{{{ns_si}}}hybrid")
        assert hybrid is not None
        real_list = hybrid.find(f"{{{ns_si}}}realListXMLList")
        assert real_list is not None
        vals_text = real_list.find(f"{{{ns_si}}}valueXMLList").text or ""
        return [float(v) for v in vals_text.split()]

    def test_uA_ref_values_correct(self, quantities):
        """The emitted u_A_ref values must match the budget input."""
        q = self._find_quantity_by_reftype(quantities, "gp_uncertaintyTypeA_reference")
        vals = self._read_value_list(q)
        budget = self._make_budget(self.N_STEPS)
        expected = [b["uA_ref"] for b in budget]
        assert len(vals) == len(expected)
        for v, e in zip(vals, expected):
            assert v == pytest.approx(e, rel=1e-6)

    def test_uA_sensor_values_correct(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_uncertaintyTypeA_sensor")
        vals = self._read_value_list(q)
        budget = self._make_budget(self.N_STEPS)
        expected = [b["uA_sensor"] for b in budget]
        for v, e in zip(vals, expected):
            assert v == pytest.approx(e, rel=1e-6)

    def test_combined_unc_values_correct(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_combinedStandardUncertainty")
        vals = self._read_value_list(q)
        budget = self._make_budget(self.N_STEPS)
        expected = [b["u_c"] for b in budget]
        for v, e in zip(vals, expected):
            assert v == pytest.approx(e, rel=1e-6)

    def test_coverage_factor_values_are_two(self, quantities):
        q = self._find_quantity_by_reftype(quantities, "gp_coverageFactor")
        vals = self._read_value_list(q)
        for v in vals:
            assert v == pytest.approx(2.0, rel=1e-6)

    def test_budget_absent_when_no_budget_data(self):
        """Without '_u_budget_per_step' the four extra quantities must NOT appear."""
        import xml.etree.ElementTree as ET
        import sys
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        from generate_dcc_xml import build_dcc_tree

        meas = [
            [1.0, 25.0, 25.001, 0.01, 0.001, 0.35],
        ]
        data = {
            "cert": {
                "certificate_title": "Test",
                "certificate_number": "TEST-002",
                "issue_date": "2026-01-01",
                "customer": "Test",
                "request_date": "2026-01-01",
                "receipt_date": "2026-01-01",
                "measurement_dates": "2026-01-01",
                "item": "NTC",
                "device_type": "NTC",
                "manufacturer": "Acme",
                "model": "NTC-1",
                "serial_number": "SN-002",
                "asset_id": "A-002",
                "lab_reference": "LAB-002",
                "procedure_code": "PROC",
                "calibration_method": "comparison",
                "traceability": "PTB",
                "conditions": {},
                "environment": {"temperature": "23 ± 1.5", "relative_humidity": "50 ± 10"},
                "authorised_by": "Alice",
                "executed_by": "Bob",
                "reproduction_conditions": "normal",
                "traceability_statement": "traceable",
                "starting_uncertainties": "see text",
            },
            "org": {
                "org_name": "Lab",
                "address_lines": [],
                "phone": "+39 000",
                "email": "x@x.com",
                "website": "<website>",
            },
            "measurements": meas,
            "sensor_model": {},
            # No '_u_budget_per_step' key
        }
        root = build_dcc_tree(data).getroot()
        ns_dcc = "https://ptb.de/dcc"
        all_quantities = root.findall(f".//{{{ns_dcc}}}quantity")
        ref_types = {q.get("refType") for q in all_quantities}
        budget_reftypes = {
            "gp_uncertaintyTypeA_reference",
            "gp_uncertaintyTypeA_sensor",
            "gp_combinedStandardUncertainty",
            "gp_coverageFactor",
        }
        assert ref_types.isdisjoint(budget_reftypes), (
            f"Budget quantities should not appear when no budget data is provided. "
            f"Found: {ref_types & budget_reftypes}"
        )


# ===========================================================================
# 9. Check H u_std_mode: "combined" vs "type_a"
# ===========================================================================

class TestCheckHUStdMode:
    """
    Verify the u_std_mode parameter of check_H.

    "combined" must use U_exp / k as u_std.
    "type_a"   must use uA_sensor from the budget dict.
    Fallback: if "type_a" is requested but no budget supplied, "combined" is used.
    """

    @staticmethod
    def _row(punto: int, me_pre: float, u_exp: float, t_ref: float = 25.0) -> list:
        return [float(punto), t_ref, t_ref, me_pre, 0.0, u_exp]

    @staticmethod
    def _budget_entry(uA_i: float) -> dict:
        """Minimal budget dict with only the key check_H needs."""
        return {
            "t_nom": 25.0,
            "uA_ref": 0.002,
            "uA_sensor": uA_i,
            "u_ref": 0.033,
            "u_sensor": 0.173,
            "u_c": 0.176,
            "U_exp": 0.352,
            "k": 2.0,
        }

    @staticmethod
    def _run(rows, budget, mae_y, pfa_threshold_pct, u_std_mode):
        from checks_helper import check_H
        return check_H(
            rows,
            mae_y=mae_y,
            pfa_threshold_pct=pfa_threshold_pct,
            verbose=False,
            u_std_mode=u_std_mode,
            u_budget_per_step=budget,
        )

    # ── mode stored in result ──────────────────────────────────────────────

    def test_combined_mode_stored_in_result(self):
        rows = [self._row(1, 0.0, 0.10)]
        _, detail = self._run(rows, None, 0.10, 20.0, "combined")
        assert detail[0]["u_std_mode"] == "combined"

    def test_type_a_mode_stored_in_result(self):
        rows = [self._row(1, 0.0, 0.10)]
        budget = [self._budget_entry(uA_i=0.005)]
        _, detail = self._run(rows, budget, 0.10, 20.0, "type_a")
        assert detail[0]["u_std_mode"] == "type_a"

    # ── combined uses U_exp / k ────────────────────────────────────────────

    def test_combined_u_std_equals_u_exp_over_k(self):
        u_exp = 0.40
        rows = [self._row(1, 0.0, u_exp)]
        _, detail = self._run(rows, None, 0.10, 20.0, "combined")
        assert detail[0]["u_std_y"] == pytest.approx(u_exp / 2.0, rel=1e-9)

    def test_combined_pfa_matches_formula(self):
        from scipy.stats import norm
        mae = 0.10
        u_exp = 0.30
        u_std = u_exp / 2.0
        rows = [self._row(1, 0.0, u_exp)]
        _, detail = self._run(rows, None, mae, 20.0, "combined")
        expected = (
            1.0 - norm.cdf(mae, loc=0.0, scale=u_std)
            + norm.cdf(-mae, loc=0.0, scale=u_std)
        ) * 100.0
        assert detail[0]["PFA_pct"] == pytest.approx(expected, abs=0.01)

    # ── type_a uses uA_sensor ──────────────────────────────────────────────

    def test_type_a_u_std_equals_uA_i(self):
        uA_i = 0.004
        u_exp = 0.40   # much larger than uA_i — distinguishable
        rows = [self._row(1, 0.0, u_exp)]
        budget = [self._budget_entry(uA_i=uA_i)]
        _, detail = self._run(rows, budget, 0.10, 20.0, "type_a")
        assert detail[0]["u_std_y"] == pytest.approx(uA_i, rel=1e-9)

    def test_type_a_pfa_matches_formula(self):
        from scipy.stats import norm
        mae = 0.10
        uA_i = 0.004
        rows = [self._row(1, 0.0, 0.40)]
        budget = [self._budget_entry(uA_i=uA_i)]
        _, detail = self._run(rows, budget, mae, 20.0, "type_a")
        expected = (
            1.0 - norm.cdf(mae, loc=0.0, scale=uA_i)
            + norm.cdf(-mae, loc=0.0, scale=uA_i)
        ) * 100.0
        assert detail[0]["PFA_pct"] == pytest.approx(expected, abs=0.01)

    def test_type_a_gives_lower_pfa_than_combined_when_uA_i_smaller(self):
        """With small uA_i and large U_exp, type_a PFA << combined PFA."""
        rows = [self._row(1, 0.0, 0.40)]
        budget = [self._budget_entry(uA_i=0.003)]
        _, detail_comb = self._run(rows, None, 0.10, 20.0, "combined")
        _, detail_ta   = self._run(rows, budget, 0.10, 20.0, "type_a")
        assert detail_ta[0]["PFA_pct"] < detail_comb[0]["PFA_pct"]

    # ── fallback behaviour ─────────────────────────────────────────────────

    def test_type_a_falls_back_when_no_budget(self):
        """type_a with no budget → falls back to combined → u_std_mode='combined'."""
        u_exp = 0.30
        rows = [self._row(1, 0.0, u_exp)]
        _, detail = self._run(rows, None, 0.10, 20.0, "type_a")
        assert detail[0]["u_std_mode"] == "combined"
        assert detail[0]["u_std_y"] == pytest.approx(u_exp / 2.0, rel=1e-9)

    def test_type_a_falls_back_when_budget_length_mismatch(self):
        """Budget with wrong length → falls back to combined."""
        rows = [self._row(1, 0.0, 0.30), self._row(2, 0.01, 0.30)]
        budget = [self._budget_entry(uA_i=0.003)]   # only 1 entry for 2 rows
        _, detail = self._run(rows, budget, 0.10, 20.0, "type_a")
        assert detail[0]["u_std_mode"] == "combined"

    # ── invalid mode raises ────────────────────────────────────────────────

    def test_invalid_mode_raises(self):
        rows = [self._row(1, 0.0, 0.10)]
        with pytest.raises(ValueError, match="u_std_mode"):
            self._run(rows, None, 0.10, 20.0, "invalid_mode")

    # ── multi-point: each row uses its own budget entry ────────────────────

    def test_type_a_multi_point_uses_per_row_budget(self):
        """Each point's u_std must come from its own budget entry."""
        uA_values = [0.003, 0.007, 0.005]
        rows = [self._row(i + 1, 0.0, 0.40) for i in range(3)]
        budget = [self._budget_entry(uA_i=v) for v in uA_values]
        _, detail = self._run(rows, budget, 0.10, 20.0, "type_a")
        for i, (entry, expected_uA) in enumerate(zip(detail, uA_values)):
            assert entry["u_std_y"] == pytest.approx(expected_uA, rel=1e-9), (
                f"Point {i+1}: expected u_std={expected_uA}, got {entry['u_std_y']}"
            )


# ===========================================================================
# Standalone runner
# ===========================================================================
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
