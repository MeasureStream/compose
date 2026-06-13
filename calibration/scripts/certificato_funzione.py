"""
certificato_funzione.py
=======================
Variante NTC_FUNZIONE del generatore di certificati di taratura.

Differenze rispetto alla versione originale del generatore PDF:
- Pagina 3: tabella a 6 colonne in °C + LSB grezzo
  (Point, T_ref/°C, T_c/°C, D/LSB, M_e/°C, U(E)/°C)
  - Colonne Ohm e dR rimosse
  - "Object" = DIGITAL thermometer
- Pagina 4: funzione di taratura T = A·D + B con tabella coefficienti
  (R25, B25/85, alpha, Interpolation uncertainty, A, B/°C, u(A), u(B)/°C, cov(A,B))
  seguita dal riquadro Approval
- Page 3 footnotes: only GUM notes + D/LSB note
- Righe vuote tabella: solo i punti misurati (niente padding fino a 15)

Formato measurements atteso (ogni riga):
  [point, T_ref_degC, T_sensor_degC, D_lsb, error_degC, U_exp_degC]
"""
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


DEFAULT_INPUT_JSON = Path(__file__).with_name("certificato_funzione_filled.json")

CERTIFICATE_PARAMS: Dict[str, Any] = {}
ORG: Dict[str, Any] = {}
CERT: Dict[str, Any] = {}
MEASUREMENTS: List[List[float]] = []
SENSOR_MODEL: Dict[str, Any] = {}
PDF_TEMPLATE_DATA: Dict[str, Any] = {}
CALIBRATION_RESULT: Dict[str, Any] = {}
PHYS_UNIT_SYMBOL: str = "\u00b0C"   # default; overridden from JSON at load time


def _require_keys(d: Dict[str, Any], keys: List[str], scope_name: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"Missing keys in {scope_name}: {', '.join(missing)}")


