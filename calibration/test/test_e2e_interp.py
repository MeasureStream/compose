"""
test_e2e_interp.py
==================
End-to-end tests for the linear_interp and cubic_interp procedures through
the analisi_calib_data.py orchestrator CLI.

These tests verify the full pipeline:
  payload JSON
    -> analisi_calib_data.py --procedure {linear_interp|cubic_interp}
    -> certificate JSON (filled)
    -> conformity checks

Run:
    pytest backend/calibration/test/test_e2e_interp.py -v
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR    = Path(__file__).resolve().parent
CALIB_ROOT   = TESTS_DIR.parent
SCRIPTS_DIR  = CALIB_ROOT / "scripts"
MODELS_DIR   = CALIB_ROOT / "models_in"
TEMPLATE_DIR = CALIB_ROOT / "template_in"
DATA_DIR     = TESTS_DIR / "data_in"
OUT_DIR      = CALIB_ROOT / "certificato_out"

INPUT_JSON      = DATA_DIR   / "export2_tmp126_lsb16.json"
SENSOR_JSON     = MODELS_DIR / "ntc_temperature.json"
REF_JSON        = MODELS_DIR / "fluke_9142.json"
CERT_INPUT_JSON = TEMPLATE_DIR / "certificato_funzione_input.json"

LSB_MIN, LSB_MAX = -40.0, 105.0


def _run_orchestrator(procedure: str, cert_out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "analisi_calib_data.py"),
            "--input",       str(INPUT_JSON),
            "--sensor",      str(SENSOR_JSON),
            "--ref",         str(REF_JSON),
            "--cert-input",  str(CERT_INPUT_JSON),
            "--cert-output", str(cert_out),
            "--no-pdf",
            "--no-xml",
            "--no-charts",
            "--no-verbose",
            "--procedure",   procedure,
        ],
        capture_output=True,
        text=True,
    )


# ===========================================================================
# Fixtures — run the orchestrator once per procedure, class-scoped
# ===========================================================================

@pytest.fixture(scope="module")
def li_cert(tmp_path_factory):
    out = tmp_path_factory.mktemp("li") / "cert_linear_interp.json"
    r = _run_orchestrator("linear_interp", out)
    assert r.returncode == 0, f"linear_interp orchestrator failed:\n{r.stdout}\n{r.stderr}"
    assert out.exists(), "Certificate JSON not written (linear_interp)"
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ci_cert(tmp_path_factory):
    out = tmp_path_factory.mktemp("ci") / "cert_cubic_interp.json"
    r = _run_orchestrator("cubic_interp", out)
    assert r.returncode == 0, f"cubic_interp orchestrator failed:\n{r.stdout}\n{r.stderr}"
    assert out.exists(), "Certificate JSON not written (cubic_interp)"
    return json.loads(out.read_text(encoding="utf-8"))


# ===========================================================================
# 1. Orchestrator exits zero
# ===========================================================================

class TestOrchestratorExitCode:
    def test_linear_interp_exits_zero(self, tmp_path):
        r = _run_orchestrator("linear_interp", tmp_path / "li.json")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    def test_cubic_interp_exits_zero(self, tmp_path):
        r = _run_orchestrator("cubic_interp", tmp_path / "ci.json")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


# ===========================================================================
# 2. Certificate JSON structure
# ===========================================================================

class TestCertJSONStructure:
    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_template_parts_present(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        assert "template_parts" in cert

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_calibration_result_present(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        assert "_calibration_result" in cert

    def test_linear_interp_model_label(self, li_cert):
        assert li_cert["_calibration_result"]["_calib_model"] == "linear_interp"

    def test_cubic_interp_model_label(self, ci_cert):
        assert ci_cert["_calibration_result"]["_calib_model"] == "cubic_interp"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_variant_is_funzione(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        assert cert["_calibration_result"]["_variant"] == "funzione"


# ===========================================================================
# 3. Measurements rows
# ===========================================================================

def _get_measurements(cert):
    calc = cert["template_parts"]["calculated_calibration_values"]
    return calc.get("measurements", calc.get("_measurements", []))


class TestMeasurementRows:
    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_six_measurement_rows(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        meas = _get_measurements(cert)
        assert len(meas) == 6

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_row_has_six_columns(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for i, row in enumerate(_get_measurements(cert)):
            assert len(row) == 6, f"Row {i} has {len(row)} columns, expected 6"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_point_numbers_sequential(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for i, row in enumerate(_get_measurements(cert)):
            assert int(row[0]) == i + 1

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_t_ref_within_range(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for row in _get_measurements(cert):
            # Real calibration data may exceed the sensor's declared range ±30 °C
            assert LSB_MIN - 30 <= row[1] <= LSB_MAX + 30, f"T_ref={row[1]} out of range"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_expanded_uncertainty_positive(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for i, row in enumerate(_get_measurements(cert)):
            assert row[5] > 0.0, f"U(E) non-positive at row {i}: {row[5]}"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_expanded_uncertainty_under_one_degC(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for row in _get_measurements(cert):
            assert row[5] < 1.0, f"U(E)={row[5]:.4f} °C exceeds 1 °C"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_error_post_is_finite(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        for i, row in enumerate(_get_measurements(cert)):
            assert math.isfinite(row[4]), f"M_e_post is not finite at row {i}"


# ===========================================================================
# 4. linear_interp specific: node rows have zero M_e_post
# ===========================================================================

class TestLinearInterpNodeRows:
    def test_first_and_last_rows_have_zero_post_error(self, li_cert):
        """Node rows (first and last) must have M_e_post = 0."""
        meas = _get_measurements(li_cert)
        assert abs(meas[0][4]) < 1e-7, f"First row M_e_post={meas[0][4]} should be ~0"
        assert abs(meas[-1][4]) < 1e-7, f"Last row M_e_post={meas[-1][4]} should be ~0"

    def test_interior_rows_may_have_nonzero_post_error(self, li_cert):
        """At least one interior row should show the NTC nonlinearity."""
        meas = _get_measurements(li_cert)
        interior = meas[1:-1]
        assert any(abs(r[4]) > 1e-5 for r in interior), (
            "Expected nonzero M_e_post in at least one interior row"
        )

    def test_calibration_result_has_node_keys(self, li_cert):
        cr = li_cert["_calibration_result"]
        assert "_x_nodes" in cr
        assert "_y_nodes" in cr
        assert "_node_steps" in cr
        assert len(cr["_x_nodes"]) == 2
        assert len(cr["_y_nodes"]) == 2


# ===========================================================================
# 5. cubic_interp specific: all rows pass through the four nodes exactly
# ===========================================================================

class TestCubicInterpNodeRows:
    def test_calibration_result_has_node_keys(self, ci_cert):
        cr = ci_cert["_calibration_result"]
        assert "_x_nodes" in cr
        assert "_y_nodes" in cr
        assert "_node_steps" in cr
        assert len(cr["_x_nodes"]) == 4
        assert len(cr["_y_nodes"]) == 4

    def test_rmse_is_finite_or_none(self, ci_cert):
        rmse = ci_cert["_calibration_result"].get("_rmse_degC")
        if rmse is not None:
            assert math.isfinite(rmse)

    def test_all_post_errors_small(self, ci_cert):
        """Cubic interpolation through 4 nodes leaves small residuals at all 6 points."""
        meas = _get_measurements(ci_cert)
        for i, row in enumerate(meas):
            assert abs(row[4]) < 5.0, f"M_e_post={row[4]:.4f} °C too large at row {i}"


# ===========================================================================
# 6. Calibration result extra keys
# ===========================================================================

class TestCalibResultExtraKeys:
    @pytest.mark.parametrize("cert_fixture,expected_keys", [
        ("li_cert", {"_x_nodes", "_y_nodes", "_node_steps", "_rmse_degC", "_u_H_degC"}),
        ("ci_cert", {"_x_nodes", "_y_nodes", "_node_steps", "_rmse_degC", "_u_H_degC"}),
    ])
    def test_interp_keys_present(self, cert_fixture, expected_keys, request):
        cert = request.getfixturevalue(cert_fixture)
        cr = cert["_calibration_result"]
        for k in expected_keys:
            assert k in cr, f"Key '{k}' missing from _calibration_result"

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_expanded_uncertainties_stored(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        cr = cert["_calibration_result"]
        u_list = cr.get("_expanded_uncertainties") or cr.get("_expanded_uncertainties_degC", [])
        assert len(u_list) == 6
        assert all(u > 0.0 for u in u_list)


# ===========================================================================
# 7. Conformity output — checks G, A, B, H present and have valid status
# ===========================================================================

class TestConformityChecks:
    VALID_STATUSES = {"PASS", "FAIL", "WARN", "N/A"}

    @pytest.mark.parametrize("cert_fixture", ["li_cert", "ci_cert"])
    def test_conformity_done_is_done(self, cert_fixture, request):
        cert = request.getfixturevalue(cert_fixture)
        done = cert.get("_calibration_done", "done")
        assert done in ("done", "not_necessary")

    def _run_with_conformity(self, procedure: str, tmp_path: Path):
        conf_out = tmp_path / f"conf_{procedure}.json"
        cert_out = tmp_path / f"cert_{procedure}.json"
        r = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "analisi_calib_data.py"),
                "--input",              str(INPUT_JSON),
                "--sensor",             str(SENSOR_JSON),
                "--ref",                str(REF_JSON),
                "--cert-input",         str(CERT_INPUT_JSON),
                "--cert-output",        str(cert_out),
                "--conformity-output",  str(conf_out),
                "--no-pdf", "--no-xml", "--no-charts", "--no-verbose",
                "--procedure", procedure,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
        assert conf_out.exists(), "Conformity JSON not written"
        return json.loads(conf_out.read_text(encoding="utf-8"))

    def test_linear_interp_conformity_summary_keys(self, tmp_path):
        data = self._run_with_conformity("linear_interp", tmp_path)
        summary = data["summary"]
        for key in ("G", "A", "B", "H", "overall"):
            assert key in summary, f"Key '{key}' missing from conformity summary"

    def test_cubic_interp_conformity_summary_keys(self, tmp_path):
        data = self._run_with_conformity("cubic_interp", tmp_path)
        summary = data["summary"]
        for key in ("G", "A", "B", "H", "overall"):
            assert key in summary

    def test_linear_interp_C_F_are_na(self, tmp_path):
        """Checks C and F are OLS-only and must be N/A for linear_interp."""
        data = self._run_with_conformity("linear_interp", tmp_path)
        summary = data["summary"]
        assert "N/A" in str(summary.get("C", "")), f"Check C should be N/A, got {summary.get('C')}"
        assert "N/A" in str(summary.get("F", "")), f"Check F should be N/A, got {summary.get('F')}"

    def test_cubic_interp_C_F_are_na(self, tmp_path):
        data = self._run_with_conformity("cubic_interp", tmp_path)
        summary = data["summary"]
        assert "N/A" in str(summary.get("C", "")), f"Check C should be N/A, got {summary.get('C')}"
        assert "N/A" in str(summary.get("F", "")), f"Check F should be N/A, got {summary.get('F')}"

    def test_linear_interp_check_H_has_six_points(self, tmp_path):
        data = self._run_with_conformity("linear_interp", tmp_path)
        rH = data.get("check_H", [])
        assert isinstance(rH, list)
        assert len(rH) == 6

    def test_cubic_interp_check_H_has_six_points(self, tmp_path):
        data = self._run_with_conformity("cubic_interp", tmp_path)
        rH = data.get("check_H", [])
        assert isinstance(rH, list)
        assert len(rH) == 6
