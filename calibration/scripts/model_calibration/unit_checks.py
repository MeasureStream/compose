"""
unit_checks.py
==============
Pint-based dimensional analysis utilities for the calibration pipeline.

Two pure, side-effect-free public functions:

    check_dsi(sensor_json, ref_json, model) -> UnitCheckResult
        Validates that the physical units declared in the two JSON model files
        are dimensionally consistent with the calibration model equation.
        Returns a dataclass with a ``ok`` bool, a list of errors, and a list
        of warnings.  Never raises; callers decide whether to abort.

    convert_result(calib_result, sensor_json, ref_json) -> dict
        Attempts to express every numeric result key in the preferred output
        unit derived from the JSON ``unit`` field.  Returns a new dict
        (shallow-merged with calib_result) adding ``*_converted`` and
        ``units`` sub-dicts.  Never raises; on failure it records the error
        and returns the input unchanged.

DSI string mapping
------------------
The JSON files use LaTeX-style DSI strings (e.g. ``\\degreeCelsius``).
The mapping table below translates those to pint unit names.  Unknown strings
are treated as dimensionless with a warning.

Usage
-----
These functions are imported by each calibration engine and called inside
``calibrate()`` when the ``check_units`` / ``convert_units`` kwargs are True.
They can also be imported standalone for testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# DSI -> pint name mapping
# ---------------------------------------------------------------------------

#: Map from the LaTeX DSI strings used in the JSON models to pint unit names.
_DSI_TO_PINT: Dict[str, str] = {
    "\\degreeCelsius":          "degC",
    "\\kelvin":                 "kelvin",
    "\\one":                    "dimensionless",
    "\\meter":                  "meter",
    "\\second":                 "second",
    "\\kilogram":               "kilogram",
    "\\ampere":                 "ampere",
    "\\mole":                   "mole",
    "\\candela":                "candela",
    "\\pascal":                 "pascal",
    "\\bar":                    "bar",
    "\\coulomb":                "coulomb",
    "\\volt":                   "volt",
    "\\ohm":                    "ohm",
    "\\hertz":                  "hertz",
    "\\watt":                   "watt",
    "\\radian":                 "radian",
    "\\degreeFahrenheit":       "degF",
    "\\kelvin\\per\\second":    "kelvin / second",
    "\\second\\per\\kelvin":    "second / kelvin",
    "\\degreeCelsius\\per\\second": "degC / second",
    "\\second\\per\\degreeCelsius": "second / degC",
    "\\meter\\per\\second":     "meter / second",
}

# ---------------------------------------------------------------------------
# Allowed physical dimensions per role in the calibration model
# ---------------------------------------------------------------------------

#: Dimensions that are valid as a *temperature* quantity (T in the model).
_TEMPERATURE_DIMS = frozenset({"[temperature]"})

#: The sensor electrical output (D) is a dimensionless 16-bit integer count.
_DIMENSIONLESS_DIMS = frozenset({"[dimensionless]", ""})


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class UnitCheckResult:
    """Returned by :func:`check_dsi`."""

    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sensor_phys_unit: str = ""       # pint name for sensor physical output
    sensor_elec_unit: str = ""       # pint name for sensor electrical output (D)
    ref_phys_unit: str = ""          # pint name for reference physical output (T_ref)

    def add_error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def print_report(self, prefix: str = "[unit-check]") -> None:
        for w in self.warnings:
            print(f"{prefix} WARNING: {w}")
        for e in self.errors:
            print(f"{prefix} ERROR:   {e}")
        if self.ok:
            print(f"{prefix} PASS — all dimensional checks passed.")
        else:
            print(f"{prefix} FAIL — {len(self.errors)} error(s), calibration blocked.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_pint_ureg():
    """Return a module-level shared pint UnitRegistry (lazy import)."""
    try:
        import pint
        return pint.UnitRegistry()
    except ImportError:
        return None


def _dsi_to_pint_name(dsi: str, label: str, result: UnitCheckResult) -> Optional[str]:
    """
    Translate a DSI string to a pint unit name.

    Returns the pint name, or None if the DSI is unknown (warning added).
    """
    dsi = dsi.strip()
    if dsi in _DSI_TO_PINT:
        return _DSI_TO_PINT[dsi]
    result.add_warning(
        f"{label}: unknown DSI string '{dsi}' — treated as dimensionless. "
        "Add it to _DSI_TO_PINT in unit_checks.py to suppress this warning."
    )
    return "dimensionless"


def _dimensionality_str(quantity) -> str:
    """Return a compact dimensionality string for a pint Quantity."""
    return str(quantity.dimensionality)


def _unit_lx(unit_name: str, ureg) -> str:
    """
    Return the siunitx LaTeX representation of *unit_name* using pint's
    ``Lx`` format specifier (e.g. ``\\si{\\degreeCelsius}``).

    Falls back to the plain pint unit string if formatting fails.
    """
    try:
        unit_obj = ureg.Unit(unit_name)
        return f"{unit_obj:Lx}"
    except Exception:
        return unit_name


# ---------------------------------------------------------------------------
# Public helpers — unit display / XML conversion
# ---------------------------------------------------------------------------

#: Map from pint unit name to the human-readable symbol used in certificates.
_PINT_TO_SYMBOL: Dict[str, str] = {
    "degC":          "\u00b0C",   # °C
    "kelvin":        "K",
    "degF":          "\u00b0F",   # °F
    "dimensionless": "",
    "meter":         "m",
    "second":        "s",
    "kilogram":      "kg",
    "ampere":        "A",
    "mole":          "mol",
    "candela":       "cd",
    "pascal":        "Pa",
    "bar":           "bar",
    "coulomb":       "C",
    "volt":          "V",
    "ohm":           "\u03a9",    # Ω
    "hertz":         "Hz",
    "watt":          "W",
    "radian":        "rad",
}

#: Map from pint unit name to the PTB DCC XML ``unitXMLList`` string (lowercase DSI).
_PINT_TO_XML_UNIT: Dict[str, str] = {
    "degC":          "\\degreecelsius",
    "kelvin":        "\\kelvin",
    "degF":          "\\degreefahrenheit",
    "dimensionless": "\\one",
    "meter":         "\\meter",
    "second":        "\\second",
    "kilogram":      "\\kilogram",
    "ampere":        "\\ampere",
    "mole":          "\\mole",
    "candela":       "\\candela",
    "pascal":        "\\pascal",
    "bar":           "\\bar",
    "coulomb":       "\\coulomb",
    "volt":          "\\volt",
    "ohm":           "\\ohm",
    "hertz":         "\\hertz",
    "watt":          "\\watt",
    "radian":        "\\radian",
}


def dsi_to_symbol(dsi: str) -> str:
    """
    Convert a DSI LaTeX string (e.g. ``\\degreeCelsius``) to a human-readable
    unit symbol (e.g. ``°C``) suitable for PDF table headers and labels.

    Uses the ``_DSI_TO_PINT`` → ``_PINT_TO_SYMBOL`` lookup chain so the
    mapping is maintained in one place.  Falls back to the raw DSI string
    stripped of backslashes if no mapping is found.

    Examples
    --------
    >>> dsi_to_symbol("\\\\degreeCelsius")
    '°C'
    >>> dsi_to_symbol("\\\\kelvin")
    'K'
    """
    pint_name = _DSI_TO_PINT.get(dsi.strip())
    if pint_name is not None:
        sym = _PINT_TO_SYMBOL.get(pint_name)
        if sym is not None:
            return sym
    # Fallback: strip leading backslash(es) and return as-is
    return dsi.lstrip("\\")


def dsi_to_xml_unit(dsi: str) -> str:
    """
    Convert a DSI LaTeX string (e.g. ``\\degreeCelsius``) to the PTB DCC XML
    ``unitXMLList`` string (e.g. ``\\degreecelsius``).

    Used by ``generate_dcc_xml.py`` so the XML unit tag is always consistent
    with the sensor JSON rather than being hardcoded.

    Falls back to the DSI string lowercased if no mapping is found.
    """
    pint_name = _DSI_TO_PINT.get(dsi.strip())
    if pint_name is not None:
        xml_unit = _PINT_TO_XML_UNIT.get(pint_name)
        if xml_unit is not None:
            return xml_unit
    # Fallback: lowercase the DSI (DCC XML uses lowercase convention)
    return dsi.lower()


def _is_temperature(unit_name: str, ureg) -> bool:
    """Return True if the unit has temperature dimensionality."""
    try:
        q = ureg.Quantity(1.0, unit_name)
        return q.dimensionality == ureg.degC.dimensionality
    except Exception:
        return False


def _is_dimensionless(unit_name: str, ureg) -> bool:
    """Return True if the unit is dimensionless."""
    try:
        q = ureg.Quantity(1.0, unit_name)
        return q.dimensionless
    except Exception:
        return False


def _can_convert(from_unit: str, to_unit: str, ureg) -> bool:
    """Return True if from_unit and to_unit are dimensionally compatible."""
    try:
        ureg.Quantity(1.0, from_unit).to(to_unit)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API — check_dsi
# ---------------------------------------------------------------------------


def check_dsi(
    sensor_json: Dict[str, Any],
    ref_json: Dict[str, Any],
    model: str,
) -> UnitCheckResult:
    """
    Validate dimensional consistency of the two model JSON files for the
    given calibration model.

    Parameters
    ----------
    sensor_json : dict
        Full parsed content of ``ntc_temperature.json`` (or equivalent).
    ref_json : dict
        Full parsed content of ``fluke_9142.json`` (or equivalent).
    model : str
        One of ``"linear"``, ``"cubic"``, ``"cube-log"``,
        ``"cubic_interp"``, ``"linear_interp"``.

    Returns
    -------
    UnitCheckResult
        ``.ok`` is False if any hard error is found (incompatible dimensions).
        Warnings are non-fatal (unknown DSI strings, etc.).

    Model equations and expected dimensions
    ----------------------------------------
    linear:        T_ref [temperature] = A [dimensionless] * D [dimensionless] + B [temperature]
                   A is dimensionless (LSB/LSB ratio), B is offset in same unit as T_ref.
                   Both T (output) and T_ref must be temperature.
                   D (sensor electrical output) must be dimensionless.

    cubic:         T_ref [temperature] = a0 + a1*D + a2*D² + a3*D³
                   All coefficients produce temperature when multiplied by D^k (dimensionless).
                   Same constraints as linear.

    cube-log:      1/T [K^-1] = C0 + C1*ln(D) + C3*(ln(D))³
                   T_ref must be temperature; D must be dimensionless.
                   Coefficients have unit K^-1.

    cubic_interp:  Lagrange cubic interpolation through 4 calibration nodes.
                   T_ref [temperature] = sum_i T_ref_i * ell_i(D).
                   Same constraints as linear/cubic: T_ref must be temperature,
                   D must be dimensionless.

    linear_interp: Piecewise linear interpolation between adjacent calibration nodes.
                   T_ref [temperature] = (1-lambda)*T_ref_1 + lambda*T_ref_2,
                   lambda = (D - D_1)/(D_2 - D_1) in [0,1].
                   Same constraints as cubic_interp.
    """
    result = UnitCheckResult()

    ureg = _get_pint_ureg()
    if ureg is None:
        result.add_warning(
            "pint is not installed — unit checks skipped. "
            "Install with: pip install pint"
        )
        return result

    # ── Extract DSI strings from JSON ──
    ranges_s = sensor_json.get("ranges", {})
    phys_s   = ranges_s.get("phys", {})
    elec_s   = ranges_s.get("elec", {})
    ranges_r = ref_json.get("ranges", {})
    phys_r   = ranges_r.get("phys", {})

    sensor_phys_dsi = phys_s.get("dsi", "\\degreeCelsius")
    sensor_elec_dsi = elec_s.get("dsi", "\\one")
    ref_phys_dsi    = phys_r.get("dsi", "\\degreeCelsius")

    sensor_phys_unit = _dsi_to_pint_name(sensor_phys_dsi, "sensor.ranges.phys.dsi", result)
    sensor_elec_unit = _dsi_to_pint_name(sensor_elec_dsi, "sensor.ranges.elec.dsi", result)
    ref_phys_unit    = _dsi_to_pint_name(ref_phys_dsi,    "ref.ranges.phys.dsi",    result)

    result.sensor_phys_unit = sensor_phys_unit or "degC"
    result.sensor_elec_unit = sensor_elec_unit or "dimensionless"
    result.ref_phys_unit    = ref_phys_unit    or "degC"

    # ── Check 1: reference output must be a temperature ──
    if not _is_temperature(result.ref_phys_unit, ureg):
        result.add_error(
            f"Reference calibrator physical unit '{ref_phys_dsi}' "
            f"(siunitx: {_unit_lx(result.ref_phys_unit, ureg)}) "
            f"is not a temperature. "
            f"Dimensionality: {_dimensionality_str(ureg.Quantity(1.0, result.ref_phys_unit))}. "
            f"Expected: [temperature] (e.g. \\degreeCelsius or \\kelvin)."
        )

    # ── Check 2: sensor electrical output must be dimensionless ──
    if not _is_dimensionless(result.sensor_elec_unit, ureg):
        result.add_error(
            f"Sensor electrical unit '{sensor_elec_dsi}' "
            f"(siunitx: {_unit_lx(result.sensor_elec_unit, ureg)}) "
            f"is not dimensionless. "
            f"The calibration model requires D (raw ADC count) to be dimensionless (\\one). "
            f"Got dimensionality: {_dimensionality_str(ureg.Quantity(1.0, result.sensor_elec_unit))}."
        )

    # ── Check 3: model-specific rules ──
    if model in ("linear", "cubic", "cubic_interp", "linear_interp"):
        # T_ref and sensor physical output must both be temperature
        if not _is_temperature(result.sensor_phys_unit, ureg):
            result.add_error(
                f"Sensor physical unit '{sensor_phys_dsi}' "
                f"(siunitx: {_unit_lx(result.sensor_phys_unit, ureg)}) "
                f"is not a temperature. "
                f"For model '{model}', T (output) must have [temperature] dimensionality."
            )
        # T_ref and T must be mutually convertible (same temperature family)
        if (
            result.ok
            and _is_temperature(result.sensor_phys_unit, ureg)
            and _is_temperature(result.ref_phys_unit, ureg)
        ):
            # degC and kelvin are both [temperature] — convertible via offset
            # degF is also [temperature] — all fine.  Warn if they differ.
            if result.sensor_phys_unit != result.ref_phys_unit:
                result.add_warning(
                    f"Sensor physical unit '{result.sensor_phys_unit}' differs from "
                    f"reference physical unit '{result.ref_phys_unit}'. "
                    "Conversion will be applied in convert_result()."
                )

        # For linear/cubic regression: A is dimensionless (LSB/LSB), B has unit of T_ref.
        # For interpolation models: node weights are dimensionless; output has unit of T_ref.
        # Both cases are enforced by the algorithms; we just document them here.

    elif model == "cube-log":
        # T_ref must be temperature (Kelvin-convertible for Steinhart-Hart 1/T)
        if _is_temperature(result.ref_phys_unit, ureg):
            # Steinhart-Hart works with 1/T [K^-1].
            # If ref unit is degC that's fine — we add 273.15 internally.
            pass
        else:
            result.add_error(
                f"Reference physical unit '{ref_phys_dsi}' is not a temperature. "
                "Steinhart-Hart model requires T_ref in Kelvin (or convertible, e.g. °C)."
            )

    return result


# ---------------------------------------------------------------------------
# Public API — convert_result
# ---------------------------------------------------------------------------


def convert_result(
    calib_result: Dict[str, Any],
    sensor_json: Dict[str, Any],
    ref_json: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Attempt to express numeric calibration results in the preferred output
    unit from the sensor JSON ``unit`` field (e.g. convert LSB offsets to °C,
    or °C values to K if the sensor declares ``\\kelvin``).

    This function is purely additive: it returns a *new* dict that contains
    all keys from ``calib_result`` plus:

    - ``"units"``: a sub-dict mapping result key -> pint unit string used
    - ``"converted"``: a sub-dict with converted scalar values (floats)
    - ``"conversion_errors"``: list of strings for any key that failed

    The original keys in ``calib_result`` are never modified.

    Parameters
    ----------
    calib_result : dict
        Output dict from one of the calibration engines.
    sensor_json : dict
        Full parsed content of the sensor model JSON.
    ref_json : dict
        Full parsed content of the reference calibrator JSON.

    Returns
    -------
    dict
        Shallow copy of calib_result with the three added keys.
    """
    out = dict(calib_result)
    out["units"] = {}
    out["converted"] = {}
    out["conversion_errors"] = []

    ureg = _get_pint_ureg()
    if ureg is None:
        out["conversion_errors"].append("pint not installed — conversion skipped.")
        return out

    # Determine target output unit from sensor JSON ``unit`` field
    sensor_unit_dsi = sensor_json.get("unit", "\\degreeCelsius")
    _dummy = UnitCheckResult()
    target_unit = _dsi_to_pint_name(sensor_unit_dsi, "sensor.unit", _dummy)
    if not target_unit:
        target_unit = "degC"

    # Determine source unit (what the calibration engine works in).
    # The engines produce ref_temp_means and expanded_uncertainties in the same
    # physical unit as lsb_scale_sensor_info (minPhysVal / maxPhysVal), which is
    # whatever unit the sensor JSON declares in ranges.phys.dsi (default: degC).
    _src_dsi = (
        sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    )
    source_unit_temperature = _DSI_TO_PINT.get(_src_dsi.strip(), "degC")

    # Lx (siunitx) representation of the target unit for use in ``out["units"]``
    target_unit_lx = _unit_lx(target_unit, ureg)

    lsb_per_c: float = float(calib_result.get("lsb_per_c", 1.0))
    model: str = str(calib_result.get("model", "linear"))

    def _try_convert(value: float, from_unit: str, to_unit: str, key: str):
        """Convert a scalar and record result; return (converted_value, success)."""
        try:
            q = ureg.Quantity(value, from_unit)
            q_conv = q.to(to_unit)
            return float(q_conv.magnitude), True
        except Exception as exc:
            out["conversion_errors"].append(f"{key}: {exc}")
            return value, False

    # ── ref_temp_means: list[float] in °C -> target ──
    ref_means = calib_result.get("ref_temp_means", [])
    if ref_means:
        converted_means = []
        for i, v in enumerate(ref_means):
            cv, ok = _try_convert(v, source_unit_temperature, target_unit, f"ref_temp_means[{i}]")
            converted_means.append(cv)
        out["converted"]["ref_temp_means"] = converted_means
        out["units"]["ref_temp_means"] = target_unit_lx

    # ── expanded_uncertainties: list[float] in °C -> target ──
    # Uncertainties are *differences* (delta-temperature), not absolute temperatures.
    # For degC <-> kelvin the magnitude is the same (1 K = 1 °C difference).
    # For degF 1 °C = 1.8 °F.  We convert as delta.
    exp_unc = calib_result.get("expanded_uncertainties", [])
    if exp_unc:
        # Use delta conversion (multiply by scale factor only, no offset)
        try:
            delta_factor = ureg.Quantity(1.0, source_unit_temperature).to(target_unit, "delta").magnitude
        except Exception:
            delta_factor = 1.0
        out["converted"]["expanded_uncertainties"] = [float(v) * delta_factor for v in exp_unc]
        out["units"]["expanded_uncertainties"] = target_unit_lx

    # ── Model-specific coefficient conversions ──
    if model == "linear":
        # B [LSB] -> target temperature: B_degC = B / lsb_per_c, then convert
        B_degc = calib_result.get("B", 0.0) / lsb_per_c
        cv, _ = _try_convert(B_degc, source_unit_temperature, target_unit, "B")
        out["converted"]["B"] = cv
        out["units"]["B"] = target_unit_lx

        u_B_degc = calib_result.get("u_B", 0.0) / lsb_per_c
        try:
            df = ureg.Quantity(1.0, source_unit_temperature).to(target_unit, "delta").magnitude
        except Exception:
            df = 1.0
        out["converted"]["u_B"] = u_B_degc * df
        out["units"]["u_B"] = target_unit_lx

        # A is dimensionless — no conversion needed
        out["converted"]["A"] = calib_result.get("A", 0.0)
        out["units"]["A"] = _unit_lx("dimensionless", ureg)

    elif model == "cubic":
        # Coefficients a0..a3 are in LSB; a0 has unit [temperature in LSB],
        # a1 is [dimensionless], a2 is [1/LSB], a3 is [1/LSB²].
        # We convert a0 (offset) to target temperature unit.
        a0_lsb = calib_result.get("a0", 0.0)
        a0_degc = a0_lsb / lsb_per_c
        cv, _ = _try_convert(a0_degc, source_unit_temperature, target_unit, "a0")
        out["converted"]["a0"] = cv
        out["units"]["a0"] = target_unit_lx
        # a1..a3 are scale factors — record as-is with note
        for k in ("a1", "a2", "a3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = "LSB / LSB^k (dimensionless polynomial coefficient)"

    elif model == "cube-log":
        # C0, C1, C3 are in K^-1 already (Steinhart-Hart coefficients).
        # Target for display might be K^-1 (no change) or other — just echo.
        _k_inv_lx = _unit_lx("1/kelvin", ureg)
        for k in ("C0", "C1", "C3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = _k_inv_lx
        # u_C0..u_C3 same
        for k in ("u_C0", "u_C1", "u_C3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = _k_inv_lx

    elif model in ("cubic_interp", "linear_interp"):
        # y_nodes are already in °C (physical units), x_nodes are in LSB (dimensionless).
        # Apply the delta conversion factor for the target unit (1 for degC→degC, etc.).
        try:
            delta_factor = ureg.Quantity(1.0, source_unit_temperature).to(
                target_unit, "delta"
            ).magnitude
        except Exception:
            delta_factor = 1.0

        y_nodes = calib_result.get("y_nodes", [])
        if y_nodes:
            # y_nodes are in °C; convert to target unit using delta factor
            converted_nodes = [float(v) * delta_factor for v in y_nodes]
            out["converted"]["y_nodes"] = converted_nodes
            out["units"]["y_nodes"] = target_unit_lx

        # x_nodes are dimensionless (LSB counts) — echo as-is
        x_nodes = calib_result.get("x_nodes", [])
        if x_nodes:
            out["converted"]["x_nodes"] = list(x_nodes)
            out["units"]["x_nodes"] = _unit_lx("dimensionless", ureg)

        # RMSE and u_H are already in °C (physical unit) — apply delta factor
        try:
            delta_factor = ureg.Quantity(1.0, source_unit_temperature).to(
                target_unit, "delta"
            ).magnitude
        except Exception:
            delta_factor = 1.0
        for k in ("rmse_degC", "u_H_degC"):
            v = calib_result.get(k)
            if v is not None:
                out["converted"][k] = float(v) * delta_factor
                out["units"][k] = target_unit_lx

    return out


# ---------------------------------------------------------------------------
# Standalone test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json, sys
    from pathlib import Path

    # scripts/model_calibration/ -> scripts/ -> calibration/
    calib_root = Path(__file__).resolve().parent.parent.parent
    sensor_path = calib_root / "models_in" / "ntc_temperature.json"
    ref_path    = calib_root / "models_in" / "fluke_9142.json"

    sensor_json = json.loads(sensor_path.read_text(encoding="utf-8"))
    ref_json    = json.loads(ref_path.read_text(encoding="utf-8"))

    for model in ("linear", "cubic", "cube-log", "cubic_interp", "linear_interp"):
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        r = check_dsi(sensor_json, ref_json, model)
        r.print_report()

    # Test a bad ref (pressure)
    bad_ref = {"ranges": {"phys": {"dsi": "\\pascal"}}}
    bad_sensor = {"ranges": {"phys": {"dsi": "\\degreeCelsius"}, "elec": {"dsi": "\\one"}}}
    print("\n--- Bad ref (pascal instead of temperature) ---")
    r2 = check_dsi(bad_sensor, bad_ref, "linear")
    r2.print_report()
