"""
test_features.py
================
Unit tests for cubic/cube-log calibration engines, conformity checks A-G,
and verify_dcc_conformity. Covers features not yet tested in
test_calibration_pipeline.py.

Run:
    pytest backend/calibration/test/test_features.py -v
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

TESTS_DIR   = Path(__file__).resolve().parent
CALIB_ROOT  = TESTS_DIR.parent
SCRIPTS_DIR = CALIB_ROOT / "scripts"
MODELS_DIR  = CALIB_ROOT / "models_in"

for p in (str(SCRIPTS_DIR), str(MODELS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

INPUT_JSON  = TESTS_DIR / "data_in" / "export2_tmp126_lsb16.json"
SENSOR_JSON = MODELS_DIR / "ntc_temperature.json"
REF_JSON    = MODELS_DIR / "fluke_9142.json"

ADC_BITS  = 16
ADC_MAX   = float((1 << ADC_BITS) - 1)
LSB_MIN   = -40.0
LSB_MAX   = 105.0
LSB_PER_C = ADC_MAX / (LSB_MAX - LSB_MIN)

SAMPLE_SIZE = 20

LSB_SCALE = {"minPhysVal": LSB_MIN, "maxPhysVal": LSB_MAX}


def _load_payload():
    return json.loads(INPUT_JSON.read_text(encoding="utf-8"))


def _default_uncertainties():
    """Return (ub_pt_degc [°C], ub_tmp_lsb [LSB]) — correct physical domains."""
    from VAR_REF_SENSOR import VAR_extra, SENSOR_model
    extra  = VAR_extra()
    sensor = SENSOR_model()
    ub_pt_degc = extra._U_pt_c / extra._k_pt   # [°C] standard uncertainty of reference
    ub_tmp_lsb = sensor.uB                       # [LSB] standard uncertainty of NTC ADC
    return ub_pt_degc, ub_tmp_lsb


# ===========================================================================
# Cubic calibration
# ===========================================================================

class TestCubicCalibration:

    @pytest.fixture(scope="class")
    def result(self):
        from model_calibration.cubic_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        return calibrate(
            payload=_load_payload(),
            lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_pt_degc=ub_pt_degc,
            ub_tmp_lsb=ub_tmp_lsb,
            verbose=False,
        )

    def test_model_label(self, result):
        assert result["model"] == "cubic"

    def test_has_four_coefficients(self, result):
        for k in ("a0", "a1", "a2", "a3"):
            assert k in result

    def test_has_four_uncertainties(self, result):
        for k in ("u_a0", "u_a1", "u_a2", "u_a3"):
            assert k in result
            assert result[k] >= 0.0

    def test_has_covariance_matrix(self, result):
        assert "cov_theta" in result
        cov = np.array(result["cov_theta"])
        assert cov.shape == (4, 4)

    def test_six_expanded_uncertainties(self, result):
        assert len(result["expanded_uncertainties"]) == 6
        for u in result["expanded_uncertainties"]:
            assert u > 0.0
            assert u < 1.0

    def test_theta_matches_individual_coefficients(self, result):
        theta = result["theta"]
        assert theta[0] == pytest.approx(result["a0"])
        assert theta[1] == pytest.approx(result["a1"])
        assert theta[2] == pytest.approx(result["a2"])
        assert theta[3] == pytest.approx(result["a3"])

    def test_fit_residuals_small(self, result):
        from model_calibration.cubic_calibration import cubic_predict_degc
        theta = np.array(result["theta"])
        for i, t in enumerate(result["temp_nominali"]):
            pmean_log = result["risultati_elaborati"][t]["pmean_log"]
            t_cal = cubic_predict_degc(float(pmean_log), theta, LSB_SCALE, ADC_MAX)
            t_ref = result["ref_temp_means"][i]
            # polynomial fit residual in degC domain
            assert abs(t_cal - t_ref) < 5.0, f"large residual at step {t}: {t_cal - t_ref}"

    def test_cubic_predict_returns_float(self):
        from model_calibration.cubic_calibration import cubic_predict
        theta = np.array([1.0, 0.5, 0.0, 0.0])
        result = cubic_predict(100.0, theta)
        assert isinstance(result, float)
        assert result == pytest.approx(1.0 + 0.5 * 100.0)

    def test_cubic_uncertainty_non_negative(self, result):
        from model_calibration.cubic_calibration import cubic_uncertainty
        theta = np.array(result["theta"])
        cov   = np.array(result["cov_theta"])
        x_mid = np.mean([result["risultati_elaborati"][t]["pmean_log"]
                         for t in result["temp_nominali"]])
        u = cubic_uncertainty(float(x_mid), 10.0, theta, cov, LSB_PER_C)
        assert u >= 0.0


# ===========================================================================
# Cube-log (Steinhart-Hart) calibration
# ===========================================================================

class TestCubeLogCalibration:

    @pytest.fixture(scope="class")
    def result(self):
        from model_calibration.cube_log_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        return calibrate(
            payload=_load_payload(),
            lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_pt_degc=ub_pt_degc,
            ub_tmp_lsb=ub_tmp_lsb,
            verbose=False,
        )

    def test_model_label(self, result):
        assert result["model"] == "cube-log"

    def test_has_steinhart_coefficients(self, result):
        for k in ("C0", "C1", "C3"):
            assert k in result

    def test_has_coefficient_uncertainties(self, result):
        for k in ("u_C0", "u_C1", "u_C3"):
            assert k in result
            assert result[k] >= 0.0

    def test_covariance_matrix_shape(self, result):
        cov = np.array(result["cov_theta"])
        assert cov.shape == (3, 3)

    def test_six_expanded_uncertainties(self, result):
        assert len(result["expanded_uncertainties"]) == 6
        for u in result["expanded_uncertainties"]:
            assert u > 0.0
            assert u < 1.0

    def test_theta_contains_three_values(self, result):
        assert len(result["theta"]) == 3

    def test_steinhart_hart_predict_in_range(self, result):
        from model_calibration.cube_log_calibration import steinhart_hart_predict_degc
        theta = np.array(result["theta"])
        for t in result["temp_nominali"]:
            x = result["risultati_elaborati"][t]["pmean_log"]
            t_cal = steinhart_hart_predict_degc(float(x), theta)
            # generous tolerance since Steinhart-Hart fit may extrapolate a bit
            assert LSB_MIN - 20 <= t_cal <= LSB_MAX + 20

    def test_predict_raises_on_non_positive_lsb(self):
        from model_calibration.cube_log_calibration import steinhart_hart_predict
        theta = np.array([1e-3, 2e-4, 1e-7])
        with pytest.raises(ValueError):
            steinhart_hart_predict(0.0, theta)

    def test_steinhart_hart_uncertainty_non_negative(self, result):
        from model_calibration.cube_log_calibration import steinhart_hart_uncertainty
        theta = np.array(result["theta"])
        cov   = np.array(result["cov_theta"])
        x_mid = np.mean([result["risultati_elaborati"][t]["pmean_log"]
                         for t in result["temp_nominali"]])
        u = steinhart_hart_uncertainty(float(x_mid), 10.0, theta, cov)
        assert u >= 0.0


# ===========================================================================
# Conformity checks A, B, C, D, E, F, G
# ===========================================================================

def _make_row(punto, t_ref, t_sensor, me_pre, me_post, u_exp):
    return [float(punto), float(t_ref), float(t_sensor), float(me_pre), float(me_post), float(u_exp)]


class TestCheckA:

    def test_pass_when_residual_within_uncertainty(self):
        from verifica_conformita import check_A
        rows = [_make_row(1, 25.0, 25.05, 0.0, 0.05, 0.10)]
        status, detail = check_A(rows, verbose=False)
        assert status == "PASS"
        assert detail[0]["pass"] is True

    def test_fail_when_residual_exceeds_uncertainty(self):
        from verifica_conformita import check_A
        rows = [_make_row(1, 25.0, 25.15, 0.0, 0.15, 0.10)]
        status, detail = check_A(rows, verbose=False)
        assert status == "FAIL"
        assert detail[0]["pass"] is False

    def test_uses_me_post_column(self):
        from verifica_conformita import check_A
        rows = [_make_row(1, 25.0, 25.0, 999.0, 0.05, 0.10)]
        status, _ = check_A(rows, verbose=False)
        assert status == "PASS"

    def test_multi_point_all_pass(self):
        from verifica_conformita import check_A
        rows = [_make_row(i, 25.0 * i, 25.0 * i + 0.01, 0.0, 0.01, 0.10) for i in range(1, 5)]
        status, detail = check_A(rows, verbose=False)
        assert status == "PASS"
        assert all(r["pass"] for r in detail)

    def test_multi_point_one_fail(self):
        from verifica_conformita import check_A
        rows = [
            _make_row(1, 25.0, 25.05, 0.0, 0.05, 0.10),
            _make_row(2, 50.0, 50.20, 0.0, 0.20, 0.10),
        ]
        status, detail = check_A(rows, verbose=False)
        assert status == "FAIL"
        assert detail[0]["pass"] is True
        assert detail[1]["pass"] is False


class TestCheckB:

    def test_pass_when_uncertainty_within_limit(self):
        from verifica_conformita import check_B
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.08)]
        status, detail = check_B(rows, limit_degc=0.10, verbose=False)
        assert status == "PASS"
        assert detail[0]["pass"] is True

    def test_fail_when_uncertainty_exceeds_limit(self):
        from verifica_conformita import check_B
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.15)]
        status, detail = check_B(rows, limit_degc=0.10, verbose=False)
        assert status == "FAIL"
        assert detail[0]["pass"] is False

    def test_excess_stored_correctly(self):
        from verifica_conformita import check_B
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.12)]
        _, detail = check_B(rows, limit_degc=0.10, verbose=False)
        assert detail[0]["excess"] == pytest.approx(0.02, abs=1e-9)


class TestCheckD:

    def test_pass_on_realistic_values(self):
        from verifica_conformita import check_D
        rows = [_make_row(i, 25.0 * i, 25.0 * i, 0.0, 0.0, 0.35) for i in range(1, 5)]
        u_exp_list = [0.35] * 4
        # check_D passes when u_B components alone don't exceed u_std (u_A2_est >= 0)
        _, detail = check_D(rows, u_exp_list, LSB_MIN, LSB_MAX, 0.1, verbose=False)
        for r in detail:
            assert r["u_A_est"] >= 0.0

    def test_result_has_all_budget_keys(self):
        from verifica_conformita import check_D
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.35)]
        _, detail = check_D(rows, [0.35], LSB_MIN, LSB_MAX, 0.1, verbose=False)
        for key in ("u_B_ref", "u_B_sensor", "u_res", "u_A_est", "u_ricostruita", "pass"):
            assert key in detail[0]

    def test_u_components_are_positive(self):
        from verifica_conformita import check_D
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.35)]
        _, detail = check_D(rows, [0.35], LSB_MIN, LSB_MAX, 0.1, verbose=False)
        assert detail[0]["u_B_ref"] > 0.0
        assert detail[0]["u_B_sensor"] > 0.0
        assert detail[0]["u_res"] > 0.0


class TestCheckE:

    def test_pass_when_k2_in_notes(self):
        from verifica_conformita import check_E
        notes = ["Coverage factor k = 2, confidence level about 95 %."]
        status, result = check_E(notes, [0.35], verbose=False)
        assert status == "PASS"
        assert result["k_declared_in_notes"] is True

    def test_fail_when_k_not_declared(self):
        from verifica_conformita import check_E
        notes = ["Some note without coverage factor declaration."]
        status, result = check_E(notes, [0.35], verbose=False)
        assert result["k_declared_in_notes"] is False

    def test_plausibility_range(self):
        from verifica_conformita import check_E
        notes = ["k = 2 coverage"]
        _, result = check_E(notes, [0.35], verbose=False)
        assert result["plausible"] is True

    def test_implausible_uncertainty(self):
        from verifica_conformita import check_E
        notes = ["k = 2 coverage"]
        _, result = check_E(notes, [100.0], verbose=False)
        assert result["plausible"] is False


class TestCheckC:

    def test_pass_when_post_cal_residuals_are_near_zero(self):
        from verifica_conformita import check_C
        rows = [
            _make_row(i, 25.0 * i, 25.0 * i + 1e-12, 0.0, 1e-12, 0.35)
            for i in range(1, 4)
        ]
        status, result = check_C(rows, A_cert=1.0, B_cert=-1000.0,
                                  min_phys=LSB_MIN, max_phys=LSB_MAX, verbose=False)
        assert status == "PASS"

    def test_result_has_note(self):
        from verifica_conformita import check_C
        rows = [_make_row(1, 25.0, 25.0, 0.0, 0.0, 0.35)]
        _, result = check_C(rows, A_cert=1.0, B_cert=0.0,
                             min_phys=LSB_MIN, max_phys=LSB_MAX, verbose=False)
        assert "note" in result


class TestCheckF:

    def test_pass_when_me_post_consistent_with_formula(self):
        from verifica_conformita import check_F
        t_ref = 25.0
        t_sensor = 25.0 + 1e-12
        me_post = t_sensor - t_ref
        rows = [_make_row(1, t_ref, t_sensor, 0.0, me_post, 0.35)]
        status, detail = check_F(rows, A=2.0, B=-30000.0,
                                  min_phys=LSB_MIN, max_phys=LSB_MAX,
                                  variant="funzione", verbose=False)
        assert status == "PASS"

    def test_fail_when_me_post_inconsistent(self):
        from verifica_conformita import check_F
        rows = [_make_row(1, 25.0, 25.5, 0.0, 0.999, 0.35)]
        status, detail = check_F(rows, A=2.0, B=-30000.0,
                                  min_phys=LSB_MIN, max_phys=LSB_MAX,
                                  variant="funzione", verbose=False)
        assert status == "FAIL"

    def test_delta_me_stored(self):
        from verifica_conformita import check_F
        t_ref, t_sensor = 25.0, 25.1
        me_post = t_sensor - t_ref
        rows = [_make_row(1, t_ref, t_sensor, 0.0, me_post, 0.35)]
        _, detail = check_F(rows, A=2.0, B=-30000.0,
                             min_phys=LSB_MIN, max_phys=LSB_MAX,
                             variant="funzione", verbose=False)
        assert detail[0]["delta_me"] == pytest.approx(0.0, abs=1e-9)


class TestCheckG:

    def _accuracy_ranges(self):
        return [{"tempMin": -40.0, "tempMax": 125.0, "maxError": 0.5}]

    def test_pass_when_as_found_within_limit(self):
        from verifica_conformita import check_G
        rows = [_make_row(1, 25.0, 25.0, 0.1, 0.0, 0.35)]
        status, result = check_G(rows, self._accuracy_ranges(), "linear", verbose=False)
        assert status == "PASS"

    def test_fail_when_as_found_exceeds_limit(self):
        from verifica_conformita import check_G
        rows = [_make_row(1, 25.0, 25.0, 0.8, 0.0, 0.35)]
        status, result = check_G(rows, self._accuracy_ranges(), "linear", verbose=False)
        assert status == "FAIL"

    def test_na_when_no_accuracy_ranges(self):
        from verifica_conformita import check_G
        rows = [_make_row(1, 25.0, 25.0, 0.1, 0.0, 0.35)]
        status, result = check_G(rows, [], "linear", verbose=False)
        assert status == "N/A"

    def test_warn_when_point_outside_coverage(self):
        from verifica_conformita import check_G
        # Only covers 0-50, point at 80 is outside
        accuracy_ranges = [{"tempMin": 0.0, "tempMax": 50.0, "maxError": 0.5}]
        rows = [_make_row(1, 80.0, 80.0, 0.1, 0.0, 0.35)]
        status, result = check_G(rows, accuracy_ranges, "cubic", verbose=False)
        assert status == "WARN"
        assert result["G2_all_covered"] is False

    def test_uses_me_pre_column(self):
        from verifica_conformita import check_G
        # me_pre far exceeds limit, me_post within limit
        rows = [_make_row(1, 25.0, 25.0, 0.9, 0.01, 0.35)]
        status, _ = check_G(rows, self._accuracy_ranges(), "linear", verbose=False)
        assert status == "FAIL"


# ===========================================================================
# Full calibration output (linear) — numeric regression test
# ===========================================================================

class TestLinearCalibrationOutput:
    """Checks that key numeric outputs don't drift unexpectedly."""

    @pytest.fixture(scope="class")
    def result(self):
        from model_calibration.linear_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        return calibrate(
            payload=_load_payload(),
            lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_pt_degc=ub_pt_degc,
            ub_tmp_lsb=ub_tmp_lsb,
            verbose=False,
        )

    def test_a_coefficient_in_degc_per_lsb_range(self, result):
        # A is now in [°C/LSB]. Nominal value ≈ (105-(-40))/65535 ≈ 0.00221 °C/LSB.
        # Allow ±50 % for a typical NTC calibration.
        nominal_a = (LSB_MAX - LSB_MIN) / ADC_MAX
        assert nominal_a * 0.5 < result["A"] < nominal_a * 1.5

    def test_lsb_per_c_computed_correctly(self, result):
        expected = ADC_MAX / (LSB_MAX - LSB_MIN)
        assert result["lsb_per_c"] == pytest.approx(expected, rel=1e-9)

    def test_all_ref_temps_within_plausible_range(self, result):
        # ref_temp_means now in native °C; calibration points go up to 125°C nominal
        for t in result["ref_temp_means"]:
            assert LSB_MIN - 5 <= t <= 135.0, f"ref_temp {t} implausible"

    def test_ub_pt_degc_and_ub_tmp_lsb_echoed(self, result):
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        assert result["ub_pt_degc"] == pytest.approx(ub_pt_degc, rel=1e-9)
        assert result["ub_tmp_lsb"] == pytest.approx(ub_tmp_lsb, rel=1e-9)

    def test_all_step_stats_present(self, result):
        for t in result["temp_nominali"]:
            r = result["risultati_elaborati"][t]
            for key in ("pmean_rtd", "pmean_log", "pstd_rtd", "pstd_log"):
                assert key in r


