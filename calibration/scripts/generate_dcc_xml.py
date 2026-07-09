from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Dict, List
import xml.etree.ElementTree as ET


DEFAULT_INPUT_JSON = Path(__file__).with_name("certificato-void-filled.json")
DEFAULT_OUTPUT_XML = Path(__file__).with_name("ntc_calibration_certificate.xml")

NS = {
    "dcc": "https://ptb.de/dcc",
    "si": "https://ptb.de/si",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def _require_keys(data: Dict[str, Any], keys: List[str], scope: str) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"Missing keys in {scope}: {', '.join(missing)}")


def _expand_template_parts(parts: Dict[str, Any]) -> Dict[str, Any]:
    _require_keys(
        parts,
        [
            "company_data",
            "organization_data",
            "sensor_method_template",
            "calibration_specific_data",
            "calculated_calibration_values",
        ],
        "template_parts",
    )

    company = parts["company_data"]
    organization = parts["organization_data"]
    sensor = parts["sensor_method_template"]
    calibration = parts["calibration_specific_data"]
    calculated = parts["calculated_calibration_values"]

    # RespPersons: build a deduplicated, non-empty list.
    # Priority: explicit organization_data.resp_persons (list of dicts or strings)
    # > (authorised_by, executed_by, signature_name) fallback tuple.
    raw_resp = organization.get("resp_persons")
    if isinstance(raw_resp, list) and raw_resp:
        resp_persons: List[str] = []
        for item in raw_resp:
            if isinstance(item, dict):
                name = item.get("name") or item.get("full_name") or ""
            else:
                name = str(item)
            name = (name or "").strip()
            if name and not (name.startswith("<") and name.endswith(">")) and name not in resp_persons:
                resp_persons.append(name)
    else:
        resp_persons = []
    for legacy in (organization.get("authorised_by"),
                   organization.get("executed_by"),
                   organization.get("signature_name")):
        s = (str(legacy or "")).strip()
        if s and not (s.startswith("<") and s.endswith(">")) and s not in resp_persons:
            resp_persons.append(s)
    if not resp_persons:
        resp_persons = ["Responsible person 1"]

    cert = {
        "certificate_title": calibration["certificate_title"],
        "certificate_number": calibration["certificate_number"],
        "issue_date": calibration.get("issue_date", ""),
        "customer": calibration.get("customer", ""),
        "request_date": calibration.get("request_date", ""),
        "request_number": calibration.get("request_number", ""),
        "receipt_date": calibration.get("receipt_date", ""),
        "measurement_dates": calibration.get("measurement_dates", ""),
        "measurement_date_start": calibration.get("measurement_date_start", ""),
        "measurement_date_end": calibration.get("measurement_date_end", ""),
        "item": sensor["item"],
        "device_type": sensor["device_type"],
        "manufacturer": sensor["manufacturer"],
        "model": sensor["model"],
        "serial_number": sensor["serial_number"],
        "asset_id": calibration.get("asset_id", ""),
        "lab_reference": calibration.get("lab_reference", ""),
        "procedure_code": sensor.get("procedure_code", ""),
        "calibration_method": sensor.get("calibration_method", ""),
        "traceability": sensor.get("traceability", ""),
        "conditions": calibration.get("conditions", []),
        "environment": calibration.get("environment", {}),
        "country": calibration.get("country", "IT"),
        "authorised_by": organization.get("authorised_by", ""),
        "executed_by": organization.get("executed_by", ""),
        "resp_persons": resp_persons,
        "resp_person_roles": organization.get("resp_person_roles", []),
        "reproduction_conditions": organization.get("reproduction_conditions", ""),
        "traceability_statement": organization.get("traceability_statement", ""),
        "starting_uncertainties": sensor.get("starting_uncertainties", ""),
        "accreditation_code": organization.get("accreditation_code")
            or company.get("accreditation_code", ""),
        "accreditation_body": organization.get("accreditation_body", ""),
        "amends": calibration.get("amends"),
        "supersedes": calibration.get("supersedes"),
        "previous_report": calibration.get("previous_report"),
        "description": sensor.get("description", ""),
        "equipment_class": sensor.get("equipment_class"),
        "firmware": sensor.get("firmware")
            or sensor.get("firmware_version", ""),
        "notes": sensor.get("_notes_computed",
                            sensor.get("notes_template", [])),
    }

    org = {
        "org_name": company["org_name"],
        "address_lines": company["address_lines"],
        "phone": company["phone"],
        "email": company["email"],
        "website": company["website"],
        "fax": company.get("fax", ""),
    }

    return {
        "cert": cert,
        "org": org,
        "measurements": calculated["measurements"],
        "ntc_model": sensor.get("ntc_model", {}),
    }


def load_input_data(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))

    if "template_parts" in raw:
        data = _expand_template_parts(raw["template_parts"])
        # Propagate the per-step uncertainty budget (computed by the orchestrator
        # and stored outside template_parts at the root of the filled JSON).
        calib_result = raw.get("_calibration_result", {})
        data["_u_budget_per_step"] = calib_result.get("_u_budget_per_step", [])
        data["_rmse"] = calib_result.get("_rmse", 0.0)
        data["_calib_model"] = calib_result.get("_calib_model", "linear")
        data["_ref_instrument"] = calib_result.get("_ref_instrument", {})
        data["_conformity"] = calib_result.get("_conformity", {})
        data["_sensor_schema_version"] = calib_result.get("_sensor_schema_version", "")
        data["_ref_schema_version"] = calib_result.get("_ref_schema_version", "")
        # Calibration coefficients (+ their standard uncertainties) for the
        # method statement. Covers all four procedures: linear (A,B),
        # cubic/quadratic (a0..a3), Steinhart-Hart (a,b,c). Uncertainties are
        # always included here even though the PDF hides them — the DCC XML
        # is the authoritative, complete metrological record.
        data["_coeffs"] = {}
        for k in (
            "_A", "_B", "_u_A", "_u_B", "_cov_AB",
            "_a0", "_a1", "_a2", "_a3",
            "_u_a0", "_u_a1", "_u_a2", "_u_a3",
            "_a", "_b", "_c",
            "_u_a", "_u_b", "_u_c",
        ):
            if k in calib_result:
                data["_coeffs"][k] = calib_result[k]
        # Physical unit DSI for XML unit elements — read from sensor JSON via orchestrator.
        # Default: "\\degreeCelsius" (D-SI case-sensitive).
        data["_phys_unit_dsi"] = calib_result.get("_phys_unit_dsi", "\\degreeCelsius")
    else:
        data = raw

    _require_keys(data, ["cert", "org", "measurements", "ntc_model"], "root")
    return data


def _text(parent: ET.Element, tag: str, value: Any) -> ET.Element:
    elem = ET.SubElement(parent, tag)
    elem.text = "" if value is None else str(value)
    return elem


def _lang_text(parent: ET.Element, text: str, lang: str = "en") -> ET.Element:
    content = ET.SubElement(parent, "{https://ptb.de/dcc}content", {"lang": lang})
    content.text = text
    return content


def _normalize_date(date_text: str) -> str:
    value = (date_text or "").strip()
    if value.startswith("<") and value.endswith(">"):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dZ")
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return f"{value}Z"
    if len(value) == 11 and value.endswith("Z") and value[4] == "-" and value[7] == "-":
        return value
    return datetime.now(timezone.utc).strftime("%Y-%m-%dZ")


def _safe_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or (text.startswith("<") and text.endswith(">")):
        return fallback
    return text


def _safe_lang_text(
    parent: ET.Element, value: Any, fallback: str, lang: str = "en"
) -> ET.Element:
    return _lang_text(parent, _safe_text(value, fallback), lang)


def _is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or (text.startswith("<") and text.endswith(">"))


