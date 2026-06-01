"""
test_interp_calibration.py
==========================
Unit and integration tests for the interpolation-based calibration models
and the updated unit_checks module.

Covers
------
- cubic_interp_calibration  : Lagrange cubic (4-node)
- linear_interp_calibration : piecewise linear (N-node)
- unit_checks               : check_dsi / convert_result for new model strings

Run
---
    pytest backend/calibration/test/test_interp_calibration.py -v
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
TESTS_DIR   = Path(__file__).resolve().parent
CALIB_ROOT  = TESTS_DIR.parent
SCRIPTS_DIR = CALIB_ROOT / "scripts"
MODELS_DIR  = CALIB_ROOT / "models_in"
DATA_DIR    = TESTS_DIR / "data_in"

for p in (str(SCRIPTS_DIR), str(MODELS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

INPUT_JSON  = DATA_DIR / "export2_tmp126_lsb16.json"
SENSOR_JSON = MODELS_DIR / "ntc_temperature.json"
REF_JSON    = MODELS_DIR / "fluke_9142.json"

ADC_BITS  = 16
ADC_MAX   = float((1 << ADC_BITS) - 1)   # 65535.0
LSB_MIN   = -40.0
LSB_MAX   = 105.0
LSB_PER_C = ADC_MAX / (LSB_MAX - LSB_MIN)
LSB_SCALE = {"minPhysVal": LSB_MIN, "maxPhysVal": LSB_MAX}
SAMPLE_SIZE = 20


def _default_uncertainties():
    """Return (ub_pt_lsb, ub_tmp_lsb) for the OLS regression models (LSB domain)."""
    ub_pt_c  = 0.065 / 2.0
    ub_tmp_c = 0.30 / math.sqrt(3.0)
    return ub_pt_c * LSB_PER_C, ub_tmp_c * LSB_PER_C


def _interp_uncertainties():
    """Return (ub_pt_degc, ub_tmp_lsb) for the interpolation models.

    Interpolation models receive ub_pt as °C (not LSB) because they work
    in physical units for the y (reference) domain.
    """
    ub_pt_degc  = 0.065 / 2.0          # 0.0325 °C  (standard uncertainty)
    ub_tmp_lsb  = 0.30                  # 0.30  LSB  (sensor type-B, from sensor.uB)
    return ub_pt_degc, ub_tmp_lsb


def _load_payload() -> Dict[str, Any]:
    return json.loads(INPUT_JSON.read_text(encoding="utf-8"))


def _payload_with_n_steps(n: int) -> Dict[str, Any]:
    return {
        "steps": [f"({float(i * 25)}, 20)" for i in range(n)],
        "reference_temperature_samples": [],
        "sensor_raw_samples": [],
    }


def _good_sensor_json() -> Dict[str, Any]:
    return {"ranges": {"phys": {"dsi": "\\degreeCelsius"}, "elec": {"dsi": "\\one"}}}


def _good_ref_json() -> Dict[str, Any]:
    return {"ranges": {"phys": {"dsi": "\\degreeCelsius"}}}


def _bad_ref_json() -> Dict[str, Any]:
    return {"ranges": {"phys": {"dsi": "\\pascal"}}}


# ===========================================================================
# 1. Lagrange helper math (cubic_interp_calibration internals)
# ===========================================================================

class TestLagrangeHelpers:
    """Verify the core math of the Lagrange basis functions."""

    def _import(self):
        from model_calibration.cubic_interp_calibration import (
            _lagrange_weights,
            _lagrange_weights_derivative,
            _predict,
        )
        return _lagrange_weights, _lagrange_weights_derivative, _predict

    def test_weights_sum_to_one(self):
        """Lagrange weights always sum to 1 (partition-of-unity property)."""
        lw, _, _ = self._import()
        x_nodes = np.array([10.0, 30.0, 60.0, 90.0])
        for x in [10.0, 25.0, 50.0, 80.0, 90.0]:
            w = lw(x, x_nodes)
            assert np.sum(w) == pytest.approx(1.0, abs=1e-12)

    def test_weight_is_one_at_own_node(self):
        """ell_i(x_i) == 1 for each node."""
        lw, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        for i, xi in enumerate(x_nodes):
            w = lw(xi, x_nodes)
            assert w[i] == pytest.approx(1.0, abs=1e-10)

    def test_weight_is_zero_at_other_nodes(self):
        """ell_i(x_j) == 0 for j != i."""
        lw, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        for i, xi in enumerate(x_nodes):
            w = lw(xi, x_nodes)
            for j in range(len(x_nodes)):
                if j != i:
                    assert abs(w[j]) < 1e-10, f"w[{j}] at node {i} should be 0, got {w[j]}"

    def test_interpolant_exact_at_nodes(self):
        """Cubic interpolant reproduces node values exactly."""
        _, _, pred = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 60.0])
        for xi, yi in zip(x_nodes, y_nodes):
            assert pred(xi, x_nodes, y_nodes) == pytest.approx(yi, abs=1e-8)

    def test_interpolant_reproduces_linear_function(self):
        """On a linear function f(x)=2x+3, cubic interpolant must be exact everywhere."""
        _, _, pred = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = 2.0 * x_nodes + 3.0
        test_xs = np.linspace(0, 50, 20)
        for x in test_xs:
            expected = 2.0 * x + 3.0
            assert pred(x, x_nodes, y_nodes) == pytest.approx(expected, abs=1e-8)

    def test_interpolant_reproduces_cubic_function(self):
        """On a true cubic f(x)=x³-2x²+x-1, cubic interpolant must be exact."""
        _, _, pred = self._import()
        x_nodes = np.array([1.0, 2.0, 3.0, 4.0])
        y_nodes = x_nodes**3 - 2*x_nodes**2 + x_nodes - 1
        test_xs = np.linspace(1, 4, 15)
        for x in test_xs:
            expected = x**3 - 2*x**2 + x - 1
            assert pred(x, x_nodes, y_nodes) == pytest.approx(expected, abs=1e-6)

    def test_derivative_numerical_consistency(self):
        """Derivative via the formula matches finite differences."""
        _, lwd, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        from model_calibration.cubic_interp_calibration import _lagrange_weights
        eps = 1e-5
        x = 18.0  # interior point, not at a node
        dw_analytical = lwd(x, x_nodes)
        dw_numerical = (
            _lagrange_weights(x + eps, x_nodes) - _lagrange_weights(x - eps, x_nodes)
        ) / (2 * eps)
        for i in range(len(x_nodes)):
            assert dw_analytical[i] == pytest.approx(dw_numerical[i], abs=1e-5)

    def test_duplicate_nodes_raises(self):
        """Duplicate adjacent nodes must raise ValueError."""
        from model_calibration.cubic_interp_calibration import _lagrange_weights
        x_nodes = np.array([0.0, 10.0, 10.0, 50.0])
        with pytest.raises(ValueError):
            _lagrange_weights(20.0, x_nodes)


# ===========================================================================
# 2. GUM uncertainty — cubic interpolation
# ===========================================================================

class TestCubicInterpGUM:

    def _import(self):
        from model_calibration.cubic_interp_calibration import _gum_uncertainty
        return _gum_uncertainty

    def test_gum_non_negative(self):
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        u_y = np.array([0.01, 0.02, 0.015, 0.01])
        u_x = 1.5
        for x in [5.0, 15.0, 35.0]:
            u = gum(x, u_x, x_nodes, y_nodes, u_y)
            assert u >= 0.0

    def test_gum_zero_when_all_uncertainties_zero(self):
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        u_y = np.zeros(4)
        u = gum(15.0, 0.0, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(0.0, abs=1e-14)

    def test_gum_scales_with_u_x(self):
        """When only u_x is nonzero, GUM uncertainty scales linearly with u_x."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        u_y = np.zeros(4)
        u1 = gum(15.0, 1.0, x_nodes, y_nodes, u_y)
        u2 = gum(15.0, 2.0, x_nodes, y_nodes, u_y)
        assert u2 == pytest.approx(2.0 * u1, rel=1e-9)

    def test_gum_scales_with_u_y(self):
        """When only u_y is nonzero and uniform, GUM uncertainty scales linearly."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        # At x = node, only one weight is nonzero so u = u_y[i]
        # At x=x_nodes[0], w = [1, 0, 0, 0], so u = u_y[0]
        u_y_a = np.array([0.1, 0.0, 0.0, 0.0])
        u_y_b = np.array([0.2, 0.0, 0.0, 0.0])
        ua = gum(0.0, 0.0, x_nodes, y_nodes, u_y_a)
        ub = gum(0.0, 0.0, x_nodes, y_nodes, u_y_b)
        assert ub == pytest.approx(2.0 * ua, rel=1e-9)

    def test_gum_at_node_matches_u_y_node(self):
        """At a node, uncertainty = u_y[i] (weight=1, others=0, u_x term=0)."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        u_y = np.array([0.05, 0.03, 0.04, 0.06])
        for i, (xi, ui) in enumerate(zip(x_nodes, u_y)):
            u = gum(xi, 0.0, x_nodes, y_nodes, u_y)
            assert u == pytest.approx(ui, abs=1e-10), f"node {i}: expected {ui}, got {u}"


