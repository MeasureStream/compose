from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import pint as _pint
    _UREG = _pint.UnitRegistry()
except ImportError:
    _pint = None
    _UREG = None


# ---------------------------------------------------------------------------
# Exception mappings
# ---------------------------------------------------------------------------

_DSI_EXCEPTIONS: Dict[str, str] = {
    "\\degreeCelsius":    "degC",
    "\\degreeFahrenheit": "degF",
    "\\one":              "dimensionless",
}

_PINT_EXCEPTIONS: Dict[str, str] = {v: k for k, v in _DSI_EXCEPTIONS.items()}


# ---------------------------------------------------------------------------
# DSI <-> pint name conversion
# ---------------------------------------------------------------------------

def _dsi_to_pint_name(dsi: str) -> str:
    dsi = dsi.strip()

    parts = dsi.split("\\per")
    converted: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in _DSI_EXCEPTIONS:
            converted.append(_DSI_EXCEPTIONS[part])
        else:
            name = part.lstrip("\\")
            if name:
                converted.append(name)
    return " / ".join(converted) if converted else "dimensionless"


def _validate_pint_name(
    pint_name: str,
    label: str,
    result: UnitCheckResult,
) -> str:
    if _UREG is None or pint_name == "dimensionless":
        return pint_name
    try:
        _UREG.Unit(pint_name)
        return pint_name
    except Exception:
        result.add_warning(
            f"{label}: unrecognised unit '{pint_name}'. "
            "Treated as dimensionless."
        )
        return "dimensionless"


def _pint_to_dsi(pint_name: str) -> str:
    parts = pint_name.split(" / ")
    converted = []
    for part in parts:
        part = part.strip()
        if part in _PINT_EXCEPTIONS:
            converted.append(_PINT_EXCEPTIONS[part])
        else:
            if not part.startswith("\\"):
                part = "\\" + part
            converted.append(part)
    return "\\per".join(converted)


# ---------------------------------------------------------------------------
# Unit formatting helpers
# ---------------------------------------------------------------------------

def _unit_lx(unit_name: str) -> str:
    if _UREG is None:
        return unit_name
    try:
        return f"{_UREG.Unit(unit_name):Lx}"
    except Exception:
        return unit_name


def _dimensionality_str(quantity) -> str:
    return str(quantity.dimensionality)


def _is_temperature(unit_name: str) -> bool:
    if _UREG is None:
        return False
    try:
        q = _UREG.Quantity(1.0, unit_name)
        return q.dimensionality == _UREG.degC.dimensionality
    except Exception:
        return False


def _is_dimensionless(unit_name: str) -> bool:
    if _UREG is None:
        return False
    try:
        q = _UREG.Quantity(1.0, unit_name)
        return q.dimensionless
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class UnitCheckResult:

    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sensor_phys_unit: str = ""
    sensor_elec_unit: str = ""
    ref_phys_unit: str = ""

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
# Public API — dsi_to_symbol, dsi_to_xml_unit
# ---------------------------------------------------------------------------

def dsi_to_symbol(dsi: str) -> str:
    dsi = dsi.strip()
    pint_name = _dsi_to_pint_name(dsi)
    if _UREG is not None:
        try:
            return f"{_UREG.Unit(pint_name):~P}"
        except Exception:
            pass
    return dsi.lstrip("\\")


def dsi_to_xml_unit(dsi: str) -> str:
    pint_name = _dsi_to_pint_name(dsi.strip())
    return _pint_to_dsi(pint_name).lower()


# ---------------------------------------------------------------------------
# Public API — check_dsi
# ---------------------------------------------------------------------------