def _expand_template_parts(
    parts: Dict[str, Any], calibration_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Normalize grouped template input into the flat shape used by the PDF builder."""
    company = parts["company_data"]
    organization = parts["organization_data"]
    sensor = parts["sensor_method_template"]
    calibration = parts["calibration_specific_data"]
    calculated = parts["calculated_calibration_values"]
    pdf_template = parts["pdf_template_data"]

    measurements_data = calculated.get("_measurements", calculated.get("measurements"))
    observations_data = calculated.get("_observations", calculated.get("observations"))
    notes_data = sensor.get("_notes_computed", sensor.get("notes_template", []))
    expanded_uncertainties = calibration_result.get(
        "_expanded_uncertainties",
        calibration_result.get("_expanded_uncertainties_phys", []),
    )
    # Physical unit symbol (e.g. "°C" or "K") — written into the JSON by the
    # orchestrator from the sensor JSON ranges.phys.dsi field.
    _unit_sym = calibration_result.get("_phys_unit_symbol", "\u00b0C")

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
            {"name": organization["authorised_by"], "role": "Head of Centre", "signature": ""},
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
        "calibration_method": sensor["calibration_method"],
        "procedure_code": sensor["procedure_code"],
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
        "sensor_model": sensor.get("sensor_model", sensor.get("ntc_model", {})),
        "pdf_template_data": pdf_template,
        "calibration_result": calibration_result,
        "phys_unit_symbol": _unit_sym,
    }


def load_input_data(json_path: Path) -> Dict[str, Any]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if "template_parts" in data:
        calibration_result = data.get("_calibration_result", {})
        data = _expand_template_parts(data["template_parts"], calibration_result)
    _require_keys(
        data,
        ["certificate_params", "org", "cert", "measurements", "pdf_template_data"],
        "root",
    )
    return data


def configure_from_input(data: Dict[str, Any]) -> None:
    global CERTIFICATE_PARAMS, ORG, CERT, MEASUREMENTS, SENSOR_MODEL, PDF_TEMPLATE_DATA, CALIBRATION_RESULT, PHYS_UNIT_SYMBOL

    CERTIFICATE_PARAMS = data["certificate_params"]
    ORG = data["org"]
    CERT = data["cert"]
    MEASUREMENTS = data["measurements"]
    SENSOR_MODEL = data.get("sensor_model", data.get("ntc_model", {}))
    PDF_TEMPLATE_DATA = data["pdf_template_data"]
    CALIBRATION_RESULT = data.get("calibration_result", {})
    PHYS_UNIT_SYMBOL = data.get(
        "phys_unit_symbol",
        CALIBRATION_RESULT.get("_phys_unit_symbol", "\u00b0C"),
    )


# Layout helpers

def mmv(value: float) -> float:
    return value * mm


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def fmt_dec(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def fmt_sci(value: float, decimals: int = 10) -> str:
    return f"{value:.{decimals}f}"


def fmt_sci_sig(value: float, sig: int = 2) -> str:
    """Format with 2 significant digits, comma as decimal separator."""
    if value == 0.0:
        return "0"
    import math
    return f"{value:.{sig - 1 - int(math.floor(math.log10(abs(value))))}f}".replace(".", ",")


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


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = A4
    left_x = doc.leftMargin
    right_x = width - doc.rightMargin

    canvas.setStrokeColor(colors.HexColor("#1f2937"))
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, height - mmv(12), width - doc.rightMargin, height - mmv(12))

    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(left_x, height - mmv(9), ORG["org_name"])
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(left_x, height - mmv(16.2), ORG["department"])

    y = height - mmv(20)
    canvas.setFont("Helvetica", 7.2)
    for line in ORG["address_lines"]:
        canvas.drawString(left_x, y, line)
        y -= mmv(3.6)
    canvas.drawString(left_x, y - mmv(1.0), f"{PDF_TEMPLATE_DATA['contact_labels']['phone']} {ORG['phone']}")
    canvas.drawString(left_x, y - mmv(4.6), f"{PDF_TEMPLATE_DATA['contact_labels']['email']} {ORG['email']}")
    canvas.drawString(left_x, y - mmv(8.2), ORG["website"])

    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawRightString(right_x, height - mmv(9), ORG["accreditation_line"])

    canvas.setStrokeColor(colors.HexColor("#cfd8e3"))
    canvas.line(doc.leftMargin, mmv(12), width - doc.rightMargin, mmv(12))
    canvas.setFont("Helvetica-Oblique", 7.2)
    canvas.setFillColor(colors.HexColor("#374151"))
    canvas.drawString(doc.leftMargin, mmv(7), PDF_TEMPLATE_DATA["footer_left_text"])
    page_no = canvas.getPageNumber()
    total_pages = getattr(doc, "_total_pages_computed", CERTIFICATE_PARAMS.get("total_pages", "?"))
    footer_right = f"{CERT['certificate_number']}  Page {page_no}/{total_pages}"
    canvas.drawRightString(width - doc.rightMargin, mmv(7), footer_right)

    canvas.restoreState()


def build_story(styles):
    story = []
    text_cfg = PDF_TEMPLATE_DATA

    # ── Pagina 1: Dati generali ──
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
    ]
    left = list(zip(text_cfg["general_data_labels"], left_values))
    data_rows = [
        [
            p(f"<font size='8.2'>{label}</font>", styles["body"]),
            p(f"<font size='8.6'>{value}</font>", styles["body"]),
        ]
        for label, value in left
    ]
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

    story.append(PageBreak())

    # ── Pagina 2: Procedura ──
    story.append(Spacer(1, mmv(18)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(6)))
    story.append(p(f"<para align='center'><b>{text_cfg['page2_title']}</b></para>", styles["subtitle"]))
    story.append(Spacer(1, mmv(4)))

    chain_ids = ", ".join(CERT["traceability_chain_ids"])
    cert_ids = ", ".join(CERT["traceability_certificate_ids"])
    cert_labs = ", ".join(CERT["traceability_labs"])
    procedure_text = text_cfg["procedure_text_template"].format(
        procedure_code="",
        chain_ids=chain_ids,
        cert_ids=cert_ids,
        cert_labs=cert_labs,
    )

    procedure_rows = [
        (text_cfg["procedure_row_labels"][1],  CERT["calibration_method"]),
        (text_cfg["procedure_row_labels"][0], procedure_text),
        (
            text_cfg["procedure_row_labels"][3],
            f"Temperature {CERT['environment']['temperature']}<br/>Relative humidity {CERT['environment']['relative_humidity']}",
        ),
        (text_cfg["procedure_row_labels"][4], "<br/>".join(CERT["conditions"])),
    ]
    proc_data = [
        [
            p(f"<font size='8.2'><b>{label}</b></font>", styles["body"]),
            p(f"<font size='8.2'>{value}</font>", styles["body"]),
        ]
        for label, value in procedure_rows
    ]
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

    story.append(p(f"<font size='8.8'><b>{text_cfg['additional_notes_title']}</b></font>", styles["section"]))
    for note in CERT["notes"]:
        story.append(p(f"<font size='8.1'>• {note}</font>", styles["body"]))
    story.append(p(f"<font size='8.1'>• M_e = T_c - T_ref (signed temperature difference in {PHYS_UNIT_SYMBOL}).</font>", styles["body"]))
    story.append(Spacer(1, mmv(6)))

    intro = text_cfg["intro_text"]
    story.append(p(f"<font size='8.8'>{intro}</font>", styles["body"]))
    story.append(Spacer(1, mmv(3)))

    statements = text_cfg["statements"]
    bullet_html = "".join([f"<li>{s}</li>" for s in statements])
    story.append(p(f"<font size='8.6'><b>{text_cfg['statements_title']}</b></font>", styles["section"]))
    story.append(p(f"<font size='8.1'><ul>{bullet_html}</ul></font>", styles["body"]))

    story.append(PageBreak())

    # ── Pagina 3: Risultati — tabella 5 colonne in °C ──
    story.append(Spacer(1, mmv(18)))
    story.append(
        p(
            f"<para align='center'><b>{CERT['certificate_title']}</b><br/>{CERT['certificate_title_en']}</para>",
            styles["title"],
        )
    )
    story.append(Spacer(1, mmv(4)))
    story.append(p(f"<para align='center'><b>{text_cfg['page3_title']}</b></para>", styles["subtitle"]))
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

    # Tabella risultati: 5 colonne [Point, T_ref/°C, T_c/°C, M_e/°C, U(E)/°C]
    # MEASUREMENTS formato: [point, T_ref_degC, T_sensor_degC, error_degC, U_exp_degC]
    # Temperatura con risoluzione 0.01°C -> max 2 decimali in tabella
    # Incertezza con 2 cifre significative -> virgola, max 2 decimali
    # 6-column table: Point | T_ref | T_c (post) | M_e pre | M_e post | U(E)
    # Row format: [point, T_ref, T_c_post, M_e_pre, M_e_post, U_exp]
    # Backwards-compat: rows with only 5 elements treated as old format (no pre column).
    _has_pre = len(MEASUREMENTS) > 0 and len(MEASUREMENTS[0]) >= 6

    u = PHYS_UNIT_SYMBOL   # short alias for header strings
    if _has_pre:
        default_headers = [
            "Point",
            f"T_ref / {u}\nReference temperature",
            f"T_c / {u}\nsensor value as left",
            f"M_e pre / {u}\nfound",
            f"M_e post / {u}\nas left",
            f"U(E) / {u}\nMeasurement Uncertainty",
        ]
    else:
        default_headers = [
            "Point",
            f"T_ref / {u}\nReference temperature",
            f"T_c / {u}\nMeasured value",
            f"M_e / {u}\nMeasurement error",
            f"U(E) / {u}\nMeasurement Uncertainty",
        ]

    headers = text_cfg.get("results_headers", default_headers)
    # If template still has the old 5-element header list but data has 6 cols, extend it.
    if _has_pre and len(headers) == 5:
        headers = [
            headers[0],
            headers[1],
            headers[2],
            f"M_e pre / {u}\nError before cal.",
            headers[3],
            headers[4],
        ]

    cleaned_headers = []
    for h in headers:
        h_clean = h
        for suffix in [f" / {u}", " / \u00b0C", " / °C", " / K", f"/{u}", "/\u00b0C", "/°C", "/K"]:
            h_clean = h_clean.replace(suffix, "")
        cleaned_headers.append(h_clean)
    headers = cleaned_headers

    def _header_para(text: str) -> Paragraph:
        if "\n" in text:
            title, desc = text.split("\n", 1)
            html = f"<font size='7.5'><b>{title}</b></font><br/><font size='6.2'>{desc}</font>"
        else:
            html = f"<font size='7.5'><b>{text}</b></font>"
        return p(html, styles["table"])

    header_row = [_header_para(h) for h in headers]

    rows = [header_row]
    for row in MEASUREMENTS:
        point  = row[0]
        t_ref  = row[1]
        t_sensor = row[2]
        if len(row) >= 6:
            me_pre  = row[3]
            me_post = row[4]
            u_exp   = row[5]
        else:
            me_pre  = None
            me_post = row[3]
            u_exp   = row[4]

        data_row = [
            p(f"<font size='8'>{int(point)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(t_ref, 2)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(t_sensor, 2)}</font>", styles["table"]),
        ]
        if me_pre is not None:
            data_row.append(p(f"<font size='8'>{fmt_dec(me_pre, 2)}</font>", styles["table"]))
        data_row.append(p(f"<font size='8'>{fmt_dec(me_post, 2)}</font>", styles["table"]))
        u_exp_fmt = "0" if u_exp == 0.0 else f"{u_exp:.1e}".replace(".", ",")
        data_row.append(p(f"<font size='8'>{u_exp_fmt}</font>", styles["table"]))
        rows.append(data_row)

    # Column widths — scale to fit A4 body (173 mm usable)
    if _has_pre:
        # 6 columns: 14 + 30 + 28 + 28 + 28 + 25 = 153 mm
        col_widths = [mmv(14), mmv(30), mmv(28), mmv(28), mmv(28), mmv(25)]
    else:
        # 5 columns (legacy)
        col_widths = [mmv(18), mmv(36), mmv(36), mmv(36), mmv(30)]
    results_tbl = Table(rows, colWidths=col_widths, hAlign="LEFT")
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

    story.append(p(f"<font size='8.6'><b>{text_cfg['notes_title']}</b></font>", styles["section"]))
    for line in text_cfg["notes_lines"]:
        story.append(p(f"<font size='8.1'>{line}</font>", styles["body"]))
    story.append(
        p(
            f"<font size='8.1'>{text_cfg['measurement_note_template'].format(measurement_current=CERT['measurement_current'], connection_terminals=CERT['connection_terminals'])}</font>",
            styles["body"],
        )
    )

    story.append(PageBreak())

    # ── Pagina 4: Funzione di taratura + Approval ──
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
            f"<para align='center'><b>{text_cfg.get('page4_title', f'Tabulazione T/{PHYS_UNIT_SYMBOL} = f(D/LSB)')}</b></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(4)))

    # Identificazione della F. taratura e sua espressione
    _calib_model = CALIBRATION_RESULT.get("_calib_model", SENSOR_MODEL.get("_calib_model", "linear"))
    if _calib_model == "cubic":
        model_desc = "cubic polynomial"
    else:
        model_desc = "linear"

    story.append(p(
        f"<font size='8.6'>After having identified the calibration function, the {model_desc} calibration function is:</font>",
        styles["body"],
    ))
    story.append(Spacer(1, mmv(3)))

    # Testo introduttivo funzione di taratura
    intro_p4 = text_cfg.get(
        "page4_intro_text",
        "Nella seguente tabella sono riportati i coefficienti dell'equazione lineare di taratura:",
    )
    if _calib_model == "cubic" and intro_p4 == "Nella seguente tabella sono riportati i coefficienti dell'equazione lineare di taratura:":
        intro_p4 = "Nella seguente tabella sono riportati i coefficienti dell'equazione cubica di taratura:"
    story.append(p(f"<font size='8.6'>{intro_p4}</font>", styles["body"]))
    story.append(Spacer(1, mmv(2)))

    # Equazione — lineare o cubica
    if _calib_model == "cubic":
        cal_formula = "T = A + B \u00b7 D + C \u00b7 D\u00b2 + D \u00b7 D\u00b3"
    else:
        cal_formula = SENSOR_MODEL.get("calibration_formula", "T = A \u00b7 D + B")
    story.append(p(f"<para align='center'><font size='10'><b>{cal_formula}</b></font></para>", styles["body"]))
    story.append(Spacer(1, mmv(4)))

    # Helper: scientific notation with N significant digits
    def _fmt_sci(val: float, sig: int = 4) -> str:
        if val == 0.0:
            return "0"
        return f"{val:.{sig - 1}e}".replace(".", ",")

    # Tabella coefficienti
    coeff_headers = text_cfg.get("coeff_table_headers", ["Parameter", "Value"])
    coeff_labels = text_cfg.get("coeff_labels", {})

    # Regression uncertainty = 2 * RMSE, 2 significant digits
    rmse_val = float(CALIBRATION_RESULT.get("_rmse", 0.0))
    reg_unc_val = 2.0 * rmse_val
    reg_unc_text = f"{fmt_sci_sig(reg_unc_val)} {PHYS_UNIT_SYMBOL}"
    print(f"Debug: RMSE={rmse_val}, Regression uncertainty (2*RMSE)={reg_unc_val}, formatted='{reg_unc_text}'")

    if _calib_model == "cubic":
        _a0 = SENSOR_MODEL.get("_a0", 0)
        _a1 = SENSOR_MODEL.get("_a1", 0)
        _a2 = SENSOR_MODEL.get("_a2", 0)
        _a3 = SENSOR_MODEL.get("_a3", 0)

        cal_coeff_data = [
            [coeff_headers[0], coeff_headers[1]],
            [coeff_labels.get("interp", "Regression uncertainty"), reg_unc_text],
            [f"A / {PHYS_UNIT_SYMBOL}", _fmt_sci(_a0, 3)],
            [f"B / ({PHYS_UNIT_SYMBOL}/LSB)", _fmt_sci(_a1, 3)],
            [f"C / ({PHYS_UNIT_SYMBOL}/LSB\u00b2)", _fmt_sci(_a2, 3)],
            [f"D / ({PHYS_UNIT_SYMBOL}/LSB\u00b3)", _fmt_sci(_a3, 3)],
        ]
    else:
        _B_cal_display = SENSOR_MODEL.get("_B_cal", 0)

        cal_coeff_data = [
            [coeff_headers[0], coeff_headers[1]],
            [coeff_labels.get("interp", "Regression uncertainty"), reg_unc_text],
            [f"A / ({PHYS_UNIT_SYMBOL}/LSB)", _fmt_sci(SENSOR_MODEL.get('_A_cal', 0), 3)],
            [f"B / {PHYS_UNIT_SYMBOL}", _fmt_sci(_B_cal_display, 3)],
        ]

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

    story.append(p(f"<font size='8.6'><b>{text_cfg['approval_title']}</b></font>", styles["section"]))
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
    styles.add(ParagraphStyle(name="TitleCustom", parent=styles["Normal"], fontName="Helvetica", fontSize=14, leading=16, alignment=TA_CENTER, spaceAfter=0))
    styles.add(ParagraphStyle(name="SubtitleCustom", parent=styles["Normal"], fontName="Helvetica-Oblique", fontSize=9.5, leading=11, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="BodyCustom", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=10.2, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="SectionCustom", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9.0, leading=11, spaceBefore=2, spaceAfter=4, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="TableCustom", parent=styles["Normal"], fontName="Helvetica", fontSize=7.8, leading=9.1, alignment=TA_CENTER))
    return {
        "title": styles["TitleCustom"],
        "subtitle": styles["SubtitleCustom"],
        "body": styles["BodyCustom"],
        "section": styles["SectionCustom"],
        "table": styles["TableCustom"],
    }


def _compute_total_pages(n_points: int) -> int:
    """
    Compute total PDF pages based on number of measurement points.

    Fixed pages: 1 (general data) + 1 (procedure) + 1 (coefficients/approval) = 3.
    Results page: each results page fits approximately 20 data rows comfortably.
    One results page suffices for up to ~20 points; add one page per additional 20.
    """
    FIXED_PAGES = 3          # general data + procedure + coefficients
    ROWS_PER_PAGE = 20       # comfortable fit including header row
    results_pages = max(1, math.ceil(n_points / ROWS_PER_PAGE))
    return FIXED_PAGES + results_pages


def build_pdf(output_path: str):
    styles = make_styles()
    n_points = len(MEASUREMENTS)
    total_pages = _compute_total_pages(n_points)

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
    # Store on doc so header_footer callback can read it without a global
    doc._total_pages_computed = total_pages

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=header_footer)])
    story = build_story(styles)
    doc.build(story)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate NTC_FUNZIONE calibration certificate PDF.")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT_JSON), help="Path to input JSON")
    parser.add_argument("--output", type=str, default="./ntc_cert_funzione.pdf", help="Output PDF path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")

    data = load_input_data(input_path)
    configure_from_input(data)
    build_pdf(args.output)
    print(f"PDF written to: {args.output}")