# ===========================================================================
# verify_dcc_conformity standalone checks
# ===========================================================================

class TestVerifyDccConformity:

    def _make_xml(self, t_ref, t_sensor, me_pre, me_post, u_exp, tmp_dir):
        """Build a minimal DCC XML and write to tmp_dir, return path."""
        ns_dcc = "https://ptb.de/dcc"
        ns_si  = "https://ptb.de/si"
        n = len(t_ref)

        def space(vals):
            return " ".join(str(v) for v in vals)

        xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<dcc:digitalCalibrationCertificate
    xmlns:dcc="{ns_dcc}"
    xmlns:si="{ns_si}"
    schemaVersion="3.3.0">
  <dcc:measurementResults>
    <dcc:measurementResult>
      <dcc:results>
        <dcc:result>
          <dcc:data>
            <dcc:list>
              <dcc:quantity refType="basic_referenceValue">
                <si:hybrid><si:realListXMLList>
                  <si:valueXMLList>{space(t_ref)}</si:valueXMLList>
                </si:realListXMLList></si:hybrid>
              </dcc:quantity>
              <dcc:quantity refType="basic_measuredValue">
                <si:hybrid><si:realListXMLList>
                  <si:valueXMLList>{space(t_sensor)}</si:valueXMLList>
                </si:realListXMLList></si:hybrid>
              </dcc:quantity>
              <dcc:quantity refType="gp_measurementErrorPreCalibration">
                <si:hybrid><si:realListXMLList>
                  <si:valueXMLList>{space(me_pre)}</si:valueXMLList>
                </si:realListXMLList></si:hybrid>
              </dcc:quantity>
              <dcc:quantity refType="basic_measurementError">
                <si:hybrid><si:realListXMLList>
                  <si:valueXMLList>{space(me_post)}</si:valueXMLList>
                  <si:uncertaintyXMLList>{space(u_exp)}</si:uncertaintyXMLList>
                </si:realListXMLList></si:hybrid>
              </dcc:quantity>
            </dcc:list>
          </dcc:data>
        </dcc:result>
      </dcc:results>
    </dcc:measurementResult>
  </dcc:measurementResults>