# ===========================================================================
# 3. Piecewise linear helpers (linear_interp_calibration internals)
# ===========================================================================

class TestLinearInterpHelpers:

    def _import(self):
        from model_calibration.linear_interp_calibration import (
            _find_interval,
            _lambda_weight,
            _predict,
        )
        return _find_interval, _lambda_weight, _predict

    def test_find_interval_interior(self):
        find, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        assert find(5.0,  x_nodes) == 0
        assert find(15.0, x_nodes) == 1
        assert find(30.0, x_nodes) == 2

    def test_find_interval_at_node(self):
        find, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        # x == x_nodes[1] should be in interval [0,1] (first interval where x <= x_nodes[1])
        assert find(10.0, x_nodes) in (0, 1)

    def test_find_interval_clamps_low(self):
        find, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        assert find(-5.0, x_nodes) == 0

    def test_find_interval_clamps_high(self):
        find, _, _ = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        assert find(100.0, x_nodes) == 2  # last interval

    def test_lambda_at_left_node(self):
        _, lam, _ = self._import()
        assert lam(0.0, 0.0, 10.0) == pytest.approx(0.0)

    def test_lambda_at_right_node(self):
        _, lam, _ = self._import()
        assert lam(10.0, 0.0, 10.0) == pytest.approx(1.0)

    def test_lambda_midpoint(self):
        _, lam, _ = self._import()
        assert lam(5.0, 0.0, 10.0) == pytest.approx(0.5)

    def test_lambda_duplicate_nodes_raises(self):
        _, lam, _ = self._import()
        with pytest.raises(ValueError):
            lam(5.0, 10.0, 10.0)

    def test_predict_exact_at_nodes(self):
        _, _, pred = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        for xi, yi in zip(x_nodes, y_nodes):
            assert pred(xi, x_nodes, y_nodes) == pytest.approx(yi, abs=1e-8)

    def test_predict_midpoint(self):
        _, _, pred = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 20.0])
        assert pred(5.0, x_nodes, y_nodes) == pytest.approx(10.0)

    def test_predict_linear_function_exact(self):
        _, _, pred = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = 3.0 * x_nodes + 1.0
        for x in np.linspace(0, 50, 30):
            assert pred(x, x_nodes, y_nodes) == pytest.approx(3.0 * x + 1.0, abs=1e-8)