def check_dsi(
    sensor_json: Dict[str, Any],
    ref_json: Dict[str, Any],
    model: str,
) -> UnitCheckResult:
    result = UnitCheckResult()

    if _UREG is None:
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

    sensor_phys_unit = _validate_pint_name(
        _dsi_to_pint_name(sensor_phys_dsi), "sensor.ranges.phys.dsi", result
    )
    sensor_elec_unit = _validate_pint_name(
        _dsi_to_pint_name(sensor_elec_dsi), "sensor.ranges.elec.dsi", result
    )
    ref_phys_unit = _validate_pint_name(
        _dsi_to_pint_name(ref_phys_dsi), "ref.ranges.phys.dsi", result
    )

    result.sensor_phys_unit = sensor_phys_unit or "degC"
    result.sensor_elec_unit = sensor_elec_unit or "dimensionless"
    result.ref_phys_unit    = ref_phys_unit    or "degC"

    # ── Check 1: reference output must be a temperature ──
    if not _is_temperature(result.ref_phys_unit):
        result.add_error(
            f"Reference calibrator physical unit '{ref_phys_dsi}' "
            f"(siunitx: {_unit_lx(result.ref_phys_unit)}) "
            f"is not a temperature. "
            f"Dimensionality: {_dimensionality_str(_UREG.Quantity(1.0, result.ref_phys_unit))}. "
            f"Expected: [temperature] (e.g. \\degreeCelsius or \\kelvin)."
        )

    # ── Check 2: sensor electrical output must be dimensionless ──
    if not _is_dimensionless(result.sensor_elec_unit):
        result.add_error(
            f"Sensor electrical unit '{sensor_elec_dsi}' "
            f"(siunitx: {_unit_lx(result.sensor_elec_unit)}) "
            f"is not dimensionless. "
            f"The calibration model requires D (raw ADC count) to be dimensionless (\\one). "
            f"Got dimensionality: {_dimensionality_str(_UREG.Quantity(1.0, result.sensor_elec_unit))}."
        )

    # ── Check 3: model-specific rules ──
    if model in ("linear", "cubic", "cubic_interp", "linear_interp"):
        if not _is_temperature(result.sensor_phys_unit):
            result.add_error(
                f"Sensor physical unit '{sensor_phys_dsi}' "
                f"(siunitx: {_unit_lx(result.sensor_phys_unit)}) "
                f"is not a temperature. "
                f"For model '{model}', T (output) must have [temperature] dimensionality."
            )
        if (
            result.ok
            and _is_temperature(result.sensor_phys_unit)
            and _is_temperature(result.ref_phys_unit)
        ):
            if result.sensor_phys_unit != result.ref_phys_unit:
                result.add_warning(
                    f"Sensor physical unit '{result.sensor_phys_unit}' differs from "
                    f"reference physical unit '{result.ref_phys_unit}'. "
                    "Conversion will be applied in convert_result()."
                )

    elif model == "cube-log":
        if not _is_temperature(result.ref_phys_unit):
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
    out = dict(calib_result)
    out["units"] = {}
    out["converted"] = {}
    out["conversion_errors"] = []

    if _UREG is None:
        out["conversion_errors"].append("pint not installed — conversion skipped.")
        return out

    sensor_unit_dsi = sensor_json.get("unit", "\\degreeCelsius")
    _dummy = UnitCheckResult()
    target_unit = _validate_pint_name(
        _dsi_to_pint_name(sensor_unit_dsi), "sensor.unit", _dummy
    )
    if not target_unit or target_unit == "dimensionless":
        target_unit = "degC"

    _src_dsi = (
        sensor_json.get("ranges", {}).get("phys", {}).get("dsi", "\\degreeCelsius")
    )
    source_unit_temperature = _validate_pint_name(
        _dsi_to_pint_name(_src_dsi.strip()), "sensor.ranges.phys.dsi", _dummy
    )
    if not source_unit_temperature or source_unit_temperature == "dimensionless":
        source_unit_temperature = "degC"

    target_unit_lx = _unit_lx(target_unit)

    lsb_per_c: float = float(calib_result.get("lsb_per_c", 1.0))
    model: str = str(calib_result.get("model", "linear"))

    def _try_convert(value: float, from_unit: str, to_unit: str, key: str):
        try:
            q = _UREG.Quantity(value, from_unit)
            q_conv = q.to(to_unit)
            return float(q_conv.magnitude), True
        except Exception as exc:
            out["conversion_errors"].append(f"{key}: {exc}")
            return value, False

    # ── ref_temp_means ──
    ref_means = calib_result.get("ref_temp_means", [])
    if ref_means:
        converted_means = []
        for i, v in enumerate(ref_means):
            cv, ok = _try_convert(v, source_unit_temperature, target_unit, f"ref_temp_means[{i}]")
            converted_means.append(cv)
        out["converted"]["ref_temp_means"] = converted_means
        out["units"]["ref_temp_means"] = target_unit_lx

    # ── expanded_uncertainties ──
    exp_unc = calib_result.get("expanded_uncertainties", [])
    if exp_unc:
        try:
            delta_factor = _UREG.Quantity(1.0, source_unit_temperature).to(target_unit, "delta").magnitude
        except Exception:
            delta_factor = 1.0
        out["converted"]["expanded_uncertainties"] = [float(v) * delta_factor for v in exp_unc]
        out["units"]["expanded_uncertainties"] = target_unit_lx

    # ── Model-specific coefficient conversions ──
    if model == "linear":
        B_degc = calib_result.get("B", 0.0) / lsb_per_c
        cv, _ = _try_convert(B_degc, source_unit_temperature, target_unit, "B")
        out["converted"]["B"] = cv
        out["units"]["B"] = target_unit_lx

        u_B_degc = calib_result.get("u_B", 0.0) / lsb_per_c
        try:
            df = _UREG.Quantity(1.0, source_unit_temperature).to(target_unit, "delta").magnitude
        except Exception:
            df = 1.0
        out["converted"]["u_B"] = u_B_degc * df
        out["units"]["u_B"] = target_unit_lx

        out["converted"]["A"] = calib_result.get("A", 0.0)
        out["units"]["A"] = _unit_lx("dimensionless")

    elif model == "cubic":
        a0_lsb = calib_result.get("a0", 0.0)
        a0_degc = a0_lsb / lsb_per_c
        cv, _ = _try_convert(a0_degc, source_unit_temperature, target_unit, "a0")
        out["converted"]["a0"] = cv
        out["units"]["a0"] = target_unit_lx
        for k in ("a1", "a2", "a3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = "LSB / LSB^k (dimensionless polynomial coefficient)"

    elif model == "cube-log":
        _k_inv_lx = _unit_lx("1/kelvin")
        for k in ("C0", "C1", "C3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = _k_inv_lx
        for k in ("u_C0", "u_C1", "u_C3"):
            out["converted"][k] = calib_result.get(k, 0.0)
            out["units"][k] = _k_inv_lx

    elif model in ("cubic_interp", "linear_interp"):
        try:
            delta_factor = _UREG.Quantity(1.0, source_unit_temperature).to(
                target_unit, "delta"
            ).magnitude
        except Exception:
            delta_factor = 1.0

        y_nodes = calib_result.get("y_nodes", [])
        if y_nodes:
            out["converted"]["y_nodes"] = [float(v) * delta_factor for v in y_nodes]
            out["units"]["y_nodes"] = target_unit_lx

        x_nodes = calib_result.get("x_nodes", [])
        if x_nodes:
            out["converted"]["x_nodes"] = list(x_nodes)
            out["units"]["x_nodes"] = _unit_lx("dimensionless")

        try:
            delta_factor = _UREG.Quantity(1.0, source_unit_temperature).to(
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
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    from pathlib import Path

    print("=" * 72)
    print("UNIT CHECKS — Self-Test Suite")
    print("=" * 72)

    fail_count = 0

    # ── Part 1: DSI -> pint auto-conversion ──
    print("\n--- Part 1: DSI -> pint auto-conversion ---")

    EXPECTED: Dict[str, str] = {
        "\\kelvin":      "kelvin",
        "\\meter":       "meter",
        "\\second":      "second",
        "\\kilogram":    "kilogram",
        "\\ampere":      "ampere",
        "\\mole":        "mole",
        "\\candela":     "candela",
        "\\pascal":      "pascal",
        "\\bar":         "bar",
        "\\coulomb":     "coulomb",
        "\\volt":        "volt",
        "\\ohm":         "ohm",
        "\\hertz":       "hertz",
        "\\watt":        "watt",
        "\\radian":      "radian",
        "\\newton":      "newton",
        "\\joule":       "joule",
        "\\farad":       "farad",
        "\\siemens":     "siemens",
        "\\weber":       "weber",
        "\\tesla":       "tesla",
        "\\henry":       "henry",
        "\\degreeCelsius":    "degC",
        "\\degreeFahrenheit": "degF",
        "\\one":              "dimensionless",
        "\\kelvin\\per\\second":              "kelvin / second",
        "\\second\\per\\kelvin":              "second / kelvin",
        "\\meter\\per\\second":               "meter / second",
        "\\degreeCelsius\\per\\second":        "degC / second",
        "\\second\\per\\degreeCelsius":        "second / degC",
        "\\kilometer":   "kilometer",
        "\\kilohertz":   "kilohertz",
        "\\kilowatt":    "kilowatt",
        "\\kilovolt":    "kilovolt",
        "\\kilopascal":  "kilopascal",
        "\\megahertz":   "megahertz",
        "\\megawatt":    "megawatt",
        "\\megavolt":    "megavolt",
        "\\megapascal":  "megapascal",
        "\\megaohm":     "megaohm",
        "\\microsecond": "microsecond",
        "\\micrometer":  "micrometer",
        "\\microvolt":   "microvolt",
        "\\microampere": "microampere",
        "\\microfarad":  "microfarad",
        "\\millimeter":  "millimeter",
        "\\millisecond": "millisecond",
        "\\millivolt":   "millivolt",
        "\\milliampere": "milliampere",
        "\\milliwatt":   "milliwatt",
        "\\nanosecond":  "nanosecond",
        "\\nanometer":   "nanometer",
        "\\nanovolt":    "nanovolt",
        "\\nanoampere":  "nanoampere",
        "\\nanofarad":   "nanofarad",
        "\\gigahertz":   "gigahertz",
        "\\gigawatt":    "gigawatt",
        "\\gigavolt":    "gigavolt",
        "\\gigaohm":     "gigaohm",
        "\\terahertz":   "terahertz",
        "\\terawatt":    "terawatt",
        "\\centimeter":  "centimeter",
        "\\decimeter":   "decimeter",
        "\\kilometer\\per\\second":       "kilometer / second",
        "\\millivolt\\per\\kelvin":       "millivolt / kelvin",
        "\\microsecond\\per\\kelvin":      "microsecond / kelvin",
        "\\megawatt\\per\\meter":         "megawatt / meter",
    }

    for dsi, expected in EXPECTED.items():
        got = _dsi_to_pint_name(dsi)
        if got != expected:
            print(f"  FAIL: {dsi:45s} -> '{got}'   expected '{expected}'")
            fail_count += 1
    print(f"  {len(EXPECTED)} DSI->pint conversions tested")

    # ── Part 2: pint Unit(name) validation ──
    print("\n--- Part 2: pint Unit(name) validation ---")
    ok2 = 0
    for dsi in EXPECTED:
        pname = _dsi_to_pint_name(dsi)
        if pname == "dimensionless":
            ok2 += 1
            continue
        try:
            _UREG.Unit(pname)
            ok2 += 1
        except Exception as exc:
            print(f"  FAIL: pint does not recognise '{pname}' (from {dsi}): {exc}")
            fail_count += 1
    print(f"  {ok2}/{len(EXPECTED)} unit names valid in pint")

    # ── Part 3: pint -> DSI reverse conversion ──
    print("\n--- Part 3: pint -> DSI reverse conversion ---")
    REVERSE_EXEMPT = {
        "\\kelvin\\per\\second": True,
        "\\second\\per\\kelvin": True,
        "\\meter\\per\\second": True,
        "\\degreeCelsius\\per\\second": True,
        "\\second\\per\\degreeCelsius": True,
    }
    for dsi in EXPECTED:
        pname = _dsi_to_pint_name(dsi)
        back = _pint_to_dsi(pname)
        if dsi in REVERSE_EXEMPT:
            if back.lower() != dsi.lower():
                print(f"  WARN (compound): {dsi} -> {pname} -> {back}")
        else:
            if back != dsi:
                print(f"  FAIL: {dsi} -> {pname} -> {back}")
                fail_count += 1
    print(f"  Reverse-mapping done")

    # ── Part 4: pint ~P symbol formatting ──
    def _safe_print(msg: str) -> None:
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", "backslashreplace").decode("ascii"))

    print("\n--- Part 4: pint ~P symbol formatting ---")
    SYMBOL_TEST = [
        "degC", "kelvin", "degF", "dimensionless",
        "meter", "second", "kilogram", "ampere", "mole", "candela",
        "pascal", "bar", "coulomb", "volt", "ohm", "hertz", "watt", "radian",
        "newton", "joule", "farad", "siemens", "weber", "tesla", "henry",
        "kilometer", "kilohertz", "kilowatt", "kilovolt", "kilopascal",
        "megahertz", "megawatt", "megavolt", "megapascal", "megaohm",
        "microsecond", "micrometer", "microvolt", "microampere", "microfarad",
        "millimeter", "millisecond", "millivolt", "milliampere", "milliwatt",
        "nanosecond", "nanometer", "nanovolt", "nanoampere", "nanofarad",
        "gigahertz", "gigawatt", "gigavolt", "gigaohm",
        "terahertz", "terawatt",
        "centimeter", "decimeter",
        "meter / second", "kilometer / second", "kelvin / second",
        "degC / second", "1 / kelvin",
    ]
    for name in SYMBOL_TEST:
        try:
            sym = f"{_UREG.Unit(name):~P}"
            _safe_print(f"  {name:25s} -> {sym}")
        except Exception as exc:
            print(f"  {name:25s} -> ERROR: {exc}")
            fail_count += 1
    print(f"  {len(SYMBOL_TEST)} symbols formatted via pint ~P")

    # ── Part 5: pint Lx (siunitx LaTeX) formatting ──
    print("\n--- Part 5: pint Lx (siunitx LaTeX) formatting ---")
    for name in SYMBOL_TEST:
        try:
            lx = f"{_UREG.Unit(name):Lx}"
            _safe_print(f"  {name:25s} -> {lx}")
        except Exception as exc:
            print(f"  {name:25s} -> ERROR: {exc}")
            fail_count += 1
    print(f"  {len(SYMBOL_TEST)} LaTeX strings formatted via pint Lx")

    # ── Part 6: dsi_to_symbol / dsi_to_xml_unit ──
    print("\n--- Part 6: dsi_to_symbol / dsi_to_xml_unit ---")

    print("  dsi_to_symbol:")
    for dsi in [
        "\\degreeCelsius", "\\kelvin", "\\degreeFahrenheit", "\\one",
        "\\pascal", "\\volt", "\\ohm", "\\watt", "\\hertz", "\\newton",
        "\\kilometer", "\\megahertz", "\\microsecond", "\\millivolt",
        "\\nanometer", "\\gigawatt", "\\terahertz",
    ]:
        sym = dsi_to_symbol(dsi)
        _safe_print(f"    {dsi:30s} -> {sym}")

    print("  dsi_to_xml_unit:")
    for dsi in [
        "\\degreeCelsius", "\\kelvin", "\\degreeFahrenheit", "\\one",
        "\\pascal", "\\volt", "\\ohm", "\\watt",
        "\\meter\\per\\second", "\\degreeCelsius\\per\\second",
    ]:
        xml = dsi_to_xml_unit(dsi)
        _safe_print(f"    {dsi:35s} -> {xml}")

    # ── Part 7: check_dsi with real model JSONs ──
    print("\n--- Part 7: check_dsi with real model JSONs ---")
    calib_root = Path(__file__).resolve().parent.parent.parent
    sensor_path = calib_root / "models_in" / "ntc_temperature.json"
    ref_path    = calib_root / "models_in" / "fluke_9142.json"

    sensor_json = json.loads(sensor_path.read_text(encoding="utf-8"))
    ref_json    = json.loads(ref_path.read_text(encoding="utf-8"))

    def _safe_print_report(r: UnitCheckResult, prefix: str) -> None:
        try:
            r.print_report(prefix=prefix)
        except UnicodeEncodeError:
            for w in r.warnings:
                _safe_print(f"{prefix} WARNING: {w}")
            for e in r.errors:
                _safe_print(f"{prefix} ERROR:   {e}")
            if r.ok:
                _safe_print(f"{prefix} PASS — all dimensional checks passed.")
            else:
                _safe_print(f"{prefix} FAIL — {len(r.errors)} error(s), calibration blocked.")

    for model in ("linear", "cubic", "cube-log", "cubic_interp", "linear_interp"):
        print(f"\n  Model: {model}")
        r = check_dsi(sensor_json, ref_json, model)
        _safe_print_report(r, "    [unit-check]")
        if not r.ok:
            fail_count += 1

    print("\n  Edge case: bad ref (pascal instead of temperature)")
    bad_ref = {"ranges": {"phys": {"dsi": "\\pascal"}}}
    bad_sensor = {"ranges": {"phys": {"dsi": "\\degreeCelsius"}, "elec": {"dsi": "\\one"}}}
    r2 = check_dsi(bad_sensor, bad_ref, "linear")
    _safe_print_report(r2, "    [unit-check]")

    print("\n  Edge case: pressure sensor + bar ref (same dimensionality)")
    press_sensor = {"ranges": {"phys": {"dsi": "\\pascal"}, "elec": {"dsi": "\\one"}}}
    press_ref = {"ranges": {"phys": {"dsi": "\\bar"}}}
    r3 = check_dsi(press_sensor, press_ref, "linear")
    _safe_print_report(r3, "    [unit-check]")

    print("\n  Edge case: volt sensor + pascal ref (mismatch)")
    volt_sensor = {"ranges": {"phys": {"dsi": "\\volt"}, "elec": {"dsi": "\\one"}}}
    r4 = check_dsi(volt_sensor, press_ref, "linear")
    _safe_print_report(r4, "    [unit-check]")

    # ── Part 8: convert_result smoke test ──
    print("\n--- Part 8: convert_result smoke test ---")
    dummy_calib = {
        "model": "linear",
        "A": 0.0,
        "B": 100.0,
        "u_B": 0.5,
        "lsb_per_c": 1.0,
        "ref_temp_means": [20.0, 30.0, 40.0],
        "expanded_uncertainties": [0.1, 0.1, 0.1],
    }
    out = convert_result(dummy_calib, sensor_json, ref_json)
    print(f"  converted keys: {sorted(out['converted'].keys())}")
    print(f"  units: { {k: v for k, v in out['units'].items()} }")
    if out["conversion_errors"]:
        print(f"  conversion errors: {out['conversion_errors']}")
        fail_count += 1
    else:
        print("  OK — no conversion errors")

    # ── Summary ──
    print("\n" + "=" * 72)
    if fail_count == 0:
        print("ALL CHECKS PASSED")
    else:
        print(f"{fail_count} FAILURE(S)")
    print("=" * 72)