def _split_date_range(value: str) -> tuple[str, str]:
    """Split a free-form date string into (begin, end) ISO dates.

    Accepts:
      - "YYYY-MM-DD"                          -> (date, date)
      - "YYYY-MM-DD..YYYY-MM-DD" or
        "YYYY-MM-DD — YYYY-MM-DD" or
        "YYYY-MM-DD to YYYY-MM-DD"           -> (begin, end)
      - "YYYY-MM-DD HH:MM"                    -> (date, date)
    Raises ValueError on unparseable input.
    """
    s = (value or "").strip()
    if not s:
        raise ValueError("empty date")
    for sep in ("..", "—", "–", " to ", " - "):
        if sep in s:
            a, b = s.split(sep, 1)
            a, b = a.strip()[:10], b.strip()[:10]
            if len(a) == 10 and len(b) == 10:
                return a, b
    s10 = s[:10]
    if len(s10) == 10 and s10[4] == "-" and s10[7] == "-":
        return s10, s10
    raise ValueError(f"unparseable date: {value!r}")


_DSI_RE = re.compile(r"^\\?[A-Za-z][A-Za-z0-9]*$")


def _split_address_lines(address_lines: List[str]) -> Dict[str, str]:
    """Best-effort split of address_lines into street/postCode/city.

    Italian convention assumed: last line = "10129 Torino, Italy" or "10129 Torino".
    Falls back to 'street' = first line, 'further' = the rest, when not parseable.
    """
    out: Dict[str, str] = {}
    if not address_lines:
        return out
    street = address_lines[0]
    out["street"] = street
    if len(address_lines) >= 2:
        last = address_lines[-1]
        m = re.match(r"^\s*(\d{4,6})\s+(.+?)(?:,\s*.+)?\s*$", last)
        if m:
            out["postCode"] = m.group(1)
            out["city"] = m.group(2).strip()
        else:
            out["city"] = last
    return out


def _customer_dict(cert_customer: Any) -> Dict[str, Any]:
    """Normalize the cert['customer'] field.

    Accepts a string ("Name | address | city") or a dict (preferred). Returns
    a dict with keys: name, address_lines, city, postCode, country, email, phone.
    """
    if isinstance(cert_customer, dict):
        return {
            "name": str(cert_customer.get("name", "")).strip(),
            "address_lines": list(cert_customer.get("address_lines", []) or []),
            "city": str(cert_customer.get("city", "")).strip(),
            "postCode": str(cert_customer.get("post_code", "")).strip(),
            "country": str(cert_customer.get("country", "IT")).strip() or "IT",
            "email": str(cert_customer.get("email", "")).strip(),
            "phone": str(cert_customer.get("phone", "")).strip(),
        }
    text = str(cert_customer or "").strip()
    parts = [p.strip() for p in text.split("|") if p.strip()]
    if not parts:
        return {"name": "", "address_lines": [], "city": "", "postCode": "",
                "country": "IT", "email": "", "phone": ""}
    return {
        "name": parts[0],
        "address_lines": parts[1:],
        "city": "", "postCode": "", "country": "IT", "email": "", "phone": "",
    }


def _iso_url(value: str) -> str:
    """Best-effort: turn a bare website string into a valid http(s) URI."""
    s = (value or "").strip()
    if not s:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        return s
    return f"https://{s}"


_PRIMARY_REF_TYPES: List[Dict[str, str]] = [
    {
        "refType": "gp_temperatureSensor",
        "name": "Generic Profile — temperature sensor",
        "namespace": "https://measurestream.it/refTypes/gp_temperatureSensor",
        "link": "https://measurestream.it/refTypes/gp_temperatureSensor",
        "release": "1.0.0",
        "procedure": "Comparison method with Pt100/Fluke 1502A reference in a controlled bath/oven.",
    },
    {
        "refType": "gp_measuringResult1",
        "name": "Generic Profile — calibration result #1 (table)",
        "namespace": "https://measurestream.it/refTypes/gp_measuringResult1",
        "link": "https://measurestream.it/refTypes/gp_measuringResult1",
        "release": "1.0.0",
        "procedure": "Per-point T_ref, T_c, M_e_pre, M_e_post, U_exp table.",
    },
    {
        "refType": "gp_table1",
        "name": "Generic Profile — table 1",
        "namespace": "https://measurestream.it/refTypes/gp_table1",
        "link": "https://measurestream.it/refTypes/gp_table1",
        "release": "1.0.0",
    },
    {
        "refType": "gp_measurementErrorPreCalibration",
        "name": "Generic Profile — measurement error before calibration",
        "namespace": "https://measurestream.it/refTypes/gp_measurementErrorPreCalibration",
        "link": "https://measurestream.it/refTypes/gp_measurementErrorPreCalibration",
        "release": "1.0.0",
    },
    {
        "refType": "gp_uncertaintyTypeA_reference",
        "name": "Generic Profile — Type A standard uncertainty (reference)",
        "namespace": "https://measurestream.it/refTypes/gp_uncertaintyTypeA_reference",
        "link": "https://measurestream.it/refTypes/gp_uncertaintyTypeA_reference",
        "release": "1.0.0",
    },
    {
        "refType": "gp_uncertaintyTypeA_sensor",
        "name": "Generic Profile — Type A standard uncertainty (sensor)",
        "namespace": "https://measurestream.it/refTypes/gp_uncertaintyTypeA_sensor",
        "link": "https://measurestream.it/refTypes/gp_uncertaintyTypeA_sensor",
        "release": "1.0.0",
    },
    {
        "refType": "gp_combinedStandardUncertainty",
        "name": "Generic Profile — combined standard uncertainty u_c(E)",
        "namespace": "https://measurestream.it/refTypes/gp_combinedStandardUncertainty",
        "link": "https://measurestream.it/refTypes/gp_combinedStandardUncertainty",
        "release": "1.0.0",
    },
    {
        "refType": "gp_coverageFactor",
        "name": "Generic Profile — coverage factor k",
        "namespace": "https://measurestream.it/refTypes/gp_coverageFactor",
        "link": "https://measurestream.it/refTypes/gp_coverageFactor",
        "release": "1.0.0",
    },
    {
        "refType": "gp_regressionUncertainty",
        "name": "Generic Profile — regression fit uncertainty (RMSE)",
        "namespace": "https://measurestream.it/refTypes/gp_regressionUncertainty",
        "link": "https://measurestream.it/refTypes/gp_regressionUncertainty",
        "release": "1.0.0",
    },
    {
        "refType": "gp_conformityDecisionRule",
        "name": "Generic Profile — conformity decision rule (ILAC-G8)",
        "namespace": "https://measurestream.it/refTypes/gp_conformityDecisionRule",
        "link": "https://measurestream.it/refTypes/gp_conformityDecisionRule",
        "release": "1.0.0",
        "procedure": "Acceptance when |M_e| <= M_AE; guard band MAE - g * U_exp; PFA per point <= threshold.",
    },
]


def _fmt(values: List[float], decimals: int = 6) -> str:
    def _strip(v):
        s = f"{v:.{decimals}f}".rstrip("0")
        if s.endswith("."):
            s = s[:-1]
        return s if s else "0"
    return " ".join(_strip(v) for v in values)


def _extract_center_tolerance(
    text: str, default_center: float, default_tol: float
) -> tuple[float, float]:
    numbers = [float(m) for m in re.findall(r"[-+]?\d+(?:\.\d+)?", text or "")]
    if len(numbers) >= 2:
        return numbers[0], abs(numbers[1])
    if len(numbers) == 1:
        return numbers[0], default_tol
    return default_center, default_tol