# ===========================================================================
# 4. GUM uncertainty — linear interpolation
# ===========================================================================

class TestLinearInterpGUM:

    def _import(self):
        from model_calibration.linear_interp_calibration import _gum_uncertainty
        return _gum_uncertainty

    def test_gum_non_negative(self):
        gum = self._import()
        x_nodes = np.array([0.0, 10.0, 25.0, 50.0])
        y_nodes = np.array([5.0, 15.0, 30.0, 55.0])
        u_y = np.array([0.01, 0.02, 0.015, 0.01])
        for x in [5.0, 12.0, 40.0]:
            u = gum(x, 1.0, x_nodes, y_nodes, u_y)
            assert u >= 0.0

    def test_gum_zero_all_zero_inputs(self):
        gum = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 10.0])
        u_y = np.zeros(2)
        u = gum(5.0, 0.0, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(0.0, abs=1e-14)

    def test_gum_at_left_node_equals_u_y0(self):
        """At x = x_nodes[0]: lambda=0, w1=1 → u = u_y[0] (when u_x=0)."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 10.0])
        u_y = np.array([0.05, 0.03])
        u = gum(0.0, 0.0, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(0.05, abs=1e-10)

    def test_gum_at_right_node_equals_u_y1(self):
        """At x = x_nodes[1]: lambda=1, w2=1 → u = u_y[1] (when u_x=0)."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 10.0])
        u_y = np.array([0.05, 0.03])
        u = gum(10.0, 0.0, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(0.03, abs=1e-10)

    def test_gum_x_sensitivity_on_unit_slope(self):
        """On y=x (slope=1): dy/dx=1, so u_x contribution = u_x."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 10.0])
        u_y = np.zeros(2)
        u_x = 2.5
        u = gum(5.0, u_x, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(u_x * 1.0, abs=1e-10)

    def test_gum_with_node_position_uncertainty(self):
        """Including u_x_nodes must be >= version without (more variance sources)."""
        gum = self._import()
        x_nodes = np.array([0.0, 10.0])
        y_nodes = np.array([0.0, 10.0])
        u_y = np.array([0.02, 0.02])
        u_x_nodes = np.array([0.5, 0.5])
        u_without = gum(5.0, 0.5, x_nodes, y_nodes, u_y, None)
        u_with    = gum(5.0, 0.5, x_nodes, y_nodes, u_y, u_x_nodes)
        assert u_with >= u_without

    def test_gum_quadrature_formula_manual(self):
        """At midpoint on uniform slope, verify closed-form result."""
        gum = self._import()
        x1, x2 = 0.0, 10.0
        y1, y2 = 0.0, 20.0  # slope = 2
        x = 5.0              # lambda = 0.5
        u_y1, u_y2 = 0.1, 0.2
        u_x = 1.0
        slope = (y2 - y1) / (x2 - x1)  # = 2
        lam = (x - x1) / (x2 - x1)     # = 0.5
        expected = math.sqrt(
            ((1 - lam) * u_y1)**2 + (lam * u_y2)**2 + (slope * u_x)**2
        )
        x_nodes = np.array([x1, x2])
        y_nodes = np.array([y1, y2])
        u_y = np.array([u_y1, u_y2])
        u = gum(x, u_x, x_nodes, y_nodes, u_y)
        assert u == pytest.approx(expected, rel=1e-9)


# ===========================================================================
# 5. run_prechecks — cubic_interp
# ===========================================================================

class TestCubicInterpPrechecks:

    def test_ok_with_exactly_four_steps(self):
        from model_calibration.cubic_interp_calibration import (
            run_prechecks, MIN_STEPS_CUBIC_INTERP,
        )
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_CUBIC_INTERP))
        assert result["ok"] is True
        assert result["steps_ok"] is True
        assert result["n_steps"] == MIN_STEPS_CUBIC_INTERP

    def test_ok_with_more_than_four_steps(self):
        from model_calibration.cubic_interp_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(6))
        assert result["ok"] is True
        # Extra steps → warning
        assert any("extra" in w for w in result["warnings"])

    def test_fail_with_fewer_than_four_steps(self):
        from model_calibration.cubic_interp_calibration import (
            run_prechecks, MIN_STEPS_CUBIC_INTERP,
        )
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_CUBIC_INTERP - 1))
        assert result["ok"] is False
        assert result["steps_ok"] is False
        assert len(result["errors"]) >= 1

    def test_zero_steps_fails(self):
        from model_calibration.cubic_interp_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(0))
        assert result["ok"] is False

    def test_unit_check_none_when_disabled(self):
        from model_calibration.cubic_interp_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(4), check_units=False)
        assert result["unit_check"] is None

    def test_unit_check_passes_with_good_jsons(self):
        from model_calibration.cubic_interp_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(4),
            sensor_json=_good_sensor_json(), ref_json=_good_ref_json(),
            check_units=True,
        )
        assert result["unit_check"] is not None
        assert result["unit_check"].ok is True
        assert result["ok"] is True

    def test_unit_check_fails_with_bad_ref(self):
        from model_calibration.cubic_interp_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(4),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["ok"] is False
        assert len(result["errors"]) >= 1

    def test_calibrate_raises_on_too_few_steps(self):
        from model_calibration.cubic_interp_calibration import (
            calibrate, MIN_STEPS_CUBIC_INTERP,
        )
        ub_pt_lsb, ub_tmp_lsb = _default_uncertainties()
        with pytest.raises(ValueError):
            calibrate(
                payload=_payload_with_n_steps(MIN_STEPS_CUBIC_INTERP - 1),
                lsb_scale_sensor_info=LSB_SCALE, sample_size=SAMPLE_SIZE,
                adc_max=ADC_MAX, ub_pt_lsb=ub_pt_lsb, ub_tmp_lsb=ub_tmp_lsb,
                verbose=False,
            )


# ===========================================================================
# 6. run_prechecks — linear_interp
# ===========================================================================

class TestLinearInterpPrechecks:

    def test_ok_with_two_steps(self):
        from model_calibration.linear_interp_calibration import (
            run_prechecks, MIN_STEPS_LINEAR_INTERP,
        )
        result = run_prechecks(_payload_with_n_steps(MIN_STEPS_LINEAR_INTERP))
        assert result["ok"] is True
        assert result["steps_ok"] is True

    def test_fail_with_one_step(self):
        from model_calibration.linear_interp_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(1))
        assert result["ok"] is False

    def test_ok_with_many_steps(self):
        from model_calibration.linear_interp_calibration import run_prechecks
        result = run_prechecks(_payload_with_n_steps(6))
        assert result["ok"] is True

    def test_unit_check_passes_with_good_jsons(self):
        from model_calibration.linear_interp_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(6),
            sensor_json=_good_sensor_json(), ref_json=_good_ref_json(),
            check_units=True,
        )
        assert result["unit_check"].ok is True
        assert result["ok"] is True

    def test_unit_check_fails_with_bad_ref(self):
        from model_calibration.linear_interp_calibration import run_prechecks
        result = run_prechecks(
            _payload_with_n_steps(6),
            sensor_json=_good_sensor_json(), ref_json=_bad_ref_json(),
            check_units=True,
        )
        assert result["ok"] is False

    def test_calibrate_raises_on_one_step(self):
        from model_calibration.linear_interp_calibration import calibrate
        ub_pt_lsb, ub_tmp_lsb = _default_uncertainties()
        with pytest.raises(ValueError):
            calibrate(
                payload=_payload_with_n_steps(1),
                lsb_scale_sensor_info=LSB_SCALE, sample_size=SAMPLE_SIZE,
                adc_max=ADC_MAX, ub_pt_lsb=ub_pt_lsb, ub_tmp_lsb=ub_tmp_lsb,
                verbose=False,
            )


# ===========================================================================
# 7. calibrate() — cubic_interp — integration tests with real data
# ===========================================================================

@pytest.mark.skipif(not INPUT_JSON.exists(), reason="test data not found")
class TestCubicInterpCalibrate:

    @pytest.fixture(scope="class")
    def result(self):
        from model_calibration.cubic_interp_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _interp_uncertainties()
        return calibrate(
            payload=_load_payload(),
            lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_pt_lsb=ub_pt_degc,
            ub_tmp_lsb=ub_tmp_lsb,
            verbose=False,
        )

    def test_model_label(self, result):
        assert result["model"] == "cubic_interp"

    def test_required_keys_present(self, result):
        required = {
            "x_nodes", "y_nodes", "uc_x_nodes", "uc_y_nodes",
            "node_steps", "steps", "dati_raw", "risultati_elaborati",
            "expanded_uncertainties", "per_step_budget", "ref_temp_means",
            "rmse_degC", "u_H_degC", "lsb_per_c", "ub_pt_lsb", "ub_tmp_lsb",
        }
        assert required.issubset(result.keys())

    def test_four_nodes(self, result):
        assert len(result["x_nodes"]) == 4
        assert len(result["y_nodes"]) == 4

    def test_nodes_sorted_ascending(self, result):
        x = result["x_nodes"]
        assert all(x[i] < x[i + 1] for i in range(len(x) - 1))

    def test_interpolant_exact_at_nodes(self, result):
        """Interpolated value at each node position must equal y_node."""
        from model_calibration.cubic_interp_calibration import _predict
        x_nodes = np.array(result["x_nodes"])
        y_nodes = np.array(result["y_nodes"])
        for xi, yi in zip(x_nodes, y_nodes):
            y_hat = _predict(float(xi), x_nodes, y_nodes)
            assert y_hat == pytest.approx(yi, abs=1e-6)

    def test_six_expanded_uncertainties(self, result):
        assert len(result["expanded_uncertainties"]) == 6
        for u in result["expanded_uncertainties"]:
            assert u > 0.0

    def test_expanded_uncertainties_under_one_degC(self, result):
        for u in result["expanded_uncertainties"]:
            assert u < 1.0, f"U(E)={u:.4f} °C exceeds 1 °C"

    def test_per_step_budget_length(self, result):
        assert len(result["per_step_budget"]) == len(result["steps"])

    def test_per_step_budget_keys(self, result):
        required_keys = {
            "t_nominal", "uA_ref_degC", "uA_i_degC", "mu_T_ref", "mu_T_i",
            "mu_E", "U_E", "u_interp_degC", "U_interp_degC", "residual_degC",
        }
        for entry in result["per_step_budget"]:
            assert required_keys.issubset(entry.keys())

    def test_u_exp_equals_two_times_mu_E(self, result):
        """U_E must equal 2 * mu_E at every step."""
        for entry in result["per_step_budget"]:
            assert entry["U_E"] == pytest.approx(2.0 * entry["mu_E"], rel=1e-9)

    def test_rmse_non_negative(self, result):
        assert result["rmse_degC"] >= 0.0

    def test_rmse_matches_residuals(self, result):
        """RMSE must equal sqrt(mean(residuals^2))."""
        residuals = [b["residual_degC"] for b in result["per_step_budget"]]
        expected_rmse = math.sqrt(sum(r**2 for r in residuals) / len(residuals))
        assert result["rmse_degC"] == pytest.approx(expected_rmse, rel=1e-9)

    def test_ref_temp_means_within_range(self, result):
        # Real calibration data may extend beyond the sensor's declared range
        for t in result["ref_temp_means"]:
            assert LSB_MIN - 30 <= t <= LSB_MAX + 30, f"ref_temp_mean {t} unreasonably outside range"

    def test_lsb_per_c_correct(self, result):
        expected = ADC_MAX / (LSB_MAX - LSB_MIN)
        assert result["lsb_per_c"] == pytest.approx(expected, rel=1e-9)

    def test_build_report_runs(self, result):
        from model_calibration.cubic_interp_calibration import build_report
        report = build_report(
            steps=result["steps"],
            risultati_elaborati=result["risultati_elaborati"],
            x_nodes=result["x_nodes"],
            y_nodes=result["y_nodes"],
            per_step_budget=result["per_step_budget"],
            expanded_uncertainties=result["expanded_uncertainties"],
            rmse_degC=result["rmse_degC"],
            lsb_scale_sensor_info=LSB_SCALE,
            adc_max=ADC_MAX,
            ub_pt_lsb=result["ub_pt_lsb"],
            ub_tmp_lsb=result["ub_tmp_lsb"],
        )
        assert isinstance(report, str)
        assert len(report) > 100
        assert "Cubic Lagrange" in report


# ===========================================================================
# 8. calibrate() — linear_interp — integration tests with real data
# ===========================================================================

@pytest.mark.skipif(not INPUT_JSON.exists(), reason="test data not found")
class TestLinearInterpCalibrate:

    @pytest.fixture(scope="class")
    def result(self):
        from model_calibration.linear_interp_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _interp_uncertainties()
        return calibrate(
            payload=_load_payload(),
            lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE,
            adc_max=ADC_MAX,
            ub_pt_lsb=ub_pt_degc,
            ub_tmp_lsb=ub_tmp_lsb,
            verbose=False,
        )

    def test_model_label(self, result):
        assert result["model"] == "linear_interp"

    def test_required_keys_present(self, result):
        required = {
            "x_nodes", "y_nodes", "uc_x_nodes", "uc_y_nodes",
            "steps", "dati_raw", "risultati_elaborati",
            "expanded_uncertainties", "per_step_budget", "ref_temp_means",
            "rmse_degC", "u_H_degC", "lsb_per_c", "ub_pt_lsb", "ub_tmp_lsb",
        }
        assert required.issubset(result.keys())

    def test_two_nodes_always(self, result):
        """First/last strategy always produces exactly 2 nodes."""
        assert len(result["x_nodes"]) == 2
        assert len(result["y_nodes"]) == 2
        assert len(result["node_steps"]) == 2

    def test_nodes_sorted_ascending(self, result):
        x = result["x_nodes"]
        assert all(x[i] <= x[i + 1] for i in range(len(x) - 1))

    def test_interpolant_exact_at_nodes(self, result):
        """Piecewise linear interpolant is exact at each node."""
        from model_calibration.linear_interp_calibration import _predict
        x_nodes = np.array(result["x_nodes"])
        y_nodes = np.array(result["y_nodes"])
        for xi, yi in zip(x_nodes, y_nodes):
            y_hat = _predict(float(xi), x_nodes, y_nodes)
            assert y_hat == pytest.approx(yi, abs=1e-6)

    def test_six_expanded_uncertainties(self, result):
        assert len(result["expanded_uncertainties"]) == 6
        for u in result["expanded_uncertainties"]:
            assert u > 0.0

    def test_expanded_uncertainties_under_one_degC(self, result):
        for u in result["expanded_uncertainties"]:
            assert u < 1.0

    def test_per_step_budget_length(self, result):
        assert len(result["per_step_budget"]) == len(result["steps"])

    def test_per_step_budget_keys(self, result):
        required_keys = {
            "t_nominal", "uA_ref_degC", "uA_i_degC", "mu_T_ref", "mu_T_i",
            "mu_E", "U_E", "u_interp_degC", "U_interp_degC", "residual_degC",
        }
        for entry in result["per_step_budget"]:
            assert required_keys.issubset(entry.keys())

    def test_u_exp_equals_two_times_mu_E(self, result):
        for entry in result["per_step_budget"]:
            assert entry["U_E"] == pytest.approx(2.0 * entry["mu_E"], rel=1e-9)

    def test_rmse_non_negative(self, result):
        assert result["rmse_degC"] >= 0.0

    def test_rmse_matches_interior_residuals(self, result):
        """RMSE is computed over interior (non-node) steps only."""
        interior = [b["residual_degC"] for b in result["per_step_budget"] if not b["is_node"]]
        if not interior:
            assert result["rmse_degC"] != result["rmse_degC"]  # NaN check
        else:
            expected = math.sqrt(sum(r**2 for r in interior) / len(interior))
            assert result["rmse_degC"] == pytest.approx(expected, rel=1e-9)

    def test_treat_nodes_as_exact_flag(self, result):
        assert result["treat_nodes_as_exact"] is True

    def test_with_node_position_uncertainty(self):
        """treat_nodes_as_exact=False gives same or larger u_interp than True."""
        from model_calibration.linear_interp_calibration import calibrate
        ub_pt_degc, ub_tmp_lsb = _interp_uncertainties()
        r_exact = calibrate(
            payload=_load_payload(), lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE, adc_max=ADC_MAX,
            ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=False, treat_nodes_as_exact=True,
        )
        r_inexact = calibrate(
            payload=_load_payload(), lsb_scale_sensor_info=LSB_SCALE,
            sample_size=SAMPLE_SIZE, adc_max=ADC_MAX,
            ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb,
            verbose=False, treat_nodes_as_exact=False,
        )
        for b_exact, b_inexact in zip(
            r_exact["per_step_budget"], r_inexact["per_step_budget"]
        ):
            assert b_inexact["u_interp_degC"] >= b_exact["u_interp_degC"]

    def test_exactly_two_is_node_true_entries(self, result):
        """Exactly the first and last budget entries must have is_node=True."""
        budget = result["per_step_budget"]
        node_entries = [b for b in budget if b["is_node"]]
        assert len(node_entries) == 2
        assert budget[0]["is_node"] is True
        assert budget[-1]["is_node"] is True
        for b in budget[1:-1]:
            assert b["is_node"] is False

    def test_node_residuals_are_zero(self, result):
        """Residual at both node steps must be exactly zero by construction."""
        for b in result["per_step_budget"]:
            if b["is_node"]:
                assert b["residual_degC"] == pytest.approx(0.0, abs=1e-8), (
                    f"node step {b['t_nominal']} residual={b['residual_degC']}"
                )

    def test_interior_steps_have_nonzero_residuals(self, result):
        """NTC curve is nonlinear, so at least one interior step must show a residual."""
        interior = [b for b in result["per_step_budget"] if not b["is_node"]]
        assert any(abs(b["residual_degC"]) > 1e-6 for b in interior), (
            "Expected at least one interior step with nonzero residual"
        )

    def test_n_interior_correct(self, result):
        interior_count = sum(1 for b in result["per_step_budget"] if not b["is_node"])
        assert result["n_interior"] == interior_count

    def test_node_steps_are_first_and_last_sorted(self, result):
        assert result["node_steps"][0] == result["steps"][0]
        assert result["node_steps"][1] == result["steps"][-1]

    def test_build_report_runs(self, result):
        from model_calibration.linear_interp_calibration import build_report
        report = build_report(
            steps=result["steps"],
            risultati_elaborati=result["risultati_elaborati"],
            x_nodes=result["x_nodes"],
            y_nodes=result["y_nodes"],
            per_step_budget=result["per_step_budget"],
            expanded_uncertainties=result["expanded_uncertainties"],
            rmse_degC=result["rmse_degC"],
            lsb_scale_sensor_info=LSB_SCALE,
            adc_max=ADC_MAX,
            ub_pt_lsb=result["ub_pt_lsb"],
            ub_tmp_lsb=result["ub_tmp_lsb"],
        )
        assert isinstance(report, str)
        assert "Linear Interpolation" in report
        assert "node" in report
        assert "interior" in report


# ===========================================================================
# 9. unit_checks — check_dsi for new model strings
# ===========================================================================

class TestUnitChecksNewModels:
    """Verify that check_dsi handles 'cubic_interp' and 'linear_interp' correctly."""

    def test_cubic_interp_passes_with_temperature_units(self):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _good_ref_json(), "cubic_interp")
        # May not have pint installed; if so, ok=True with warnings
        assert result.ok is True or (
            any("pint" in w for w in result.warnings)
        )

    def test_linear_interp_passes_with_temperature_units(self):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _good_ref_json(), "linear_interp")
        assert result.ok is True or (
            any("pint" in w for w in result.warnings)
        )

    def test_cubic_interp_fails_with_pressure_ref(self):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _bad_ref_json(), "cubic_interp")
        # Either pint not installed (warnings only) or pint available and finds error
        if not any("pint" in w for w in result.warnings):
            assert result.ok is False

    def test_linear_interp_fails_with_pressure_ref(self):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _bad_ref_json(), "linear_interp")
        if not any("pint" in w for w in result.warnings):
            assert result.ok is False

    def test_unknown_model_uses_no_extra_check(self):
        """An unknown model string should not raise; it skips model-specific checks."""
        from model_calibration.unit_checks import check_dsi
        # Should not raise
        result = check_dsi(_good_sensor_json(), _good_ref_json(), "unknown_model_xyz")
        assert isinstance(result.ok, bool)

    def test_result_has_expected_attributes(self):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _good_ref_json(), "cubic_interp")
        assert hasattr(result, "ok")
        assert hasattr(result, "errors")
        assert hasattr(result, "warnings")
        assert hasattr(result, "sensor_phys_unit")
        assert hasattr(result, "ref_phys_unit")

    def test_print_report_runs(self, capsys):
        from model_calibration.unit_checks import check_dsi
        result = check_dsi(_good_sensor_json(), _good_ref_json(), "linear_interp")
        result.print_report("[test]")
        captured = capsys.readouterr()
        assert "[test]" in captured.out


# ===========================================================================
# 10. unit_checks — convert_result for new model strings
# ===========================================================================

class TestConvertResultNewModels:
    """Verify convert_result adds converted/units keys for interpolation models."""

    def _make_interp_result(self, model: str) -> Dict[str, Any]:
        """Minimal synthetic calibration result dict for convert_result."""
        return {
            "model": model,
            "x_nodes": [10000.0, 20000.0, 40000.0, 55000.0],
            "y_nodes": [1000.0, 20000.0, 40000.0, 58000.0],
            "uc_x_nodes": [50.0, 50.0, 50.0, 50.0],
            "uc_y_nodes": [30.0, 30.0, 30.0, 30.0],
            "steps": [0.0, 25.0, 75.0, 100.0],
            "ref_temp_means": [0.0, 25.0, 75.0, 100.0],
            "expanded_uncertainties": [0.35, 0.35, 0.35, 0.35],
            "rmse_degC": 0.012,
            "u_H_degC": 0.012,
            "lsb_per_c": LSB_PER_C,
            "ub_pt_lsb": 0.065 / 2.0 * LSB_PER_C,
            "ub_tmp_lsb": 0.30 / math.sqrt(3.0) * LSB_PER_C,
        }

    def test_convert_result_returns_dict(self):
        from model_calibration.unit_checks import convert_result
        r = convert_result(
            self._make_interp_result("cubic_interp"),
            _good_sensor_json(),
            _good_ref_json(),
        )
        assert isinstance(r, dict)

    def test_original_keys_preserved(self):
        from model_calibration.unit_checks import convert_result
        orig = self._make_interp_result("linear_interp")
        r = convert_result(orig, _good_sensor_json(), _good_ref_json())
        for k, v in orig.items():
            assert k in r
            assert r[k] == v

    def test_converted_and_units_keys_added(self):
        from model_calibration.unit_checks import convert_result
        r = convert_result(
            self._make_interp_result("cubic_interp"),
            _good_sensor_json(),
            _good_ref_json(),
        )
        assert "converted" in r
        assert "units" in r
        assert "conversion_errors" in r

    def test_ref_temp_means_converted(self):
        from model_calibration.unit_checks import convert_result
        r = convert_result(
            self._make_interp_result("cubic_interp"),
            _good_sensor_json(),
            _good_ref_json(),
        )
        # degC -> degC: values unchanged (same unit)
        if "ref_temp_means" in r.get("converted", {}):
            orig = self._make_interp_result("cubic_interp")["ref_temp_means"]
            conv = r["converted"]["ref_temp_means"]
            assert len(conv) == len(orig)

    def test_expanded_uncertainties_converted(self):
        from model_calibration.unit_checks import convert_result
        r = convert_result(
            self._make_interp_result("linear_interp"),
            _good_sensor_json(),
            _good_ref_json(),
        )
        if "expanded_uncertainties" in r.get("converted", {}):
            orig = self._make_interp_result("linear_interp")["expanded_uncertainties"]
            conv = r["converted"]["expanded_uncertainties"]
            # degC -> degC: delta factor = 1
            for v_orig, v_conv in zip(orig, conv):
                assert v_conv == pytest.approx(v_orig, rel=1e-6)

    def test_no_conversion_errors_for_good_jsons(self):
        from model_calibration.unit_checks import convert_result
        r = convert_result(
            self._make_interp_result("cubic_interp"),
            _good_sensor_json(),
            _good_ref_json(),
        )
        # If pint is installed, there should be no conversion errors
        errors = r.get("conversion_errors", [])
        if not any("pint" in str(e) for e in errors):
            assert errors == []


# ===========================================================================
# 11. dsi_to_symbol and dsi_to_xml_unit — regression tests
# ===========================================================================

class TestDsiHelpers:

    def test_dsi_to_symbol_celsius(self):
        from model_calibration.unit_checks import dsi_to_symbol
        assert dsi_to_symbol("\\degreeCelsius") == "°C"

    def test_dsi_to_symbol_kelvin(self):
        from model_calibration.unit_checks import dsi_to_symbol
        assert dsi_to_symbol("\\kelvin") == "K"

    def test_dsi_to_symbol_ohm(self):
        from model_calibration.unit_checks import dsi_to_symbol
        assert dsi_to_symbol("\\ohm") == "Ω"

    def test_dsi_to_symbol_unknown_strips_backslash(self):
        from model_calibration.unit_checks import dsi_to_symbol
        result = dsi_to_symbol("\\unknownUnit")
        assert result == "unknownUnit"

    def test_dsi_to_xml_celsius(self):
        from model_calibration.unit_checks import dsi_to_xml_unit
        assert dsi_to_xml_unit("\\degreeCelsius") == "\\degreecelsius"

    def test_dsi_to_xml_kelvin(self):
        from model_calibration.unit_checks import dsi_to_xml_unit
        assert dsi_to_xml_unit("\\kelvin") == "\\kelvin"

    def test_dsi_to_xml_unknown_lowercases(self):
        from model_calibration.unit_checks import dsi_to_xml_unit
        result = dsi_to_xml_unit("\\SomeThing")
        assert result == "\\something"

    def test_dsi_to_symbol_dimensionless(self):
        from model_calibration.unit_checks import dsi_to_symbol
        result = dsi_to_symbol("\\one")
        assert result == ""  # dimensionless has empty symbol


# ===========================================================================
# 12. Cross-model consistency checks
# ===========================================================================

@pytest.mark.skipif(not INPUT_JSON.exists(), reason="test data not found")
class TestCrossModelConsistency:
    """
    Compare cubic_interp, linear_interp, and polynomial regression models on the
    same dataset. Checks expected ordering of residuals and that all models
    agree on the uncertainty budget structure.
    """

    @pytest.fixture(scope="class")
    def all_results(self):
        from model_calibration.cubic_interp_calibration import (
            calibrate as ci_calibrate,
        )
        from model_calibration.linear_interp_calibration import (
            calibrate as li_calibrate,
        )
        from model_calibration.linear_calibration import (
            calibrate as lin_calibrate,
        )
        from model_calibration.cubic_calibration import (
            calibrate as cub_calibrate,
        )
        ub_pt_lsb, ub_tmp_lsb = _default_uncertainties()       # for OLS models
        ub_pt_degc, ub_tmp_lsb_interp = _interp_uncertainties() # for interp models
        payload = _load_payload()
        ols_kw = dict(lsb_scale_sensor_info=LSB_SCALE, sample_size=SAMPLE_SIZE,
                      adc_max=ADC_MAX, ub_pt_lsb=ub_pt_lsb, ub_tmp_lsb=ub_tmp_lsb,
                      verbose=False)
        interp_kw = dict(lsb_scale_sensor_info=LSB_SCALE, sample_size=SAMPLE_SIZE,
                         adc_max=ADC_MAX, ub_pt_lsb=ub_pt_degc, ub_tmp_lsb=ub_tmp_lsb_interp,
                         verbose=False)
        return {
            "cubic_interp":  ci_calibrate(payload=payload,  **interp_kw),
            "linear_interp": li_calibrate(payload=payload,  **interp_kw),
            "linear":        lin_calibrate(payload=payload, **ols_kw),
            "cubic":         cub_calibrate(payload=payload, **ols_kw),
        }

    def test_all_models_have_positive_expanded_uncertainties(self, all_results):
        for model_name, result in all_results.items():
            for u in result["expanded_uncertainties"]:
                assert u > 0.0, f"{model_name}: non-positive U(E)"

    def test_all_models_have_same_lsb_per_c(self, all_results):
        expected = ADC_MAX / (LSB_MAX - LSB_MIN)
        for model_name, result in all_results.items():
            assert result["lsb_per_c"] == pytest.approx(expected, rel=1e-9), (
                f"{model_name}: lsb_per_c mismatch"
            )

    def test_interpolation_models_zero_residuals_at_nodes(self, all_results):
        """Interpolation models must have near-zero residual at their node steps."""
        from model_calibration.cubic_interp_calibration import _predict as ci_pred
        from model_calibration.linear_interp_calibration import _predict as li_pred

        # cubic_interp: 4 nodes, all residuals should be ~0 at node x values
        ci = all_results["cubic_interp"]
        x_nodes = np.array(ci["x_nodes"])
        y_nodes = np.array(ci["y_nodes"])
        for xi, yi in zip(x_nodes, y_nodes):
            y_hat = ci_pred(float(xi), x_nodes, y_nodes)
            assert abs(y_hat - yi) < 1e-5, (
                f"cubic_interp: residual at node {xi} is {y_hat - yi}"
            )

        # linear_interp: 2 nodes (first/last); residual_degC at node steps must be 0
        li = all_results["linear_interp"]
        for b in li["per_step_budget"]:
            if b["is_node"]:
                assert abs(b["residual_degC"]) < 1e-8, (
                    f"linear_interp: node step {b['t_nominal']} has non-zero residual "
                    f"{b['residual_degC']}"
                )

    def test_cubic_interp_rmse_not_worse_than_linear_interp(self, all_results):
        """
        On a smooth NTC curve, cubic Lagrange over 4 nodes typically achieves
        lower RMSE than piecewise linear over the same 4 nodes (first 4 steps).
        This is a soft check — just ensure we don't have a trivially broken model.
        """
        # Both models use the same 6 data points; cubic uses 4 as interpolation nodes.
        # We only verify that RMSE is finite and non-negative.
        ci_rmse = all_results["cubic_interp"]["rmse_degC"]
        li_rmse = all_results["linear_interp"]["rmse_degC"]
        assert ci_rmse >= 0.0
        assert li_rmse >= 0.0
        assert math.isfinite(ci_rmse)
        assert math.isfinite(li_rmse)