</dcc:digitalCalibrationCertificate>
"""
        path = tmp_dir / "test_dcc.xml"
        path.write_text(xml, encoding="utf-8")
        return path

    def test_parse_dcc_xml_returns_correct_lengths(self, tmp_path):
        import xml.etree.ElementTree as ET
        sys.modules.pop("verify_dcc_conformity", None)
        import verify_dcc_conformity as vdc
        vdc.ET = ET

        path = self._make_xml(
            t_ref=[10.0, 25.0, 50.0],
            t_sensor=[10.05, 25.03, 50.02],
            me_pre=[0.05, 0.03, 0.02],
            me_post=[0.001, 0.001, 0.001],
            u_exp=[0.35, 0.35, 0.35],
            tmp_dir=tmp_path,
        )
        t_ref, t_sensor, me_pre, me_post, u_exp = vdc.parse_dcc_xml(path)
        assert len(t_ref) == 3
        assert len(t_sensor) == 3
        assert len(me_pre) == 3
        assert len(me_post) == 3
        assert len(u_exp) == 3

    def test_parse_dcc_xml_values_correct(self, tmp_path):
        import xml.etree.ElementTree as ET
        import verify_dcc_conformity as vdc
        vdc.ET = ET

        path = self._make_xml(
            t_ref=[10.0, 25.0],
            t_sensor=[10.05, 25.03],
            me_pre=[0.05, 0.03],
            me_post=[0.001, 0.001],
            u_exp=[0.35, 0.35],
            tmp_dir=tmp_path,
        )
        t_ref, t_sensor, me_pre, _, u_exp = vdc.parse_dcc_xml(path)
        assert t_ref[0] == pytest.approx(10.0)
        assert t_sensor[1] == pytest.approx(25.03)
        assert me_pre[0] == pytest.approx(0.05)
        assert u_exp[0] == pytest.approx(0.35)

    def test_check_h_in_run_checks(self):
        import verify_dcc_conformity as vdc
        results = vdc.run_checks(
            t_ref=[25.0],
            t_sensor=[25.05],
            me_pre=[0.05],
            u_sensor=[0.35],
            accuracy_ranges=[],
            mae=0.30,
            pfa_threshold_pct=20.0,
            u_ref=0.065,
        )
        assert "check_h" in results
        assert results["check_h"]["status"] in ("PASS", "FAIL")

    def test_check_g_na_without_accuracy_ranges(self):
        import verify_dcc_conformity as vdc
        results = vdc.run_checks(
            t_ref=[25.0],
            t_sensor=[25.0],
            me_pre=[0.05],
            u_sensor=[0.35],
            accuracy_ranges=[],
            mae=0.30,
            pfa_threshold_pct=20.0,
            u_ref=0.065,
        )
        assert results["check_g"]["status"] == "N/A"

    def test_check_g_pass_with_accuracy_ranges(self):
        import verify_dcc_conformity as vdc
        ranges = [{"tempMin": -40.0, "tempMax": 125.0, "maxError": 0.5}]
        results = vdc.run_checks(
            t_ref=[25.0],
            t_sensor=[25.0],
            me_pre=[0.1],
            u_sensor=[0.35],
            accuracy_ranges=ranges,
            mae=0.30,
            pfa_threshold_pct=20.0,
            u_ref=0.065,
        )
        assert results["check_g"]["status"] == "PASS"

    def test_check_overlap_pass_when_close(self):
        import verify_dcc_conformity as vdc
        results = vdc.run_checks(
            t_ref=[25.0],
            t_sensor=[25.1],
            me_pre=[0.1],
            u_sensor=[0.35],
            accuracy_ranges=[],
            mae=0.30,
            pfa_threshold_pct=20.0,
            u_ref=0.065,
        )
        assert results["check_overlap"]["status"] == "PASS"

    def test_check_overlap_fail_when_far_apart(self):
        import verify_dcc_conformity as vdc
        results = vdc.run_checks(
            t_ref=[25.0],
            t_sensor=[30.0],
            me_pre=[5.0],
            u_sensor=[0.35],
            accuracy_ranges=[],
            mae=0.30,
            pfa_threshold_pct=20.0,
            u_ref=0.065,
        )
        assert results["check_overlap"]["status"] == "FAIL"

    def test_normal_cdf_standard_values(self):
        import verify_dcc_conformity as vdc
        assert vdc.normal_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
        assert vdc.normal_cdf(float("inf")) == pytest.approx(1.0, abs=1e-9)
        assert vdc.normal_cdf(float("-inf")) == pytest.approx(0.0, abs=1e-9)

    def test_load_sensor_accuracy_ranges_from_file(self):
        import verify_dcc_conformity as vdc
        ranges = vdc.load_sensor_accuracy_ranges(SENSOR_JSON)
        assert isinstance(ranges, list)


# ===========================================================================
# LSB conversion helpers
# ===========================================================================

class TestLsbConversionHelpers:

    def test_phys_to_lsb16_at_min(self):
        from model_calibration.linear_calibration import phys_to_lsb16
        result = phys_to_lsb16(np.array([LSB_MIN]), LSB_SCALE, ADC_MAX)
        assert result[0] == pytest.approx(0.0, abs=1.0)

    def test_phys_to_lsb16_at_max(self):
        from model_calibration.linear_calibration import phys_to_lsb16
        result = phys_to_lsb16(np.array([LSB_MAX]), LSB_SCALE, ADC_MAX)
        assert result[0] == pytest.approx(ADC_MAX, abs=1.0)

    def test_lsb16_to_phys_roundtrip(self):
        from model_calibration.linear_calibration import phys_to_lsb16, lsb16_to_phys
        temps = np.array([-40.0, 0.0, 25.0, 75.0, 105.0])
        lsb = phys_to_lsb16(temps, LSB_SCALE, ADC_MAX)
        back = lsb16_to_phys(lsb, LSB_SCALE, ADC_MAX)
        assert back == pytest.approx(temps, abs=0.01)

    def test_parse_step_valid(self):
        from model_calibration.linear_calibration import parse_step
        t, n = parse_step("(25.0, 100)")
        assert t == pytest.approx(25.0)
        assert n == pytest.approx(100.0)

    def test_parse_step_invalid_raises(self):
        from model_calibration.linear_calibration import parse_step
        with pytest.raises(ValueError):
            parse_step("not_a_step")


# ===========================================================================
# run_prechecks — all three models
# ===========================================================================

def _payload_with_n_steps(n: int) -> dict:
    """Build a minimal payload dict with exactly n steps declared."""
    return {"steps": [f"({float(i * 25)}, 20)" for i in range(n)],
            "reference_temperature_samples": [],
            "sensor_raw_samples": []}


def _good_sensor_json() -> dict:
    return {
        "ranges": {
            "phys": {"dsi": "\\degreeCelsius"},
            "elec": {"dsi": "\\one"},
        }
    }


def _good_ref_json() -> dict:
    return {"ranges": {"phys": {"dsi": "\\degreeCelsius"}}}


def _bad_ref_json() -> dict:
    return {"ranges": {"phys": {"dsi": "\\pascal"}}}


class TestLinearPrechecks:

    def test_ok_with_enough_steps(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(6))
        assert result["ok"] is True
        assert result["steps_ok"] is True
        assert result["n_steps"] == 6
        assert result["errors"] == []

    def test_fail_with_too_few_steps(self):
        from model_calibration.linear_calibration import run_prechecks, MIN_STEPS_LINEAR
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_LINEAR - 1))
        assert result["ok"] is False
        assert result["steps_ok"] is False
        assert len(result["errors"]) == 1

    def test_exactly_minimum_steps_passes(self):
        from model_calibration.linear_calibration import run_prechecks, MIN_STEPS_LINEAR
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_LINEAR))
        assert result["steps_ok"] is True

    def test_zero_steps_fails(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(0))
        assert result["ok"] is False
        assert result["n_steps"] == 0

    def test_unit_check_none_when_disabled(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(6), check_units=False)
        assert result["unit_check"] is None

    def test_unit_check_none_when_no_jsons(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(6), check_units=True,
                               sensor_json=None, ref_json=None)
        assert result["unit_check"] is None

    def test_unit_check_passes_with_good_jsons(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(6),
            sensor_json=_good_sensor_json(), ref_json=_good_ref_json(),
            check_units=True,
        )
        assert result["unit_check"] is not None
        assert result["unit_check"].ok is True
        assert result["ok"] is True

    def test_unit_check_fails_with_bad_ref(self):
        from model_calibration.linear_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(6),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["unit_check"] is not None
        assert result["unit_check"].ok is False
        assert result["ok"] is False
        assert len(result["errors"]) >= 1

    def test_both_checks_fail_errors_combined(self):
        from model_calibration.linear_calibration import run_prechecks, MIN_STEPS_LINEAR
        result = run_prechecks(
            _payload_with_n_steps(MIN_STEPS_LINEAR - 1),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["ok"] is False
        assert len(result["errors"]) >= 2

    def test_calibrate_raises_on_too_few_steps(self):
        from model_calibration.linear_calibration import calibrate, MIN_STEPS_LINEAR
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        with pytest.raises(ValueError, match=str(MIN_STEPS_LINEAR)):
            calibrate(
                payload=_payload_with_n_steps(MIN_STEPS_LINEAR - 1),
                lsb_scale_sensor_info=LSB_SCALE, sample_size=20,
                adc_max=ADC_MAX, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
                verbose=False,
            )


class TestCubicPrechecks:

    def test_ok_with_enough_steps(self):
        from model_calibration.cubic_calibration import run_prechecks, _N_COEFFS
        result = run_prechecks(_payload_with_n_steps(_N_COEFFS + 2))
        assert result["ok"] is True
        assert result["steps_ok"] is True

    def test_fail_with_too_few_steps(self):
        from model_calibration.cubic_calibration import run_prechecks, _N_COEFFS
        result = run_prechecks(_payload_with_n_steps(_N_COEFFS - 1))
        assert result["ok"] is False
        assert result["steps_ok"] is False
        assert len(result["errors"]) == 1

    def test_exactly_minimum_steps_passes(self):
        from model_calibration.cubic_calibration import run_prechecks, _N_COEFFS
        result = run_prechecks(_payload_with_n_steps(_N_COEFFS))
        assert result["steps_ok"] is True

    def test_unit_check_passes_with_good_jsons(self):
        from model_calibration.cubic_calibration import run_prechecks, _N_COEFFS
        result = run_prechecks(
            _payload_with_n_steps(_N_COEFFS),
            sensor_json=_good_sensor_json(), ref_json=_good_ref_json(),
            check_units=True,
        )
        assert result["unit_check"].ok is True
        assert result["ok"] is True

    def test_unit_check_fails_with_bad_ref(self):
        from model_calibration.cubic_calibration import run_prechecks, _N_COEFFS
        result = run_prechecks(
            _payload_with_n_steps(_N_COEFFS),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["ok"] is False

    def test_calibrate_raises_on_too_few_steps(self):
        from model_calibration.cubic_calibration import calibrate, _N_COEFFS
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        with pytest.raises(ValueError, match=str(_N_COEFFS)):
            calibrate(
                payload=_payload_with_n_steps(_N_COEFFS - 1),
                lsb_scale_sensor_info=LSB_SCALE, sample_size=20,
                adc_max=ADC_MAX, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
                verbose=False,
            )


class TestCubeLogPrechecks:

    def test_ok_with_enough_steps(self):
        from model_calibration.cube_log_calibration import run_prechecks, MIN_STEPS_CUBE_LOG
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_CUBE_LOG + 3))
        assert result["ok"] is True
        assert result["steps_ok"] is True

    def test_fail_with_too_few_steps(self):
        from model_calibration.cube_log_calibration import run_prechecks, MIN_STEPS_CUBE_LOG
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_CUBE_LOG - 1))
        assert result["ok"] is False
        assert result["steps_ok"] is False
        assert len(result["errors"]) == 1

    def test_exactly_minimum_steps_passes(self):
        from model_calibration.cube_log_calibration import run_prechecks, MIN_STEPS_CUBE_LOG
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_CUBE_LOG))
        assert result["steps_ok"] is True

    def test_unit_check_passes_with_good_jsons(self):
        from model_calibration.cube_log_calibration import run_prechecks, MIN_STEPS_CUBE_LOG
        result = run_prechecks(
            _payload_with_n_steps(MIN_STEPS_CUBE_LOG),
            sensor_json=_good_sensor_json(), ref_json=_good_ref_json(),
            check_units=True,
        )
        assert result["unit_check"].ok is True
        assert result["ok"] is True

    def test_unit_check_fails_with_bad_ref(self):
        from model_calibration.cube_log_calibration import run_prechecks, MIN_STEPS_CUBE_LOG
        result = run_prechecks(
            _payload_with_n_steps(MIN_STEPS_CUBE_LOG),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["ok"] is False

    def test_calibrate_raises_on_too_few_steps(self):
        from model_calibration.cube_log_calibration import calibrate, MIN_STEPS_CUBE_LOG
        ub_pt_degc, ub_tmp_lsb = _default_uncertainties()
        with pytest.raises(ValueError, match=str(MIN_STEPS_CUBE_LOG)):
            calibrate(
                payload=_payload_with_n_steps(MIN_STEPS_CUBE_LOG - 1),
                lsb_scale_sensor_info=LSB_SCALE, sample_size=20,
                adc_max=ADC_MAX, ub_pt_degc=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
                verbose=False,
            )