def build_dcc_tree(data: Dict[str, Any]) -> ET.ElementTree:
    cert = data["cert"]
    org = data["org"]
    measurements = data["measurements"]

    # Physical unit for all temperature quantity elements in the XML.
    # Read from the calibration result (set by the orchestrator from sensor JSON).
    # Fallback: "\\degreeCelsius" (D-SI case-sensitive).
    phys_unit_dsi: str = data.get("_phys_unit_dsi", "\\degreeCelsius")

    # Row format (funzione, temperature domain):
    #   [point, T_ref, T_c_post, M_e_pre, M_e_post, U_exp]  (unit = phys_unit_dsi)
    # Legacy 5-element rows (no M_e_pre):
    #   [point, T_ref, T_c_post, M_e_post, U_exp]
    rows = []
    for row in measurements:
        if len(row) < 5:
            raise ValueError("Each measurements row must have at least 5 values.")
        if len(row) >= 6:
            rows.append({
                "point":    float(row[0]),
                "t_ref":    float(row[1]),
                "t_c_post": float(row[2]),
                "me_pre":   float(row[3]),
                "me_post":  float(row[4]),
                "u_exp":    float(row[5]),
            })
        else:
            rows.append({
                "point":    float(row[0]),
                "t_ref":    float(row[1]),
                "t_c_post": float(row[2]),
                "me_pre":   None,
                "me_post":  float(row[3]),
                "u_exp":    float(row[4]),
            })

    root = ET.Element(
        "{https://ptb.de/dcc}digitalCalibrationCertificate",
        {
            "schemaVersion": "3.3.0",
            "xmlns:xades": NS["xades"],
            "xmlns:ds": NS["ds"],
        },
    )

    admin = ET.SubElement(root, "{https://ptb.de/dcc}administrativeData")

    dcc_software = ET.SubElement(admin, "{https://ptb.de/dcc}dccSoftware")
    software = ET.SubElement(dcc_software, "{https://ptb.de/dcc}software",
                             {"id": "dcc-gen"})
    software_name = ET.SubElement(software, "{https://ptb.de/dcc}name")
    _lang_text(software_name, "generate_dcc_xml.py", "en")
    _text(software, "{https://ptb.de/dcc}release", "1.0")
    _text(software, "{https://ptb.de/dcc}type", "application")
    sw_desc = ET.SubElement(software, "{https://ptb.de/dcc}description")
    _lang_text(sw_desc, "Generates the DCC XML body from the calibration result JSON.", "en")

    sensor_schema = data.get("_sensor_schema_version", "")
    if sensor_schema:
        sw_sensor = ET.SubElement(dcc_software, "{https://ptb.de/dcc}software",
                                  {"id": "sensor-schema"})
        sw_sensor_name = ET.SubElement(sw_sensor, "{https://ptb.de/dcc}name")
        _lang_text(sw_sensor_name, "Sensor model schema", "en")
        _text(sw_sensor, "{https://ptb.de/dcc}release", sensor_schema)
        _text(sw_sensor, "{https://ptb.de/dcc}type", "library")

    ref_schema = data.get("_ref_schema_version", "")
    if ref_schema:
        sw_ref = ET.SubElement(dcc_software, "{https://ptb.de/dcc}software",
                               {"id": "ref-schema"})
        sw_ref_name = ET.SubElement(sw_ref, "{https://ptb.de/dcc}name")
        _lang_text(sw_ref_name, "Reference model schema", "en")
        _text(sw_ref, "{https://ptb.de/dcc}release", ref_schema)
        _text(sw_ref, "{https://ptb.de/dcc}type", "library")

    # refTypeDefinitions — declare our lab-specific refType vocabulary so external
    # validators can resolve the gp_* URIs instead of flagging them as unknown.
    ref_defs = ET.SubElement(admin, "{https://ptb.de/dcc}refTypeDefinitions")
    for rtd in _PRIMARY_REF_TYPES:
        ref_def = ET.SubElement(
            ref_defs,
            "{https://ptb.de/dcc}refTypeDefinition",
            {"refType": rtd["refType"]},
        )
        rd_name = ET.SubElement(ref_def, "{https://ptb.de/dcc}name")
        _lang_text(rd_name, rtd["name"], "en")
        if rtd.get("procedure"):
            rd_desc = ET.SubElement(ref_def, "{https://ptb.de/dcc}description")
            _lang_text(rd_desc, rtd["procedure"], "en")
        _text(ref_def, "{https://ptb.de/dcc}namespace", rtd["namespace"])
        _text(ref_def, "{https://ptb.de/dcc}link", rtd["link"])
        if rtd.get("release"):
            _text(ref_def, "{https://ptb.de/dcc}release", rtd["release"])

    core = ET.SubElement(admin, "{https://ptb.de/dcc}coreData")
    _text(core, "{https://ptb.de/dcc}countryCodeISO3166_1", cert.get("country", "IT"))
    _text(core, "{https://ptb.de/dcc}usedLangCodeISO639_1", "it")
    _text(core, "{https://ptb.de/dcc}usedLangCodeISO639_1", "en")
    _text(core, "{https://ptb.de/dcc}mandatoryLangCodeISO639_1", "en")
    _text(
        core,
        "{https://ptb.de/dcc}uniqueIdentifier",
        _safe_text(cert["certificate_number"], "DCC-UNSPECIFIED"),
    )
    # coreData/identifications — emit up to 3 entries (lab order, manufacturer
    # serial, customer request number) when the corresponding fields are present.
    core_idents = [
        ("calibrationLaboratory", cert.get("lab_reference", ""), "Order no.", "en"),
        ("manufacturer", cert.get("serial_number", ""), "Serial number", "en"),
        ("customer", cert.get("request_number", ""), "Request number", "en"),
    ]
    core_idents = [t for t in core_idents if t[1] and not _is_placeholder(t[1])]
    if core_idents:
        core_identifications = ET.SubElement(core, "{https://ptb.de/dcc}identifications")
        for issuer, value, name_en, lang in core_idents:
            ci = ET.SubElement(core_identifications, "{https://ptb.de/dcc}identification")
            _text(ci, "{https://ptb.de/dcc}issuer", issuer)
            _text(ci, "{https://ptb.de/dcc}value", value)
            ci_name = ET.SubElement(ci, "{https://ptb.de/dcc}name")
            _lang_text(ci_name, name_en, lang)

    # begin/end performance dates: split if range provided, else use single date.
    try:
        if cert.get("measurement_date_start") and cert.get("measurement_date_end"):
            begin_d = cert["measurement_date_start"][:10]
            end_d = cert["measurement_date_end"][:10]
        else:
            begin_d, end_d = _split_date_range(cert.get("measurement_dates", ""))
    except ValueError:
        begin_d, end_d = _normalize_date(cert.get("measurement_dates", "")), _normalize_date(cert.get("measurement_dates", ""))
    _text(core, "{https://ptb.de/dcc}receiptDate", _normalize_date(cert.get("receipt_date", "")))
    _text(core, "{https://ptb.de/dcc}beginPerformanceDate", begin_d)
    _text(core, "{https://ptb.de/dcc}endPerformanceDate", end_d)
    _text(core, "{https://ptb.de/dcc}performanceLocation", "laboratory")

    # issueDate (xs:date, optional) — schema places it after performanceLocation.
    issue_d = _normalize_date(cert.get("issue_date", ""))
    if issue_d and issue_d != datetime.now(timezone.utc).strftime("%Y-%m-%dZ"):
        _text(core, "{https://ptb.de/dcc}issueDate", issue_d)

    # reportAmendedSubstituted (optional): populate only when explicitly set.
    amend = cert.get("amends") or cert.get("supersedes")
    if isinstance(amend, dict):
        ras = ET.SubElement(core, "{https://ptb.de/dcc}reportAmendedSubstituted")
        _text(ras, "{https://ptb.de/dcc}typeOfChange",
              "amended" if cert.get("amends") else "substituted")
        _text(ras, "{https://ptb.de/dcc}replacedUniqueIdentifier",
              str(amend.get("uniqueIdentifier", "DCC-UNKNOWN")))

    # previousReport (hashType, optional)
    prev = cert.get("previous_report")
    if isinstance(prev, dict) and prev.get("value"):
        pr = ET.SubElement(core, "{https://ptb.de/dcc}previousReport")
        pr_ref = ET.SubElement(pr, "{https://ptb.de/dcc}referral")
        _lang_text(pr_ref, str(prev.get("referral", "Previous DCC")), "en")
        _text(pr, "{https://ptb.de/dcc}referralID", str(prev.get("referralID", "")))
        _text(pr, "{https://ptb.de/dcc}procedure", str(prev.get("procedure", "SHA-256")))
        _text(pr, "{https://ptb.de/dcc}value", str(prev["value"]))

    items = ET.SubElement(admin, "{https://ptb.de/dcc}items")
    item = ET.SubElement(
        items, "{https://ptb.de/dcc}item",
        {"id": "item-1", "refType": "gp_temperatureSensor"},
    )
    item_name = ET.SubElement(item, "{https://ptb.de/dcc}name")
    _safe_lang_text(item_name, cert["device_type"], "Temperature sensor", "en")

    # items/item/equipmentClass (optional, unbounded)
    eqc = cert.get("equipment_class")
    if isinstance(eqc, dict) and eqc.get("classID"):
        ec = ET.SubElement(item, "{https://ptb.de/dcc}equipmentClass")
        _text(ec, "{https://ptb.de/dcc}reference",
              _safe_text(eqc.get("reference", ""), ""))
        _text(ec, "{https://ptb.de/dcc}classID", str(eqc["classID"]))
        if eqc.get("link"):
            _text(ec, "{https://ptb.de/dcc}link", str(eqc["link"]))

    # items/item/description (richContentType) — fall back to first note if empty
    desc_text = cert.get("description")
    if not desc_text and cert.get("notes"):
        first_note = cert["notes"][0] if isinstance(cert["notes"], list) else str(cert["notes"])
        desc_text = str(first_note)
    if desc_text and not _is_placeholder(desc_text):
        desc_el = ET.SubElement(item, "{https://ptb.de/dcc}description")
        d_content = ET.SubElement(desc_el, "{https://ptb.de/dcc}content", {"lang": "en"})
        d_content.text = str(desc_text)

    # items/item/installedSoftwares (sensor firmware, optional)
    if cert.get("firmware") and not _is_placeholder(cert["firmware"]):
        isw = ET.SubElement(item, "{https://ptb.de/dcc}installedSoftwares")
        fw = ET.SubElement(isw, "{https://ptb.de/dcc}software",
                           {"id": "sensor-fw"})
        fw_name = ET.SubElement(fw, "{https://ptb.de/dcc}name")
        _lang_text(fw_name, "Sensor firmware", "en")
        _text(fw, "{https://ptb.de/dcc}release", str(cert["firmware"]))
        _text(fw, "{https://ptb.de/dcc}type", "firmware")

    manufacturer = ET.SubElement(item, "{https://ptb.de/dcc}manufacturer")
    manufacturer_name = ET.SubElement(manufacturer, "{https://ptb.de/dcc}name")
    _safe_lang_text(
        manufacturer_name, cert["manufacturer"], "Unknown manufacturer", "en"
    )
    _text(item, "{https://ptb.de/dcc}model", _safe_text(cert["model"], "Unknown model"))

    # items/item/itemQuantities — placed AFTER identifications per XSD order
    # (model → identifications → itemQuantities → subItems). We build the list
    # here and emit after the identifications block.
    ntc = data.get("ntc_model") or {}
    item_quantities = []
    for label, value, unit in [
        ("Nominal resistance R25", ntc.get("R25"), "\\ohm"),
        ("Beta value B25/85", ntc.get("B25_85"), "\\kelvin"),
        ("Temperature coefficient alpha_25", ntc.get("alpha_25"), "\\per\\kelvin"),
    ]:
        if value is None:
            continue
        item_quantities.append((label, float(value), unit))

    identifications = ET.SubElement(item, "{https://ptb.de/dcc}identifications")

    serial_ident = ET.SubElement(identifications, "{https://ptb.de/dcc}identification")
    _text(serial_ident, "{https://ptb.de/dcc}issuer", "manufacturer")
    _text(
        serial_ident,
        "{https://ptb.de/dcc}value",
        _safe_text(cert["serial_number"], "SERIAL-UNKNOWN"),
    )
    serial_name = ET.SubElement(serial_ident, "{https://ptb.de/dcc}name")
    _lang_text(serial_name, "Serial number", "en")

    asset_ident = ET.SubElement(identifications, "{https://ptb.de/dcc}identification")
    _text(asset_ident, "{https://ptb.de/dcc}issuer", "customer")
    _text(
        asset_ident,
        "{https://ptb.de/dcc}value",
        _safe_text(cert["asset_id"], "ASSET-UNKNOWN"),
    )
    asset_name = ET.SubElement(asset_ident, "{https://ptb.de/dcc}name")
    _lang_text(asset_name, "Asset ID", "en")

    lab_ident = ET.SubElement(identifications, "{https://ptb.de/dcc}identification")
    _text(lab_ident, "{https://ptb.de/dcc}issuer", "calibrationLaboratory")
    _text(
        lab_ident,
        "{https://ptb.de/dcc}value",
        _safe_text(cert["lab_reference"], "LAB-REF"),
    )
    lab_name = ET.SubElement(lab_ident, "{https://ptb.de/dcc}name")
    _lang_text(lab_name, "Laboratory reference", "en")

    if item_quantities:
        iq_list = ET.SubElement(item, "{https://ptb.de/dcc}itemQuantities")
        for label, value, unit in item_quantities:
            iq = ET.SubElement(
                iq_list, "{https://ptb.de/dcc}itemQuantity",
                {"refType": "gp_itemQuantity"},
            )
            iq_name = ET.SubElement(iq, "{https://ptb.de/dcc}name")
            _lang_text(iq_name, label, "en")
            iq_real = ET.SubElement(iq, "{https://ptb.de/si}real")
            _text(iq_real, "{https://ptb.de/si}value", f"{value:.6e}")
            _text(iq_real, "{https://ptb.de/si}unit", unit)

    ref_instr = data.get("_ref_instrument", {})
    if ref_instr.get("modelName") or ref_instr.get("calibrationCertificateID"):
        ref_item = ET.SubElement(items, "{https://ptb.de/dcc}item")
        ref_item_name = ET.SubElement(ref_item, "{https://ptb.de/dcc}name")
        _safe_lang_text(
            ref_item_name,
            ref_instr.get("modelName", ""),
            "Reference instrument",
            "en",
        )
        if ref_instr.get("manufacturer"):
            ref_man = ET.SubElement(ref_item, "{https://ptb.de/dcc}manufacturer")
            ref_man_name = ET.SubElement(ref_man, "{https://ptb.de/dcc}name")
            _safe_lang_text(
                ref_man_name,
                ref_instr.get("manufacturer", ""),
                "Unknown manufacturer",
                "en",
            )
        if ref_instr.get("mpn"):
            _text(ref_item, "{https://ptb.de/dcc}model", ref_instr["mpn"])
        if ref_instr.get("calibrationCertificateID"):
            ref_idents = ET.SubElement(ref_item, "{https://ptb.de/dcc}identifications")
            cert_ident = ET.SubElement(ref_idents, "{https://ptb.de/dcc}identification")
            _text(
                cert_ident,
                "{https://ptb.de/dcc}issuer",
                "calibrationLaboratory",
            )
            _text(
                cert_ident,
                "{https://ptb.de/dcc}value",
                ref_instr["calibrationCertificateID"],
            )
            cert_name = ET.SubElement(cert_ident, "{https://ptb.de/dcc}name")
            _lang_text(cert_name, "Calibration certificate", "en")

    cal_lab = ET.SubElement(admin, "{https://ptb.de/dcc}calibrationLaboratory")
    if cert.get("accreditation_code") and not _is_placeholder(cert["accreditation_code"]):
        _text(cal_lab, "{https://ptb.de/dcc}calibrationLaboratoryCode",
              str(cert["accreditation_code"]))
    contact = ET.SubElement(cal_lab, "{https://ptb.de/dcc}contact")
    contact_name = ET.SubElement(contact, "{https://ptb.de/dcc}name")
    _safe_lang_text(contact_name, org["org_name"], "Calibration laboratory", "en")
    if not _is_placeholder(org.get("email")):
        _text(contact, "{https://ptb.de/dcc}eMail", str(org["email"]))
    if not _is_placeholder(org.get("phone")):
        _text(contact, "{https://ptb.de/dcc}phone", str(org["phone"]))
    if not _is_placeholder(org.get("fax")):
        _text(contact, "{https://ptb.de/dcc}fax", str(org["fax"]))
    if not _is_placeholder(org.get("website")):
        _text(contact, "{https://ptb.de/dcc}link", _iso_url(org["website"]))
    # location is xs:choice — emit city, countryCode, postCode, street in
    # whatever order is present. fallback: "N/A" city.
    location = ET.SubElement(contact, "{https://ptb.de/dcc}location")
    addr = _split_address_lines(org.get("address_lines", []))
    if addr.get("street"):
        _text(location, "{https://ptb.de/dcc}street", addr["street"])
    if addr.get("postCode"):
        _text(location, "{https://ptb.de/dcc}postCode", addr["postCode"])
    _text(location, "{https://ptb.de/dcc}countryCode", cert.get("country", "IT"))
    if addr.get("city"):
        _text(location, "{https://ptb.de/dcc}city", addr["city"])
    else:
        _text(location, "{https://ptb.de/dcc}city", "N/A")
    # further carries the remaining address pieces (e.g. " | " joined extras).
    extra_addr = " | ".join(
        [s for s in (org.get("address_lines", []) or [])[1:-1] if s]
    ) if len(org.get("address_lines", []) or []) > 1 else ""
    if extra_addr:
        further_el = ET.SubElement(location, "{https://ptb.de/dcc}further")
        f_content = ET.SubElement(further_el, "{https://ptb.de/dcc}content", {"lang": "en"})
        f_content.text = extra_addr

    # crypt flags (C2): default seal+timestamp=true (this DCC is XAdES-signed)
    # unless the orchestrator passes explicit overrides via data["_signing"].
    signing = data.get("_signing") or {}
    _text(cal_lab, "{https://ptb.de/dcc}cryptElectronicSeal",
          "true" if signing.get("seal", True) else "false")
    _text(cal_lab, "{https://ptb.de/dcc}cryptElectronicSignature",
          "true" if signing.get("signature", False) else "false")
    _text(cal_lab, "{https://ptb.de/dcc}cryptElectronicTimeStamp",
          "true" if signing.get("timestamp", True) else "false")

    resp_persons_elem = ET.SubElement(admin, "{https://ptb.de/dcc}respPersons")
    roles = cert.get("resp_person_roles", []) or []
    for idx, person_name in enumerate(cert["resp_persons"]):
        resp_person = ET.SubElement(
            resp_persons_elem, "{https://ptb.de/dcc}respPerson",
            {"id": f"signer-{idx + 1}"},
        )
        # person is contactNotStrictType — name is required, eMail/phone/location optional.
        if isinstance(person_name, dict):
            person = ET.SubElement(resp_person, "{https://ptb.de/dcc}person")
            p_name = ET.SubElement(person, "{https://ptb.de/dcc}name")
            _safe_lang_text(p_name, person_name.get("name", ""),
                            f"Responsible person {idx + 1}", "en")
            if person_name.get("email") and not _is_placeholder(person_name["email"]):
                _text(person, "{https://ptb.de/dcc}eMail", str(person_name["email"]))
            if person_name.get("phone") and not _is_placeholder(person_name["phone"]):
                _text(person, "{https://ptb.de/dcc}phone", str(person_name["phone"]))
        else:
            person = ET.SubElement(resp_person, "{https://ptb.de/dcc}person")
            p_name = ET.SubElement(person, "{https://ptb.de/dcc}name")
            _safe_lang_text(p_name, person_name, f"Responsible person {idx + 1}", "en")
        if idx < len(roles) and roles[idx]:
            _text(resp_person, "{https://ptb.de/dcc}role", str(roles[idx]))
        if idx == 0:
            _text(resp_person, "{https://ptb.de/dcc}mainSigner", "true")
        # per-person crypt flags mirror the lab-level ones for the main signer.
        if idx == 0:
            _text(resp_person, "{https://ptb.de/dcc}cryptElectronicSignature",
                  "true" if signing.get("signature", False) else "false")
            _text(resp_person, "{https://ptb.de/dcc}cryptElectronicTimeStamp",
                  "true" if signing.get("timestamp", True) else "false")

    customer = ET.SubElement(admin, "{https://ptb.de/dcc}customer")
    cust = _customer_dict(cert.get("customer", ""))
    customer_name = ET.SubElement(customer, "{https://ptb.de/dcc}name")
    _safe_lang_text(customer_name, cust.get("name", ""), "Customer", "en")
    if cust.get("email") and not _is_placeholder(cust["email"]):
        _text(customer, "{https://ptb.de/dcc}eMail", cust["email"])
    if cust.get("phone") and not _is_placeholder(cust["phone"]):
        _text(customer, "{https://ptb.de/dcc}phone", cust["phone"])
    customer_location = ET.SubElement(customer, "{https://ptb.de/dcc}location")
    _text(customer_location, "{https://ptb.de/dcc}countryCode", cust.get("country", "IT"))
    if cust.get("city"):
        _text(customer_location, "{https://ptb.de/dcc}city", cust["city"])
    else:
        _text(customer_location, "{https://ptb.de/dcc}city", "N/A")
    if cust.get("address_lines"):
        for line in cust["address_lines"]:
            if "@" in line and "." in line and " " not in line.split("@")[0]:
                # looks like an email; skip — already handled above
                continue
            _text(customer_location, "{https://ptb.de/dcc}street", line)

    statements = ET.SubElement(admin, "{https://ptb.de/dcc}statements")
    for text in [
        cert["reproduction_conditions"],
        cert["traceability_statement"],
    ]:
        if not text or _is_placeholder(text):
            continue
        statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
        declaration = ET.SubElement(statement, "{https://ptb.de/dcc}declaration")
        _lang_text(declaration, text, "en")

    # Calibration function statement with coefficients, their standard
    # uncertainties, and the regression uncertainty. Coefficient uncertainties
    # are ALWAYS included here for all four procedures — this is the DCC's
    # complete metrological record, independent of whether the PDF shows them
    # (the PDF always hides them; see certificato_funzione.py).
    calib_model = data.get("_calib_model", "linear")
    rmse = data.get("_rmse", 0.0)
    coeffs = data.get("_coeffs", {})

    # Extract coverage factor from budget or default to 2.0
    u_budget_dcc: List[Dict[str, Any]] = data.get("_u_budget_per_step", [])
    _k_coverage = float(u_budget_dcc[0].get("k", 2.0)) if u_budget_dcc else 2.0

    if calib_model == "cubic":
        _a0, _a1, _a2, _a3 = coeffs.get("_a0", 0), coeffs.get("_a1", 0), coeffs.get("_a2", 0), coeffs.get("_a3", 0)
        _u_a0, _u_a1 = coeffs.get("_u_a0", 0), coeffs.get("_u_a1", 0)
        _u_a2, _u_a3 = coeffs.get("_u_a2", 0), coeffs.get("_u_a3", 0)
        func_text = (
            f"Calibration function (cubic polynomial): Y = A + B*D + C*D^2 + E*D^3. "
            f"Coefficients: A={_a0:.6e} (u={_u_a0:.2e}), B={_a1:.6e} (u={_u_a1:.2e}), "
            f"C={_a2:.6e} (u={_u_a2:.2e}), E={_a3:.6e} (u={_u_a3:.2e})."
        )
    elif calib_model == "quadratic":
        _a0, _a1, _a2 = coeffs.get("_a0", 0), coeffs.get("_a1", 0), coeffs.get("_a2", 0)
        _u_a0, _u_a1, _u_a2 = coeffs.get("_u_a0", 0), coeffs.get("_u_a1", 0), coeffs.get("_u_a2", 0)
        func_text = (
            f"Calibration function (quadratic polynomial): Y = A + B*D + C*D^2. "
            f"Coefficients: A={_a0:.6e} (u={_u_a0:.2e}), B={_a1:.6e} (u={_u_a1:.2e}), "
            f"C={_a2:.6e} (u={_u_a2:.2e})."
        )
    elif calib_model == "steinhart":
        _a, _b, _c = coeffs.get("_a", 0), coeffs.get("_b", 0), coeffs.get("_c", 0)
        _u_a, _u_b, _u_c = coeffs.get("_u_a", 0), coeffs.get("_u_b", 0), coeffs.get("_u_c", 0)
        func_text = (
            f"Calibration function (Steinhart-Hart): 1/T[K] = a + b*ln(R) + c*ln(R)^3. "
            f"Coefficients: a={_a:.6e} (u={_u_a:.2e}), b={_b:.6e} (u={_u_b:.2e}), "
            f"c={_c:.6e} (u={_u_c:.2e})."
        )
    else:
        _A, _B = coeffs.get("_A", 0), coeffs.get("_B", 0)
        _u_A, _u_B = coeffs.get("_u_A", 0), coeffs.get("_u_B", 0)
        func_text = (
            f"Calibration function (linear): Y = A*D + B. "
            f"Coefficients: A={_A:.6e} (u={_u_A:.2e}), B={_B:.6e} (u={_u_B:.2e})."
        )
    reg_text = (
        f"Regression uncertainty (expanded, k={_k_coverage:.1f}): u_reg = {_k_coverage * rmse:.2e}. "
        f"RMSE = {rmse:.2e}."
    )

    func_statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
    func_decl = ET.SubElement(func_statement, "{https://ptb.de/dcc}declaration")
    _lang_text(func_decl, func_text, "en")

    reg_statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
    reg_decl = ET.SubElement(reg_statement, "{https://ptb.de/dcc}declaration")
    _lang_text(reg_decl, reg_text, "en")

    conf = data.get("_conformity", {})
    if conf:
        conf_summary = conf.get("summary", {})
        conf_overall = conf_summary.get("overall", "NON-COMPLIANT")
        rH_list = conf.get("check_H", [])

        # Conformity is decided solely by Check H (Probability of False
        # Acceptance) on the as-found errors, guard-banded against the
        # declared MAE: acceptance requires PFA <= threshold (equivalently,
        # the as-found error within the reduced acceptance limits [AL, AU])
        # at every calibration point. No overlap check, no plain
        # max-tolerance check.
        dr_text = (
            f"Decision rule: acceptance when the Probability of False "
            f"Acceptance (Check H) does not exceed the declared threshold, "
            f"guard-banded against the declared MAE, for every calibration "
            f"point. Verdict: {conf_overall}."
        )
        dr_statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
        dr_decl = ET.SubElement(dr_statement, "{https://ptb.de/dcc}declaration")
        _lang_text(dr_decl, dr_text, "en")

        if rH_list:
            k_w_vals = [r.get("k_w") for r in rH_list if isinstance(r, dict) and r.get("k_w") is not None]
            if k_w_vals:
                h_params = conf.get("check_H_params", {})
                gb_text = (
                    f"Guard band: k_w = Φ⁻¹(1 − PFA_threshold) = {k_w_vals[0]:.4f}, "
                    f"MAE = ±{h_params.get('mae_y','?')}. Acceptance limits per point "
                    f"AL = -MAE + k_w·u_std, AU = MAE - k_w·u_std."
                )
                gb_statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
                gb_decl = ET.SubElement(gb_statement, "{https://ptb.de/dcc}declaration")
                _lang_text(gb_decl, gb_text, "en")

            pfa_parts = []
            for r in rH_list:
                if isinstance(r, dict):
                    pfa_parts.append(
                        f"P{r.get('punto','?')}={r.get('PFA_pct',0):.1f}%"
                    )
            if pfa_parts:
                h_params = conf.get("check_H_params", {})
                pfa_text = (
                    f"PFA (Probability of False Acceptance) per point: "
                    + " ".join(pfa_parts)
                    + f". MAE={h_params.get('mae_y','?')}, "
                    f"threshold={h_params.get('pfa_threshold_pct','?')}%, "
                    f"mode={h_params.get('u_std_mode','combined')}."
                )
                pfa_statement = ET.SubElement(statements, "{https://ptb.de/dcc}statement")
                pfa_decl = ET.SubElement(pfa_statement, "{https://ptb.de/dcc}declaration")
                _lang_text(pfa_decl, pfa_text, "en")

    meas_results = ET.SubElement(root, "{https://ptb.de/dcc}measurementResults")
    meas_result = ET.SubElement(meas_results, "{https://ptb.de/dcc}measurementResult")
    result_name = ET.SubElement(meas_result, "{https://ptb.de/dcc}name")
    _safe_lang_text(result_name, cert["certificate_title"], "Measurement results", "en")

    used_methods = ET.SubElement(meas_result, "{https://ptb.de/dcc}usedMethods")
    uncertainty_method = ET.SubElement(
        used_methods, "{https://ptb.de/dcc}usedMethod", {"refType": "basic_uncertainty"}
    )
    uncertainty_method_name = ET.SubElement(
        uncertainty_method, "{https://ptb.de/dcc}name"
    )
    _lang_text(uncertainty_method_name, "Expanded uncertainty", "en")
    uncertainty_description = ET.SubElement(
        uncertainty_method, "{https://ptb.de/dcc}description"
    )
    _lang_text(
        uncertainty_description,
        "Expanded uncertainty is reported with coverage factor k=2 and approximately 95% coverage probability.",
        "en",
    )
    _text(uncertainty_method, "{https://ptb.de/dcc}norm", "GUM")

    used_method = ET.SubElement(
        used_methods,
        "{https://ptb.de/dcc}usedMethod",
        {"refType": "gp_temperatureSensor"},
    )
    used_method_name = ET.SubElement(used_method, "{https://ptb.de/dcc}name")
    _safe_lang_text(
        used_method_name,
        cert["calibration_method"],
        "Calibration of temperature sensors",
        "en",
    )
    _text(
        used_method,
        "{https://ptb.de/dcc}norm",
        _safe_text(cert["procedure_code"], "INTERNAL-PROCEDURE"),
    )

    influence_conditions = ET.SubElement(
        meas_result, "{https://ptb.de/dcc}influenceConditions"
    )
    temp_condition = ET.SubElement(
        influence_conditions,
        "{https://ptb.de/dcc}influenceCondition",
        {"refType": "basic_temperature"},
    )
    temp_name = ET.SubElement(temp_condition, "{https://ptb.de/dcc}name")
    _lang_text(temp_name, "Ambient temperature", "en")

    temp_center, temp_tol = _extract_center_tolerance(
        str(cert["environment"].get("temperature", "")), 23.0, 1.5
    )
    temp_data = ET.SubElement(temp_condition, "{https://ptb.de/dcc}data")
    temp_min_q = ET.SubElement(
        temp_data, "{https://ptb.de/dcc}quantity", {"refType": "basic_temperatureMin"}
    )
    temp_min_name = ET.SubElement(temp_min_q, "{https://ptb.de/dcc}name")
    _lang_text(temp_min_name, "Minimum ambient temperature", "en")
    temp_min_hybrid = ET.SubElement(temp_min_q, "{https://ptb.de/si}hybrid")
    temp_min_k = ET.SubElement(temp_min_hybrid, "{https://ptb.de/si}real")
    _text(
        temp_min_k, "{https://ptb.de/si}value", f"{temp_center - temp_tol + 273.15:.2f}"
    )
    _text(temp_min_k, "{https://ptb.de/si}unit", "\\kelvin")
    temp_min_c = ET.SubElement(temp_min_hybrid, "{https://ptb.de/si}real")
    _text(temp_min_c, "{https://ptb.de/si}value", f"{temp_center - temp_tol:.2f}")
    _text(temp_min_c, "{https://ptb.de/si}unit", "\\degreeCelsius")

    temp_max_q = ET.SubElement(
        temp_data, "{https://ptb.de/dcc}quantity", {"refType": "basic_temperatureMax"}
    )
    temp_max_name = ET.SubElement(temp_max_q, "{https://ptb.de/dcc}name")
    _lang_text(temp_max_name, "Maximum ambient temperature", "en")
    temp_max_hybrid = ET.SubElement(temp_max_q, "{https://ptb.de/si}hybrid")
    temp_max_k = ET.SubElement(temp_max_hybrid, "{https://ptb.de/si}real")
    _text(
        temp_max_k, "{https://ptb.de/si}value", f"{temp_center + temp_tol + 273.15:.2f}"
    )
    _text(temp_max_k, "{https://ptb.de/si}unit", "\\kelvin")
    temp_max_c = ET.SubElement(temp_max_hybrid, "{https://ptb.de/si}real")
    _text(temp_max_c, "{https://ptb.de/si}value", f"{temp_center + temp_tol:.2f}")
    _text(temp_max_c, "{https://ptb.de/si}unit", "\\degreeCelsius")

    rh_condition = ET.SubElement(
        influence_conditions,
        "{https://ptb.de/dcc}influenceCondition",
        {"refType": "basic_humidityRelative"},
    )
    rh_name = ET.SubElement(rh_condition, "{https://ptb.de/dcc}name")
    _lang_text(rh_name, "Ambient relative humidity", "en")

    rh_center, rh_tol = _extract_center_tolerance(
        str(cert["environment"].get("relative_humidity", "")), 50.0, 10.0
    )
    rh_data = ET.SubElement(rh_condition, "{https://ptb.de/dcc}data")
    rh_min_q = ET.SubElement(
        rh_data,
        "{https://ptb.de/dcc}quantity",
        {"refType": "basic_humidityRelativeMin"},
    )
    rh_min_name = ET.SubElement(rh_min_q, "{https://ptb.de/dcc}name")
    _lang_text(rh_min_name, "Minimum ambient relative humidity", "en")
    rh_min_hybrid = ET.SubElement(rh_min_q, "{https://ptb.de/si}hybrid")
    rh_min_one = ET.SubElement(rh_min_hybrid, "{https://ptb.de/si}real")
    _text(rh_min_one, "{https://ptb.de/si}value", f"{(rh_center - rh_tol) / 100.0:.4f}")
    _text(rh_min_one, "{https://ptb.de/si}unit", "\\one")
    rh_min_percent = ET.SubElement(rh_min_hybrid, "{https://ptb.de/si}real")
    _text(rh_min_percent, "{https://ptb.de/si}value", f"{rh_center - rh_tol:.2f}")
    _text(rh_min_percent, "{https://ptb.de/si}unit", "\\percent")

    rh_max_q = ET.SubElement(
        rh_data,
        "{https://ptb.de/dcc}quantity",
        {"refType": "basic_humidityRelativeMax"},
    )
    rh_max_name = ET.SubElement(rh_max_q, "{https://ptb.de/dcc}name")
    _lang_text(rh_max_name, "Maximum ambient relative humidity", "en")
    rh_max_hybrid = ET.SubElement(rh_max_q, "{https://ptb.de/si}hybrid")
    rh_max_one = ET.SubElement(rh_max_hybrid, "{https://ptb.de/si}real")
    _text(rh_max_one, "{https://ptb.de/si}value", f"{(rh_center + rh_tol) / 100.0:.4f}")
    _text(rh_max_one, "{https://ptb.de/si}unit", "\\one")
    rh_max_percent = ET.SubElement(rh_max_hybrid, "{https://ptb.de/si}real")
    _text(rh_max_percent, "{https://ptb.de/si}value", f"{rh_center + rh_tol:.2f}")
    _text(rh_max_percent, "{https://ptb.de/si}unit", "\\percent")

    results = ET.SubElement(meas_result, "{https://ptb.de/dcc}results")
    result = ET.SubElement(
        results, "{https://ptb.de/dcc}result", {"refType": "gp_measuringResult1"}
    )
    result_title = ET.SubElement(result, "{https://ptb.de/dcc}name")
    _lang_text(result_title, "Calibration table", "en")
    data_elem = ET.SubElement(result, "{https://ptb.de/dcc}data")
    dcc_list = ET.SubElement(
        data_elem, "{https://ptb.de/dcc}list", {"refType": "gp_table1"}
    )

    # All columns are in the temperature domain (funzione variant).
    # Row format: {point, t_ref, t_c_post, me_pre, me_post, u_exp}
    reference_temps = [r["t_ref"]    for r in rows]
    t_c_post_list   = [r["t_c_post"] for r in rows]
    me_post_list    = [r["me_post"]  for r in rows]
    me_pre_list     = [r["me_pre"]   for r in rows]   # None for legacy rows
    uncertainties   = [r["u_exp"]    for r in rows]

    # Per-step GUM uncertainty budget (optional — present only for linear model).
    u_budget: List[Dict[str, Any]] = data.get("_u_budget_per_step", [])

    # ── Quantity 1: Reference temperature T_ref ──
    ref_q = ET.SubElement(
        dcc_list, "{https://ptb.de/dcc}quantity", {"refType": "basic_referenceValue"}
    )
    ref_q_name = ET.SubElement(ref_q, "{https://ptb.de/dcc}name")
    _lang_text(ref_q_name, "Reference temperature", "en")
    ref_hybrid = ET.SubElement(ref_q, "{https://ptb.de/si}hybrid")
    ref_real = ET.SubElement(ref_hybrid, "{https://ptb.de/si}realListXMLList")
    _text(ref_real, "{https://ptb.de/si}valueXMLList", _fmt(reference_temps, 6))
    _text(ref_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

    # ── Quantity 2: Calibrated sensor temperature T_c (post-calibration) ──
    meas_q = ET.SubElement(
        dcc_list, "{https://ptb.de/dcc}quantity", {"refType": "basic_measuredValue"}
    )
    meas_q_name = ET.SubElement(meas_q, "{https://ptb.de/dcc}name")
    _lang_text(meas_q_name, "Calibrated sensor temperature", "en")
    meas_hybrid = ET.SubElement(meas_q, "{https://ptb.de/si}hybrid")
    meas_real = ET.SubElement(meas_hybrid, "{https://ptb.de/si}realListXMLList")
    _text(meas_real, "{https://ptb.de/si}valueXMLList", _fmt(t_c_post_list, 6))
    _text(meas_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

    # ── Quantity 3: Measurement error before calibration M_e_pre (if available) ──
    if any(v is not None for v in me_pre_list):
        me_pre_safe = [v if v is not None else 0.0 for v in me_pre_list]
        me_pre_q = ET.SubElement(
            dcc_list, "{https://ptb.de/dcc}quantity",
            {"refType": "gp_measurementErrorPreCalibration"}
        )
        me_pre_q_name = ET.SubElement(me_pre_q, "{https://ptb.de/dcc}name")
        _lang_text(me_pre_q_name, "Measurement error before calibration (M_e pre)", "en")
        me_pre_hybrid = ET.SubElement(me_pre_q, "{https://ptb.de/si}hybrid")
        me_pre_real = ET.SubElement(me_pre_hybrid, "{https://ptb.de/si}realListXMLList")
        _text(me_pre_real, "{https://ptb.de/si}valueXMLList", _fmt(me_pre_safe, 6))
        _text(me_pre_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

    # ── Quantity 4: Measurement error after calibration M_e_post + expanded uncertainty ──
    dt_q = ET.SubElement(
        dcc_list, "{https://ptb.de/dcc}quantity", {"refType": "basic_measurementError"}
    )
    dt_q_name = ET.SubElement(dt_q, "{https://ptb.de/dcc}name")
    _lang_text(dt_q_name, "Measurement error after calibration (M_e post)", "en")
    dt_hybrid = ET.SubElement(dt_q, "{https://ptb.de/si}hybrid")
    dt_real = ET.SubElement(dt_hybrid, "{https://ptb.de/si}realListXMLList")
    _text(dt_real, "{https://ptb.de/si}valueXMLList", _fmt(me_post_list, 6))
    _text(dt_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)
    expanded_unc = ET.SubElement(dt_real, "{https://ptb.de/si}expandedUncXMLList")
    _text(expanded_unc, "{https://ptb.de/si}uncertaintyXMLList", _fmt(uncertainties, 6))
    _text(
        expanded_unc,
        "{https://ptb.de/si}coverageFactorXMLList",
        " ".join([str(_k_coverage)] * len(uncertainties)),
    )
    _text(
        expanded_unc,
        "{https://ptb.de/si}coverageProbabilityXMLList",
        " ".join(["0.95"] * len(uncertainties)),
    )
    _text(expanded_unc, "{https://ptb.de/si}distributionXMLList", "normal")

    # ── Quantities 5–8: GUM uncertainty budget breakdown (XML only, not PDF) ──
    # Present only when the per-step budget was computed (linear model).
    # Each quantity carries one uncertainty component as a realListXMLList so
    # that machine-readable consumers can reconstruct the full GUM budget.
    if u_budget and len(u_budget) == len(rows):
        uA_ref_list = [b["uA_ref"]    for b in u_budget]
        uA_i_list   = [b["uA_sensor"] for b in u_budget]
        u_c_list    = [b["u_c"]       for b in u_budget]
        k_list      = [b["k"]            for b in u_budget]

        # ── Quantity 5: Type A standard uncertainty – reference (PT100) ──
        uA_ref_q = ET.SubElement(
            dcc_list, "{https://ptb.de/dcc}quantity",
            {"refType": "gp_uncertaintyTypeA_reference"},
        )
        uA_ref_q_name = ET.SubElement(uA_ref_q, "{https://ptb.de/dcc}name")
        _lang_text(uA_ref_q_name, "Type A standard uncertainty – reference (u_A,ref)", "en")
        uA_ref_hybrid = ET.SubElement(uA_ref_q, "{https://ptb.de/si}hybrid")
        uA_ref_real = ET.SubElement(uA_ref_hybrid, "{https://ptb.de/si}realListXMLList")
        _text(uA_ref_real, "{https://ptb.de/si}valueXMLList", _fmt(uA_ref_list, 8))
        _text(uA_ref_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

        # ── Quantity 6: Type A standard uncertainty – sensor (NTC) ──
        uA_i_q = ET.SubElement(
            dcc_list, "{https://ptb.de/dcc}quantity",
            {"refType": "gp_uncertaintyTypeA_sensor"},
        )
        uA_i_q_name = ET.SubElement(uA_i_q, "{https://ptb.de/dcc}name")
        _lang_text(uA_i_q_name, "Type A standard uncertainty – sensor (u_A,sensor)", "en")
        uA_i_hybrid = ET.SubElement(uA_i_q, "{https://ptb.de/si}hybrid")
        uA_i_real = ET.SubElement(uA_i_hybrid, "{https://ptb.de/si}realListXMLList")
        _text(uA_i_real, "{https://ptb.de/si}valueXMLList", _fmt(uA_i_list, 8))
        _text(uA_i_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

        # ── Quantity 7: Combined standard uncertainty u_c(E) ──
        u_c_q = ET.SubElement(
            dcc_list, "{https://ptb.de/dcc}quantity",
            {"refType": "gp_combinedStandardUncertainty"},
        )
        u_c_q_name = ET.SubElement(u_c_q, "{https://ptb.de/dcc}name")
        _lang_text(u_c_q_name, "Combined standard uncertainty u_c(E)", "en")
        u_c_hybrid = ET.SubElement(u_c_q, "{https://ptb.de/si}hybrid")
        u_c_real = ET.SubElement(u_c_hybrid, "{https://ptb.de/si}realListXMLList")
        _text(u_c_real, "{https://ptb.de/si}valueXMLList", _fmt(u_c_list, 8))
        _text(u_c_real, "{https://ptb.de/si}unitXMLList", phys_unit_dsi)

        # ── Quantity 8: Coverage factor k ──
        k_q = ET.SubElement(
            dcc_list, "{https://ptb.de/dcc}quantity",
            {"refType": "gp_coverageFactor"},
        )
        k_q_name = ET.SubElement(k_q, "{https://ptb.de/dcc}name")
        _lang_text(k_q_name, "Coverage factor k (k=2, p≈95%)", "en")
        k_hybrid = ET.SubElement(k_q, "{https://ptb.de/si}hybrid")
        k_real = ET.SubElement(k_hybrid, "{https://ptb.de/si}realListXMLList")
        _text(k_real, "{https://ptb.de/si}valueXMLList", _fmt(k_list, 1))
        _text(k_real, "{https://ptb.de/si}unitXMLList", "\\one")

    # ── Quantity 9: Regression fit uncertainty (RMSE) ──
    _rmse_val = data.get("_rmse", 0.0)
    rmse_q = ET.SubElement(
        dcc_list, "{https://ptb.de/dcc}quantity",
        {"refType": "gp_regressionUncertainty"},
    )
    rmse_q_name = ET.SubElement(rmse_q, "{https://ptb.de/dcc}name")
    _lang_text(rmse_q_name, "Regression fit uncertainty RMSE", "en")
    rmse_hybrid = ET.SubElement(rmse_q, "{https://ptb.de/si}hybrid")
    rmse_real = ET.SubElement(rmse_hybrid, "{https://ptb.de/si}real")
    _text(rmse_real, "{https://ptb.de/si}value", f"{_rmse_val:.6e}")
    _text(rmse_real, "{https://ptb.de/si}unit", phys_unit_dsi)
    rmse_exp = ET.SubElement(rmse_real, "{https://ptb.de/si}expandedUnc")
    _text(rmse_exp, "{https://ptb.de/si}uncertainty", f"{_k_coverage * _rmse_val:.6e}")
    _text(rmse_exp, "{https://ptb.de/si}coverageFactor", str(_k_coverage))
    _text(rmse_exp, "{https://ptb.de/si}coverageProbability", "0.95")

    measurement_metadata = ET.SubElement(
        meas_result, "{https://ptb.de/dcc}measurementMetaData"
    )
    ET.SubElement(measurement_metadata, "{https://ptb.de/dcc}metaData")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    return tree


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a digital calibration certificate XML from calibration input JSON."
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT_JSON, help="Input JSON path"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_XML, help="Output XML path"
    )
    args = parser.parse_args()

    data = load_input_data(args.input)
    tree = build_dcc_tree(data)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    import io as _io

    _buf = _io.BytesIO()
    tree.write(_buf, encoding="utf-8", xml_declaration=False)
    header = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    args.output.write_bytes(header + _buf.getvalue())
    print(f"DCC XML written to: {args.output}")


if __name__ == "__main__":
    main()
