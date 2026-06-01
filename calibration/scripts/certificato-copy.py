from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List
import math
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    PageBreak,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ============================================================
# Variable data from input JSON
# ============================================================
DEFAULT_INPUT_JSON = Path(__file__).with_name("certificato-void-filled.json")

CERTIFICATE_PARAMS: Dict[str, Any] = {}
ORG: Dict[str, Any] = {}
CERT: Dict[str, Any] = {}
MEASUREMENTS: List[List[float]] = []
NTC_MODEL: Dict[str, Any] = {}
PDF_TEMPLATE_DATA: Dict[str, Any] = {}
CALIBRATION_RESULT: Dict[str, Any] = {}
EXPANDED_UNCERTAINTIES: List[float] = []


def _require_keys(d: Dict[str, Any], keys: List[str], scope_name: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"Missing keys in {scope_name}: {', '.join(missing)}")


def _expand_template_parts(
    parts: Dict[str, Any], calibration_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Normalize grouped template input into the legacy shape used by the PDF builder.

    Preferentially uses calculated fields (keys starting with ``_``) where available.
    Falls back to the non-underscore equivalent only when no calculated version exists.
    """
    _require_keys(
        parts,
        [
            "company_data",
            "organization_data",
            "sensor_method_template",
            "calibration_specific_data",
            "calculated_calibration_values",
            "pdf_template_data",
        ],
        "template_parts",
    )

    company = parts["company_data"]
    organization = parts["organization_data"]
    sensor = parts["sensor_method_template"]
    calibration = parts["calibration_specific_data"]
    calculated = parts["calculated_calibration_values"]
    pdf_template = parts["pdf_template_data"]

    _require_keys(
        company,
        [
            "org_name",
            "department",
            "address_lines",
            "phone",
            "email",
            "website",
            "document_id",
            "accreditation_line",
        ],
        "template_parts.company_data",
    )
    _require_keys(
        organization,
        [
            "accreditation_body",
            "reproduction_conditions",
            "traceability_statement",
            "authorised_by",
            "executed_by",
            "signature_name",
        ],
        "template_parts.organization_data",
    )
    _require_keys(
        sensor,
        [
            "device_type",
            "item",
            "manufacturer",
            "model",
            "serial_number",
            "calibration_method",
            "measurement_conditions",
            "procedure_code",
            "traceability",
            "traceability_chain_ids",
            "traceability_certificate_ids",
            "traceability_labs",
            "measurement_current",
            "connection_terminals",
            "ntc_model",
        ],
        "template_parts.sensor_method_template",
    )
    _require_keys(
        calibration,
        [
            "certificate_title",
            "certificate_title_en",
            "certificate_id",
            "certificate_number",
            "issue_date",
            "page_number",
            "total_pages",
            "customer",
            "receiver",
            "request_number",
            "request_date",
            "receipt_date",
            "measurement_dates",
            "asset_id",
            "lab_reference",
            "environment",
            "conditions",
            "location",
        ],
        "template_parts.calibration_specific_data",
    )
    # Require the calculated variants; fall back to plain names only when underscore key absent
    _require_keys(
        calculated,
        ["conclusions"],
        "template_parts.calculated_calibration_values",
    )
    if "_measurements" not in calculated and "measurements" not in calculated:
        raise ValueError(
            "Missing key in template_parts.calculated_calibration_values: _measurements (or measurements)"
        )
    if "_observations" not in calculated and "observations" not in calculated:
        raise ValueError(
            "Missing key in template_parts.calculated_calibration_values: _observations (or observations)"
        )

    _require_keys(
        pdf_template,
        [
            "footer_left_text",
            "contact_labels",
            "general_data_labels",
            "intro_text",
            "statements_title",
            "statements",
            "page2_title",
            "procedure_text_template",
            "procedure_row_labels",
            "additional_notes_title",
            "page3_title",
            "meta_labels",
            "results_headers",
            "notes_title",
            "notes_lines",
            "measurement_note_template",
            "page4_title",
            "model_section_title",
            "model_section_subtitle",
            "formula_lines",
            "coeff_table_headers",
            "coeff_labels",
            "approval_title",
            "approval_labels",
        ],
        "template_parts.pdf_template_data",
    )

    # Use calculated measurements / observations when available
    measurements_data = calculated.get("_measurements", calculated.get("measurements"))
    observations_data = calculated.get("_observations", calculated.get("observations"))

    # Use calculated notes when available; fall back to notes_template
    notes_data = sensor.get("_notes_computed", sensor.get("notes_template", []))

    # Per-point expanded uncertainties from calibration result (preferred)
    expanded_uncertainties = calibration_result.get("_expanded_uncertainties_degC", [])

    certificate_params = {
        "certificate_title": calibration["certificate_title"],
        "certificate_id": calibration["certificate_id"],
        "page_number": calibration["page_number"],
        "total_pages": calibration["total_pages"],
        "lab_name": company["org_name"],
        "lab_address": " | ".join(company["address_lines"]),
        "lab_location": calibration["location"],
        "client_name": calibration["customer"],
        "client_address": calibration["customer"],
        "device_type": sensor["device_type"],
        "manufacturer": sensor["manufacturer"],
        "model": sensor["model"],
        "serial_number": sensor["serial_number"],
        "calibration_date": calibration["measurement_dates"],
        "calibration_method": sensor["calibration_method"],
        "measurement_conditions": sensor["measurement_conditions"],
        "results_table": measurements_data,
        "observations": observations_data,
        "conclusions": calculated["conclusions"],
        "measured_quantity": "Temperature according to ITS-90",
        "expanded_uncertainties": expanded_uncertainties,
        "personnel": [
            {"name": organization["executed_by"], "role": "Executor", "signature": ""},
            {
                "name": organization["authorised_by"],
                "role": "Head of Centre",
                "signature": "",
            },
        ],
        "reproduction_conditions": organization["reproduction_conditions"],
        "accreditation_body": organization["accreditation_body"],
        "traceability_statement": organization["traceability_statement"],
        "executed_by": organization.get("executed_by", ""),
        "authorised_by": organization.get("authorised_by", ""),
    }

    cert = {
        "certificate_title": calibration["certificate_title"],
        "certificate_title_en": calibration["certificate_title_en"],
        "issue_date": calibration["issue_date"],
        "certificate_number": calibration["certificate_number"],
        "customer": calibration["customer"],
        "receiver": calibration["receiver"],
        "request_number": calibration["request_number"],
        "request_date": calibration["request_date"],
        "receipt_date": calibration["receipt_date"],
        "measurement_dates": calibration["measurement_dates"],
        "item": sensor["item"],
        "manufacturer": sensor["manufacturer"],
        "model": sensor["model"],
        "serial_number": sensor["serial_number"],
        "asset_id": calibration["asset_id"],
        "lab_reference": calibration["lab_reference"],
        "calibration_method": sensor["calibration_method"],
        "procedure_code": sensor["procedure_code"],
        "traceability": sensor["traceability"],
        "traceability_chain_ids": sensor["traceability_chain_ids"],
        "traceability_certificate_ids": sensor["traceability_certificate_ids"],
        "traceability_labs": sensor["traceability_labs"],
        "environment": calibration["environment"],
        "conditions": calibration["conditions"],
        "measurement_current": sensor["measurement_current"],
        "connection_terminals": sensor["connection_terminals"],
        "notes": notes_data,
        "authorised_by": organization["authorised_by"],
        "executed_by": organization["executed_by"],
        "signature_name": organization["signature_name"],
    }

    return {
        "certificate_params": certificate_params,
        "org": company,
        "cert": cert,
        "measurements": measurements_data,
        "ntc_model": sensor["ntc_model"],
        "pdf_template_data": pdf_template,
        "calibration_result": calibration_result,
    }

    cert = {
        "certificate_title": calibration["certificate_title"],
        "certificate_title_en": calibration["certificate_title_en"],
        "issue_date": calibration["issue_date"],
        "certificate_number": calibration["certificate_number"],
        "customer": calibration["customer"],
        "receiver": calibration["receiver"],
        "request_number": calibration["request_number"],
        "request_date": calibration["request_date"],
        "receipt_date": calibration["receipt_date"],
        "measurement_dates": calibration["measurement_dates"],
        "item": sensor["item"],
        "manufacturer": sensor["manufacturer"],
        "model": sensor["model"],
        "serial_number": sensor["serial_number"],
        "asset_id": calibration["asset_id"],
        "lab_reference": calibration["lab_reference"],
        "calibration_method": sensor["calibration_method"],
        "procedure_code": sensor["procedure_code"],
        "traceability": sensor["traceability"],
        "traceability_chain_ids": sensor["traceability_chain_ids"],
        "traceability_certificate_ids": sensor["traceability_certificate_ids"],
        "traceability_labs": sensor["traceability_labs"],
        "environment": calibration["environment"],
        "conditions": calibration["conditions"],
        "measurement_current": sensor["measurement_current"],
        "connection_terminals": sensor["connection_terminals"],
        "notes": notes_data,
        "authorised_by": organization["authorised_by"],
        "executed_by": organization["executed_by"],
        "signature_name": organization["signature_name"],
    }

    return {
        "certificate_params": certificate_params,
        "org": company,
        "cert": cert,
        "measurements": calculated["measurements"],
        "ntc_model": sensor["ntc_model"],
        "pdf_template_data": pdf_template,
    }


def load_input_data(json_path: Path) -> Dict[str, Any]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if "template_parts" in data:
        calibration_result = data.get("_calibration_result", {})
        data = _expand_template_parts(data["template_parts"], calibration_result)
    _require_keys(
        data,
        [
            "certificate_params",
            "org",
            "cert",
            "measurements",
            "ntc_model",
            "pdf_template_data",
        ],
        "root",
    )
    return data


def configure_from_input(data: Dict[str, Any]) -> None:
    global \
        CERTIFICATE_PARAMS, \
        ORG, \
        CERT, \
        MEASUREMENTS, \
        NTC_MODEL, \
        PDF_TEMPLATE_DATA, \
        CALIBRATION_RESULT, \
        EXPANDED_UNCERTAINTIES

    CERTIFICATE_PARAMS = data["certificate_params"]
    ORG = data["org"]
    CERT = data["cert"]
    MEASUREMENTS = data["measurements"]
    NTC_MODEL = data["ntc_model"]
    PDF_TEMPLATE_DATA = data["pdf_template_data"]
    CALIBRATION_RESULT = data.get("calibration_result", {})

    _require_keys(
        CERTIFICATE_PARAMS,
        [
            "certificate_title",
            "certificate_id",
            "page_number",
            "total_pages",
            "lab_name",
            "lab_address",
            "lab_location",
            "client_name",
            "client_address",
            "device_type",
            "manufacturer",
            "model",
            "serial_number",
            "calibration_date",
            "calibration_method",
            "measurement_conditions",
            "results_table",
            "observations",
            "conclusions",
            "measured_quantity",
            "expanded_uncertainties",
            "personnel",
            "reproduction_conditions",
            "accreditation_body",
            "traceability_statement",
        ],
        "certificate_params",
    )
    _require_keys(
        ORG,
        [
            "org_name",
            "department",
            "address_lines",
            "phone",
            "email",
            "website",
            "document_id",
            "accreditation_line",
        ],
        "org",
    )
    _require_keys(
        CERT,
        [
            "certificate_title",
            "certificate_title_en",
            "issue_date",
            "certificate_number",
            "customer",
            "receiver",
            "request_number",
            "request_date",
            "receipt_date",
            "measurement_dates",
            "item",
            "manufacturer",
            "model",
            "serial_number",
            "asset_id",
            "lab_reference",
            "calibration_method",
            "procedure_code",
            "traceability",
            "traceability_chain_ids",
            "traceability_certificate_ids",
            "traceability_labs",
            "environment",
            "conditions",
            "measurement_current",
            "connection_terminals",
            "notes",
            "authorised_by",
            "executed_by",
            "signature_name",
        ],
        "cert",
    )
    _require_keys(
        NTC_MODEL,
        [
            "R25",
            "B25_85",
            "A_steinhart",
            "B_steinhart",
            "C_steinhart",
            "alpha_25",
            "uncertainty_limit",
            "calibration_formula",
            "_A_cal",
            "_B_cal_degC",
            "_u_A",
            "_u_B_degC",
            "_cov_AB",
        ],
        "ntc_model",
    )
    _require_keys(
        PDF_TEMPLATE_DATA,
        [
            "footer_left_text",
            "contact_labels",
            "general_data_labels",
            "intro_text",
            "statements_title",
            "statements",
            "page2_title",
            "procedure_text_template",
            "procedure_row_labels",
            "additional_notes_title",
            "page3_title",
            "meta_labels",
            "results_headers",
            "notes_title",
            "notes_lines",
            "measurement_note_template",
            "page4_title",
            "model_section_title",
            "model_section_subtitle",
            "formula_lines",
            "coeff_table_headers",
            "coeff_labels",
            "approval_title",
            "approval_labels",
        ],
        "pdf_template_data",
    )


# ============================================================
# Layout helpers
# ============================================================


def mmv(value: float) -> float:
    return value * mm


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def fmt_dec(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def make_table(data, col_widths, style=None):
    tbl = Table(data, colWidths=col_widths, hAlign="LEFT")
    base = TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("WORDWRAP", (0, 0), (-1, -1), "CJK"),
        ]
    )
    if style:
        for cmd in style.getCommands():
            base.add(*cmd)
    tbl.setStyle(base)
    return tbl


def ntc_temp_from_resistance(
    r_ohm: float, r25: float | None = None, beta: float | None = None
) -> float:
    """Return temperature in °C from resistance using the Beta equation."""
    if r25 is None:
        r25 = float(NTC_MODEL["R25"])
    if beta is None:
        beta = float(NTC_MODEL["B25_85"])
    t25_k = 298.15
    t_k = 1.0 / (1.0 / t25_k + (math.log(r_ohm / r25) / beta))
    return t_k - 273.15


def beta_alpha_25(beta: float) -> float:
    """Return alpha (1/degC) at 25 degC from Beta model (negative for NTC)."""
    t25_k = 298.15
    return -beta / (t25_k**2)


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = A4
    left_x = doc.leftMargin
    right_x = width - doc.rightMargin

    # Top header line
    canvas.setStrokeColor(colors.HexColor("#1f2937"))
    canvas.setLineWidth(0.6)
    canvas.line(
        doc.leftMargin, height - mmv(12), width - doc.rightMargin, height - mmv(12)
    )

    # Organization block
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(left_x, height - mmv(9), ORG["org_name"])
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(left_x, height - mmv(16.2), ORG["department"])

    y = height - mmv(20)
    canvas.setFont("Helvetica", 7.2)
    for line in ORG["address_lines"]:
        canvas.drawString(left_x, y, line)
        y -= mmv(3.6)
    canvas.drawString(
        left_x,
        y - mmv(1.0),
        f"{PDF_TEMPLATE_DATA['contact_labels']['phone']} {ORG['phone']}",
    )
    canvas.drawString(
        left_x,
        y - mmv(4.6),
        f"{PDF_TEMPLATE_DATA['contact_labels']['email']} {ORG['email']}",
    )
    canvas.drawString(left_x, y - mmv(8.2), ORG["website"])

    # Right block
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawRightString(right_x, height - mmv(9), ORG["accreditation_line"])

    # Footer
    canvas.setStrokeColor(colors.HexColor("#cfd8e3"))
    canvas.line(doc.leftMargin, mmv(12), width - doc.rightMargin, mmv(12))
    canvas.setFont("Helvetica-Oblique", 7.2)
    canvas.setFillColor(colors.HexColor("#374151"))
    canvas.drawString(doc.leftMargin, mmv(7), PDF_TEMPLATE_DATA["footer_left_text"])
    page_no = canvas.getPageNumber()
    total_pages = CERTIFICATE_PARAMS.get("total_pages", "?")
    footer_right = f"{CERT['certificate_number']}  Page {page_no}/{total_pages}"
    canvas.drawRightString(width - doc.rightMargin, mmv(7), footer_right)

    canvas.restoreState()


def build_story(styles):
    story = []
    text_cfg = PDF_TEMPLATE_DATA

    # Title block
    story.append(Spacer(1, mmv(26)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(4)))
    story.append(
        p(
            f"<para align='center'><font size='12'><b>{CERT['certificate_number']}</b></font></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(8)))

    # General data table
    left_values = [
        CERT["issue_date"],
        CERT["customer"],
        CERT["receiver"],
        CERT["request_number"],
        CERT["request_date"],
        CERT["receipt_date"],
        CERT["measurement_dates"],
        CERT["item"],
        CERT["manufacturer"],
        CERT["model"],
        CERT["serial_number"],
        CERT["asset_id"],
        CERT["lab_reference"],
    ]
    left = list(zip(text_cfg["general_data_labels"], left_values))

    data_rows = []
    for label, value in left:
        data_rows.append(
            [
                p(f"<font size='8.2'>{label}</font>", styles["body"]),
                p(f"<font size='8.6'>{value}</font>", styles["body"]),
            ]
        )

    tbl = make_table(
        data_rows,
        [mmv(60), mmv(90)],
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#222222")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (1, 0), (1, -1), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )
    story.append(tbl)
    story.append(Spacer(1, mmv(8)))

    intro = text_cfg["intro_text"]
    story.append(p(f"<font size='8.8'>{intro}</font>", styles["body"]))
    story.append(Spacer(1, mmv(3)))

    statements = text_cfg["statements"]
    bullet_html = "".join([f"<li>{s}</li>" for s in statements])
    story.append(
        p(
            f"<font size='8.6'><b>{text_cfg['statements_title']}</b></font>",
            styles["section"],
        )
    )
    story.append(p(f"<font size='8.1'><ul>{bullet_html}</ul></font>", styles["body"]))

    story.append(PageBreak())

    # Page 2: procedure and environment
    story.append(Spacer(1, mmv(18)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(6)))
    story.append(
        p(
            f"<para align='center'><b>{text_cfg['page2_title']}</b></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(4)))

    chain_ids = ", ".join(CERT["traceability_chain_ids"])
    cert_ids = ", ".join(CERT["traceability_certificate_ids"])
    cert_labs = ", ".join(CERT["traceability_labs"])
    procedure_text = text_cfg["procedure_text_template"].format(
        procedure_code=CERT["procedure_code"],
        chain_ids=chain_ids,
        cert_ids=cert_ids,
        cert_labs=cert_labs,
    )

    # Procedure blocks
    procedure_rows = [
        (text_cfg["procedure_row_labels"][0], procedure_text),
        (text_cfg["procedure_row_labels"][1], CERT["calibration_method"]),
        (text_cfg["procedure_row_labels"][2], "<br/>".join(CERT["traceability"])),
        (
            text_cfg["procedure_row_labels"][3],
            f"Temperature {CERT['environment']['temperature']}<br/>Relative humidity {CERT['environment']['relative_humidity']}",
        ),
        (text_cfg["procedure_row_labels"][4], "<br/>".join(CERT["conditions"])),
    ]
    proc_data = []
    for label, value in procedure_rows:
        proc_data.append(
            [
                p(f"<font size='8.2'><b>{label}</b></font>", styles["body"]),
                p(f"<font size='8.2'>{value}</font>", styles["body"]),
            ]
        )

    proc_tbl = make_table(
        proc_data,
        [mmv(45), mmv(105)],
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#222222")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )
    story.append(proc_tbl)
    story.append(Spacer(1, mmv(6)))

    story.append(
        p(
            f"<font size='8.8'><b>{text_cfg['additional_notes_title']}</b></font>",
            styles["section"],
        )
    )
    for note in CERT["notes"]:
        story.append(p(f"<font size='8.1'>• {note}</font>", styles["body"]))

    story.append(PageBreak())

    # Page 3: results table
    story.append(Spacer(1, mmv(18)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(4)))
    story.append(
        p(
            f"<para align='center'><b>{text_cfg['page3_title']}</b></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(4)))

    meta_values = [CERT["item"], CERT["model"], CERT["serial_number"]]
    meta = list(zip(text_cfg["meta_labels"], meta_values))
    meta_tbl = make_table(
        [
            [
                p(f"<font size='7.9'>{a}</font>", styles["body"]),
                p(f"<font size='8.2'>{b}</font>", styles["body"]),
            ]
            for a, b in meta
        ],
        [mmv(38), mmv(107)],
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#555555")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#777777")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )
    story.append(meta_tbl)
    story.append(Spacer(1, mmv(6)))

    # Optional: display measurement uncertainty (MU) for model and serial if provided
    model_mu = CERT.get("model_mu", "-")
    serial_mu = CERT.get("serial_number_mu", "-")
    mu_rows = [
        [p(f"<font size='8.2'><b>Model MU</b></font>", styles["body"]), p(f"<font size='8.2'>{model_mu}</font>", styles["body"])],
        [p(f"<font size='8.2'><b>Serial number MU</b></font>", styles["body"]), p(f"<font size='8.2'>{serial_mu}</font>", styles["body"])],
    ]
    mu_tbl = make_table(
        mu_rows,
        [mmv(38), mmv(107)],
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#555555")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#777777")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )
    story.append(mu_tbl)

    header = [
        p(f"<font size='7.5'><b>{h}</b></font>", styles["table"])
        for h in text_cfg["results_headers"]
    ]

    # Use per-point expanded uncertainties from calibration result when available;
    # fall back to the u_c column already present in each measurement row.
    _expanded_u = CALIBRATION_RESULT.get("_expanded_uncertainties_degC", [])

    rows = [header]
    for idx_m, (point, temp_c, measured_r, nominal_r, u_c) in enumerate(MEASUREMENTS):
        sensor_temp = ntc_temp_from_resistance(measured_r)
        # Prefer calculated per-point uncertainty; fall back to measurement column value
        u_display = _expanded_u[idx_m] if idx_m < len(_expanded_u) else u_c
        rows.append(
            [
                p(f"<font size='8'>{point}</font>", styles["table"]),
                p(f"<font size='8'>{fmt_dec(temp_c, 2)}</font>", styles["table"]),
                p(f"<font size='8'>{fmt_dec(measured_r, 1)}</font>", styles["table"]),
                p(f"<font size='8'>{fmt_dec(nominal_r, 1)}</font>", styles["table"]),
                p(
                    f"<font size='8'>{fmt_dec(measured_r - nominal_r, 1)}</font>",
                    styles["table"],
                ),
                p(
                    f"<font size='8'>{fmt_dec(sensor_temp - temp_c, 2)}</font>",
                    styles["table"],
                ),
                p(f"<font size='8'>{fmt_dec(u_display, 2)}</font>", styles["table"]),
            ]
        )

    # Keep 15-point layout like the target certificate table.
    for idx in range(len(MEASUREMENTS) + 1, 16):
        rows.append(
            [
                p(f"<font size='8'>{idx}</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
                p("<font size='8'>-</font>", styles["table"]),
            ]
        )

    results_tbl = Table(
        rows,
        colWidths=[mmv(15), mmv(25), mmv(25), mmv(25), mmv(22), mmv(20), mmv(18)],
        hAlign="LEFT",
    )
    results_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#222222")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 1), (0, -1), "CENTER"),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(results_tbl)
    story.append(Spacer(1, mmv(6)))

    story.append(
        p(
            f"<font size='8.6'><b>{text_cfg['notes_title']}</b></font>",
            styles["section"],
        )
    )
    for line in text_cfg["notes_lines"]:
        story.append(p(f"<font size='8.1'>{line}</font>", styles["body"]))
    story.append(
        p(
            f"<font size='8.1'>{text_cfg['measurement_note_template'].format(measurement_current=CERT['measurement_current'], connection_terminals=CERT['connection_terminals'])}</font>",
            styles["body"],
        )
    )

    story.append(PageBreak())

    # Page 4: NTC calibration model
    story.append(Spacer(1, mmv(18)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(6)))
    story.append(
        p(
            f"<para align='center'><b>{text_cfg['page4_title']}</b></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(6)))

    # Calibration model formula: T = A * dout + B
    cal_formula = NTC_MODEL.get("calibration_formula", "T = A * dout + B")
    story.append(
        p(
            f"<font size='8.6'><b>{text_cfg['model_section_title']}</b></font>",
            styles["section"],
        )
    )
    story.append(Spacer(1, mmv(2)))
    story.append(
        p(
            f"<font size='8.8'><b>{cal_formula}</b></font>",
            styles["body"],
        )
    )
    story.append(Spacer(1, mmv(4)))

    # Calibration coefficients / summary table.
    # For NTC sensors we show the detailed Steinhart/Beta parameters; for
    # non-NTC sensors show a compact summary with maximum error and an
    # overall expanded uncertainty.
    sensor_type = NTC_MODEL.get("sensor_type", "ntc")
    cal_coeff_data = [[text_cfg["coeff_table_headers"][0], text_cfg["coeff_table_headers"][1]]]
    if sensor_type.lower() == "ntc":
        alpha_value = NTC_MODEL.get("alpha_25", beta_alpha_25(float(NTC_MODEL.get("B25_85", 0))))
        cal_coeff_data.extend([
            [text_cfg["coeff_labels"]["r25"], f"{NTC_MODEL.get('R25', 0):.1f} \u03a9"],
            [text_cfg["coeff_labels"]["b25_85"], f"{NTC_MODEL.get('B25_85', 0):.1f} K"],
            [text_cfg["coeff_labels"]["alpha25"], f"{float(alpha_value):.6e} 1/degC"],
            [text_cfg["coeff_labels"]["interp"], NTC_MODEL.get("uncertainty_limit", "-")],
            ["A (dimensionless)", f"{NTC_MODEL.get('_A_cal', 0):.10f}"],
            ["B [\u00b0C]", f"{NTC_MODEL.get('_B_cal_degC', 0):.10f}"],
            ["u(A)", f"{NTC_MODEL.get('_u_A', 0):.10f}"],
            ["u(B) [\u00b0C]", f"{NTC_MODEL.get('_u_B_degC', 0):.10f}"],
            ["cov(A,B)", f"{NTC_MODEL.get('_cov_AB', 0):.10f}"],
        ])
    
        # Generic sensor summary
        max_err = NTC_MODEL.get("maximum_error", NTC_MODEL.get("max_error", "-"))
        # Prefer an overall expanded uncertainty if provided, otherwise fall back
        # to the per-point list in certificate params / calibration result.
        overall_exp = NTC_MODEL.get(
            "expanded_uncertainty",
            CALIBRATION_RESULT.get("_expanded_uncertainties_degC", "-"),
        )
        if isinstance(overall_exp, list):
            overall_exp_display = ", ".join([fmt_dec(v, 2) for v in overall_exp])
        else:
            overall_exp_display = str(overall_exp)
        cal_coeff_data.extend([
            ["Maximum error", f"{max_err}"],
            ["Expanded uncertainty", overall_exp_display],
        ])
    cal_coeff_tbl = Table(cal_coeff_data, colWidths=[mmv(55), mmv(90)], hAlign="LEFT")
    cal_coeff_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#222222")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(cal_coeff_tbl)
    story.append(Spacer(1, mmv(8)))

    story.append(
        p(
            f"<font size='8.6'><b>{text_cfg['approval_title']}</b></font>",
            styles["section"],
        )
    )
    approval = [
        [text_cfg["approval_labels"][0], CERT["executed_by"]],
        [text_cfg["approval_labels"][1], CERT["authorised_by"]],
        [text_cfg["approval_labels"][2], CERT["signature_name"]],
    ]
    appr_tbl = Table(approval, colWidths=[mmv(55), mmv(90)], hAlign="LEFT")
    appr_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#222222")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(appr_tbl)

    return story


def make_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleCustom",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=14,
            leading=16,
            alignment=TA_CENTER,
            spaceAfter=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SubtitleCustom",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=9.5,
            leading=11,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyCustom",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.2,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionCustom",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9.0,
            leading=11,
            spaceBefore=2,
            spaceAfter=4,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableCustom",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=9.1,
            alignment=TA_CENTER,
        )
    )
    return {
        "title": styles["TitleCustom"],
        "subtitle": styles["SubtitleCustom"],
        "body": styles["BodyCustom"],
        "section": styles["SectionCustom"],
        "table": styles["TableCustom"],
    }


def build_pdf(output_path: str):
    styles = make_styles()
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=CERT["certificate_title"],
        author=ORG["org_name"],
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=header_footer)])
    story = build_story(styles)
    doc.build(story)


def _collect_leaf_paths(obj: Any, prefix: str = "") -> List[str]:
    """Recursively collect dotted key paths for *leaf* values only (scalars and empty containers)."""
    paths: List[str] = []
    if isinstance(obj, dict):
        if not obj:
            paths.append(prefix)
        else:
            for k, v in obj.items():
                full = f"{prefix}.{k}" if prefix else k
                paths.extend(_collect_leaf_paths(v, full))
    elif isinstance(obj, list):
        if not obj:
            paths.append(prefix)
        else:
            for i, v in enumerate(obj):
                paths.extend(_collect_leaf_paths(v, f"{prefix}[{i}]"))
    else:
        paths.append(prefix)
    return paths


def report_unused_json_fields(json_path: Path) -> None:
    """Print to terminal all top-level JSON paths that are not consumed by the PDF builder.

    A field is considered *used* when its dotted path appears as a substring in the set
    of paths that the script explicitly reads during ``_expand_template_parts`` and
    ``build_story``.  Fields whose non-underscore counterpart has a ``_`` calculated
    alternative that *is* used are flagged as superseded rather than unused.
    """
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    all_paths = _collect_leaf_paths(raw)

    # Paths the PDF builder actively accesses (based on key names accessed in code).
    # These are the *leaf keys* (last segment) that are read at runtime.
    used_leaf_keys: set[str] = {
        # company_data
        "org_name",
        "department",
        "address_lines",
        "phone",
        "email",
        "website",
        "document_id",
        "accreditation_line",
        # organization_data
        "accreditation_body",
        "reproduction_conditions",
        "traceability_statement",
        "authorised_by",
        "executed_by",
        "signature_name",
        # sensor_method_template
        "device_type",
        "item",
        "manufacturer",
        "model",
        "serial_number",
        "calibration_method",
        "measurement_conditions",
        "procedure_code",
        "traceability",
        "traceability_chain_ids",
        "traceability_certificate_ids",
        "traceability_labs",
        "measurement_current",
        "connection_terminals",
        "ntc_model",
        # sensor_method_template._notes_computed (preferred) / notes_template (fallback)
        "_notes_computed",
        "notes_template",
        # ntc_model fields used in PDF (physical model characterisation)
        "R25",
        "B25_85",
        "A_steinhart",
        "B_steinhart",
        "C_steinhart",
        "alpha_25",
        "uncertainty_limit",
        "calibration_formula",
        "calibration_procedure",
        "method_description",
        "formula_steinhart",
        "formula_beta",
        # ntc_model calculated fields used in PDF (GUM OLS output)
        "_A_cal",
        "_B_cal_degC",
        "_u_A",
        "_u_B_degC",
        "_cov_AB",
        # generic non-NTC sensor fields
        "sensor_type",
        "maximum_error",
        "max_error",
        "expanded_uncertainty",
        # optional PDF-only metadata for MU
        "model_mu",
        "serial_number_mu",
        # calibration_specific_data
        "certificate_title",
        "certificate_title_en",
        "certificate_id",
        "certificate_number",
        "issue_date",
        "page_number",
        "total_pages",
        "customer",
        "receiver",
        "request_number",
        "request_date",
        "receipt_date",
        "measurement_dates",
        "asset_id",
        "lab_reference",
        "environment",
        "temperature",
        "relative_humidity",
        "conditions",
        "location",
        # calculated_calibration_values — prefer underscore variants
        "_measurements",
        "_observations",
        "conclusions",
        "measurements",
        "observations",
        # pdf_template_data
        "footer_left_text",
        "contact_labels",
        "phone",  # contact_labels sub-key
        "email",  # contact_labels sub-key
        "general_data_labels",
        "intro_text",
        "statements_title",
        "statements",
        "page2_title",
        "procedure_text_template",
        "procedure_row_labels",
        "additional_notes_title",
        "page3_title",
        "meta_labels",
        "results_headers",
        "notes_title",
        "notes_lines",
        "measurement_note_template",
        "page4_title",
        "model_section_title",
        "model_section_subtitle",
        "formula_lines",
        "coeff_table_headers",
        "coeff_labels",
        # coeff_labels sub-keys (accessed via dict lookup in build_story)
        "r25",
        "b25_85",
        "a",
        "b",
        "c",
        "alpha25",
        "interp",
        "approval_title",
        "approval_labels",
        # _calibration_result — only _expanded_uncertainties_degC is directly read by PDF builder
        "_expanded_uncertainties_degC",
        # The following _calibration_result fields are informational metadata not read by PDF builder
        # (they are intentionally not in the used set so they appear in the unused report)
    }

    # Fields that have a calculated _ alternative that IS used (superseded non-_ fields)
    superseded: Dict[str, str] = {
        "measurements": "_measurements",
        "observations": "_observations",
        "notes_template": "_notes_computed",
        "starting_uncertainties": "_u_B_degC / _expanded_uncertainties_degC",
    }

    print("\n" + "=" * 70)
    print("UNUSED / SUPERSEDED JSON FIELDS IN PDF OUTPUT")
    print("=" * 70)

    unused: List[str] = []
    superseded_report: List[tuple[str, str]] = []

    for path in all_paths:
        leaf = path.split(".")[-1].split("[")[0]
        if leaf in used_leaf_keys:
            continue
        # Check if this is a superseded field
        if leaf in superseded:
            superseded_report.append((path, superseded[leaf]))
        else:
            unused.append(path)

    if superseded_report:
        print(
            "\n[SUPERSEDED] - non-calculated field exists but its _ alternative is used:"
        )
        for path, alt in superseded_report:
            print(f"  {path}  =>  use {alt} instead")

    if unused:
        print(
            "\n[NOT USED in PDF] - fields present in JSON but not read by the script:"
        )
        # Collapse array elements (e.g. foo[0]..foo[5]) into a single "foo[*]" line
        collapsed: List[str] = []
        seen_array_bases: set[str] = set()
        for path in unused:
            # Detect array element paths like "foo.bar[2]"
            if "[" in path.split(".")[-1]:
                base = path[: path.rfind("[")]
                if base not in seen_array_bases:
                    seen_array_bases.add(base)
                    collapsed.append(f"  {base}[*]  (array - all elements unused)")
            else:
                collapsed.append(f"  {path}")
        for line in collapsed:
            print(line)
    else:
        print("\n  (no unused fields detected)")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate calibration certificate PDF from JSON input."
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(DEFAULT_INPUT_JSON),
        help="Path to input JSON with all variable data",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="./ntc_calibration_certificate_template.pdf",
        help="Output PDF path",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")

    data = load_input_data(input_path)
    configure_from_input(data)
    report_unused_json_fields(input_path)
    build_pdf(args.output)
    print(f"PDF written to: {args.output}")
