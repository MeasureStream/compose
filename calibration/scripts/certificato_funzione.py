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
        "receipt_date": calibration.get("receipt_date", ""),
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
    """Format a number with `sig` significant figures (comma decimal sep).
    - If |exponent| >= 4 -> use 10^ notation: mantissa in [1, 10)  (e.g. 1,2·10^-4)
    - Otherwise            -> plain decimal, with all digits needed to keep
      the `sig` significant figures  (e.g. 0.05 -> "0,050", 1.0 -> "1,0",
      0.9999 -> "1,00", 1234.5 -> "1234,5").
    Edge case: when rounding bumps the mantissa to 10, the exponent is
    incremented so the mantissa stays in [1, 10).
    """
    if value == 0.0:
        return "0"
    import math
    abs_v = abs(value)
    exp10 = int(math.floor(math.log10(abs_v)))
    if exp10 >= 4 or exp10 <= -4:
        mantissa = abs_v / (10 ** exp10)
        mantissa_str = f"{mantissa:.{sig - 1}f}"
        if mantissa_str.startswith("10") or float(mantissa_str.replace(",", ".")) >= 10.0:
            exp10 += 1
            mantissa = abs_v / (10 ** exp10)
            mantissa_str = f"{mantissa:.{sig - 1}f}"
        mantissa_str = mantissa_str.replace(".", ",")
        sign = "-" if value < 0 else ""
        return f"{sign}{mantissa_str}\u00b710^{exp10}"
    decimals = max(0, sig - 1 - exp10)
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

    # Mappa etichetta → valore: adattiva rispetto a qualunque sottoinsieme di
    # general_data_labels nel template (10 voci legacy o 11 con receipt_date).
    _label_to_value = {
        "Date of issue":           CERT.get("issue_date", ""),
        "Customer":                CERT.get("customer", ""),
        "Receiver":                CERT.get("receiver", ""),
        "Request number":          CERT.get("request_number", ""),
        "Request date":            CERT.get("request_date", ""),
        "Date of receipt of item": CERT.get("receipt_date", "---"),
        "Date of measurements":    CERT.get("measurement_dates", ""),
        "Item":                    CERT.get("item") or CERTIFICATE_PARAMS.get("device_type", ""),
        "Manufacturer":            CERT.get("manufacturer", ""),
        "Model":                   CERT.get("model", ""),
        "Serial number":           CERT.get("serial_number", ""),
    }
    left = [
        (label, _label_to_value.get(label, ""))
        for label in text_cfg["general_data_labels"]
    ]
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

    # Tabella risultati — adattiva: 5 o 6 colonne in base ai dati.
    # Formato riga: [point, T_ref, T_sensor, as_found, residual, U_exp]
    # La colonna "residuals" viene mostrata solo se almeno una riga ha
    # as_found != residual (altrimenti sono la stessa cosa e si omette).
    u = PHYS_UNIT_SYMBOL   # short alias

    # Rileva se la colonna residui è necessaria
    def _has_residuals(meas):
        for row in meas:
            if len(row) >= 6:
                as_found = row[3]
                residual = row[4]
                if abs(float(as_found) - float(residual)) > 1e-12:
                    return True
        return False

    show_residuals = _has_residuals(MEASUREMENTS)

    # Intestazioni dal template (5 voci: Point, T_ref, T_c, M_e, U(E))
    # oppure 6 voci se il template include già la colonna residui.
    # Il layout logico del dato è sempre:
    #   col 0 = Point
    #   col 1 = T_ref
    #   col 2 = T_sensor
    #   col 3 = as_found  (M_e pre-adjustment)
    #   col 4 = residual  (M_e post-adjustment) — solo se show_residuals
    #   col 5 = U(E)

    def _strip_unit(h: str) -> str:
        for suffix in [f" / {u}", " / \u00b0C", " / °C", " / K", f"/{u}", "/\u00b0C", "/°C", "/K"]:
            h = h.replace(suffix, "")
        return h

    cfg_headers = [_strip_unit(h) for h in text_cfg.get("results_headers", [])]

    # Default a 6 posizioni — tutti nel formato "titolo\ndescrizione" per
    # altezza uniforme della riga header.
    _h_defaults = [
        "Point\n ",
        f"T_ref\nReference temperature",
        f"T_c\nMeasured value (as-found)",
        f"M_e\nMeasurement error (as-found)",
        f"M_e residuals\nMeasurement error (as-left)",
        f"U(E)\nMeasurement Uncertainty",
    ]

    # Costruisce h6 a 6 posizioni dal template:
    # 5 voci → [Point, T_ref, T_c, M_e, U(E)]: sposta U(E) in pos 5, inserisce default residui in pos 4
    # 6 voci → usa direttamente
    # < 5    → riempi con default
    def _normalise(h: str) -> str:
        """Assicura che ogni header abbia esattamente un \n (titolo\ndescrizione)."""
        if "\n" not in h:
            h = h + "\n "
        return h

    if len(cfg_headers) >= 6:
        h6 = [_normalise(cfg_headers[i] if i < len(cfg_headers) else _h_defaults[i]) for i in range(6)]
    elif len(cfg_headers) == 5:
        h6 = [_normalise(h) for h in cfg_headers[:4]] + [_h_defaults[4], _normalise(cfg_headers[4])]
    else:
        h6 = [_normalise(cfg_headers[i] if i < len(cfg_headers) else _h_defaults[i]) for i in range(6)]

    if show_residuals:
        col_indices = [0, 1, 2, 3, 4, 5]
        col_widths  = [mmv(14), mmv(30), mmv(28), mmv(26), mmv(26), mmv(26)]
    else:
        col_indices = [0, 1, 2, 3, 5]
        col_widths  = [mmv(18), mmv(36), mmv(34), mmv(34), mmv(28)]

    selected_headers = [h6[i] for i in col_indices]

    def _header_para(text: str) -> Paragraph:
        # Formato sempre "titolo\ndescrizione"
        title, desc = text.split("\n", 1)
        html = f"<font size='7.5'><b>{title}</b></font><br/><font size='6.2'>{desc.strip()}</font>"
        return p(html, styles["table"])

    header_row = [_header_para(h) for h in selected_headers]
    rows = [header_row]

    for row in MEASUREMENTS:
        point    = row[0]
        t_ref    = row[1]
        t_sensor = row[2]
        as_found = row[3] if len(row) > 3 else 0.0
        residual = row[4] if len(row) > 4 else as_found
        u_exp    = row[5] if len(row) > 5 else (row[4] if len(row) > 4 else 0.0)

        all_vals = [
            p(f"<font size='8'>{int(point)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(t_ref, 2)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(t_sensor, 2)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(as_found, 2)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_dec(residual, 4)}</font>", styles["table"]),
            p(f"<font size='8'>{fmt_sci_sig(u_exp)}</font>", styles["table"]),
        ]
        data_row = [all_vals[i] for i in col_indices if i < len(all_vals)]
        rows.append(data_row)

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
    _calib_model_early = CALIBRATION_RESULT.get("_calib_model", SENSOR_MODEL.get("_calib_model", "linear"))
    _page4_title_default = {
        "steinhart": f"Calibration function T/{PHYS_UNIT_SYMBOL} = f(R/\u03a9) — Steinhart-Hart",
        "cubic":     f"Calibration function T/{PHYS_UNIT_SYMBOL} = f(D/LSB) — cubic",
        "linear":    f"Calibration function T/{PHYS_UNIT_SYMBOL} = f(D/LSB) — linear",
    }.get(_calib_model_early, f"T/{PHYS_UNIT_SYMBOL} = f(D/LSB)")
    # Sovrascrivi il titolo italiano dal template se ancora presente
    _page4_title_raw = text_cfg.get("page4_title", _page4_title_default)
    if "Tabulazione" in _page4_title_raw or "taratura" in _page4_title_raw:
        _page4_title_raw = _page4_title_default
    story.append(
        p(
            f"<para align='center'><b>{_page4_title_raw}</b></para>",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, mmv(4)))

    # Identificazione della F. taratura e sua espressione
    _calib_model = CALIBRATION_RESULT.get("_calib_model", SENSOR_MODEL.get("_calib_model", "linear"))

    _model_desc_map = {
        "linear":    "linear",
        "cubic":     "cubic polynomial",
        "quadratic": "quadratic polynomial",
        "steinhart": "Steinhart-Hart",
    }
    model_desc = _model_desc_map.get(_calib_model, _calib_model)

    story.append(p(
        f"<font size='8.6'>After having identified the calibration function, the {model_desc} calibration function is:</font>",
        styles["body"],
    ))
    story.append(Spacer(1, mmv(3)))

    # Testo introduttivo funzione di taratura
    _default_intro_map = {
        "linear":    "The following table lists the coefficients of the linear calibration equation:",
        "cubic":     "The following table lists the coefficients of the cubic calibration equation:",
        "quadratic": "The following table lists the coefficients of the quadratic calibration equation:",
        "steinhart": "The following table lists the Steinhart-Hart coefficients of the calibration equation:",
    }
    _default_intro = _default_intro_map.get(_calib_model, "The following table lists the calibration coefficients:")
    intro_p4 = text_cfg.get("page4_intro_text", _default_intro)
    # Traduci automaticamente le frasi italiane rimaste nel template
    _italian_fallbacks = {
        "Nella seguente tabella sono riportati i coefficienti dell'equazione lineare di taratura:":
            _default_intro_map.get(_calib_model, _default_intro),
        "Nella seguente tabella sono riportati i coefficienti dell'equazione cubica di taratura:":
            _default_intro_map.get(_calib_model, _default_intro),
    }
    intro_p4 = _italian_fallbacks.get(intro_p4, intro_p4)
    story.append(p(f"<font size='8.6'>{intro_p4}</font>", styles["body"]))
    story.append(Spacer(1, mmv(2)))

    # Equazione — lineare, cubica o Steinhart-Hart
    if _calib_model == "cubic":
        cal_formula = "T = A + B \u00b7 D + C \u00b7 D\u00b2 + E \u00b7 D\u00b3"
    elif _calib_model == "steinhart":
        cal_formula = "1/T<sub rise='3' size='7'>K</sub> = a + b\u00b7ln(R) + c\u00b7[ln(R)]<sup>3</sup>"
    else:
        cal_formula = SENSOR_MODEL.get("calibration_formula", "T = A \u00b7 D + B")
    story.append(p(f"<para align='center'><font size='10'><b>{cal_formula}</b></font></para>", styles["body"]))
    story.append(Spacer(1, mmv(4)))

    # Helper: notazione 10^ solo quando |exp| >= 4
    def _fmt_sci(val: float, sig: int = 4) -> str:
        if val == 0.0:
            return "0"
        import math
        abs_v = abs(val)
        exp10 = int(math.floor(math.log10(abs_v)))
        if exp10 >= 4 or exp10 <= -4:
            mantissa = abs_v / (10 ** exp10)
            mantissa_str = f"{mantissa:.{sig - 1}f}"
            if mantissa_str.startswith("10") or float(mantissa_str.replace(",", ".")) >= 10.0:
                exp10 += 1
                mantissa = abs_v / (10 ** exp10)
                mantissa_str = f"{mantissa:.{sig - 1}f}"
            mantissa_str = mantissa_str.replace(".", ",")
            sign = "-" if val < 0 else ""
            return f"{sign}{mantissa_str}\u00b710^{exp10}"
        decimals = max(0, sig - 1 - exp10)
        return f"{val:.{decimals}f}".replace(".", ",")

    # Tabella coefficienti
    coeff_headers = text_cfg.get("coeff_table_headers", ["Parameter", "Value"])
    coeff_labels = text_cfg.get("coeff_labels", {})

    # Regression uncertainty:
    #   linear  → max_i(u_fit_i) × k   (GUM covariance propagation per punto)
    #   others  → 2 × RMSE
    if _calib_model == "linear":
        _u_budget = CALIBRATION_RESULT.get("_u_budget_per_step", [])
        _u_fit_values = [b.get("u_fit_i", 0.0) for b in _u_budget]
        _k_val = float(_u_budget[0].get("k", 2.0)) if _u_budget else 2.0
        reg_unc_val = max(_u_fit_values) * _k_val if _u_fit_values else 0.0
        _reg_label = "Regression uncertainty (max u_fit × k)"
    else:
        rmse_val = float(CALIBRATION_RESULT.get("_rmse", 0.0))
        reg_unc_val = 2.0 * rmse_val
        _reg_label = "Regression uncertainty (2·RMSE)"
    reg_unc_text = f"{fmt_sci_sig(reg_unc_val)} {PHYS_UNIT_SYMBOL}"

    if _calib_model == "steinhart":
        # Coefficienti da SENSOR_MODEL (scritti dall'orchestratore come _a, _b, _c)
        # fallback a CALIBRATION_RESULT se non presenti nel modello sensore
        _a = SENSOR_MODEL.get("_a", CALIBRATION_RESULT.get("_a", 0.0))
        _b = SENSOR_MODEL.get("_b", CALIBRATION_RESULT.get("_b", 0.0))
        _c = SENSOR_MODEL.get("_c", CALIBRATION_RESULT.get("_c", 0.0))
        _u_a = SENSOR_MODEL.get("_u_a", CALIBRATION_RESULT.get("_u_a", 0.0))
        _u_b = SENSOR_MODEL.get("_u_b", CALIBRATION_RESULT.get("_u_b", 0.0))
        _u_c = SENSOR_MODEL.get("_u_c", CALIBRATION_RESULT.get("_u_c", 0.0))

        def _pc(txt, val=""):
            """Riga tabella coefficienti come coppia di Paragraph."""
            return [
                p(f"<font size='8.2'>{txt}</font>", styles["body"]),
                p(f"<font size='8.2'>{val}</font>", styles["body"]),
            ]

        cal_coeff_data = [
            _pc(f"<b>{coeff_headers[0]}</b>", f"<b>{coeff_headers[1]}</b>"),
            _pc(coeff_labels.get("interp", _reg_label), reg_unc_text),
            _pc("a  [1/K]",    _fmt_sci(_a, 4)),
            _pc("b  [1/K]",    _fmt_sci(_b, 4)),
            _pc("c  [1/K]",    _fmt_sci(_c, 4)),
            _pc("u(a)  [1/K]", _fmt_sci(_u_a, 2)),
            _pc("u(b)  [1/K]", _fmt_sci(_u_b, 2)),
            _pc("u(c)  [1/K]", _fmt_sci(_u_c, 2)),
        ]
    elif _calib_model == "cubic":
        _a0 = SENSOR_MODEL.get("_a0", CALIBRATION_RESULT.get("_a0", 0.0))
        _a1 = SENSOR_MODEL.get("_a1", CALIBRATION_RESULT.get("_a1", 0.0))
        _a2 = SENSOR_MODEL.get("_a2", CALIBRATION_RESULT.get("_a2", 0.0))
        _a3 = SENSOR_MODEL.get("_a3", CALIBRATION_RESULT.get("_a3", 0.0))
        _u_a0 = SENSOR_MODEL.get("_u_a0", CALIBRATION_RESULT.get("_u_a0", 0.0))
        _u_a1 = SENSOR_MODEL.get("_u_a1", CALIBRATION_RESULT.get("_u_a1", 0.0))
        _u_a2 = SENSOR_MODEL.get("_u_a2", CALIBRATION_RESULT.get("_u_a2", 0.0))
        _u_a3 = SENSOR_MODEL.get("_u_a3", CALIBRATION_RESULT.get("_u_a3", 0.0))

        def _pc(txt, val=""):
            return [
                p(f"<font size='8.2'>{txt}</font>", styles["body"]),
                p(f"<font size='8.2'>{val}</font>", styles["body"]),
            ]

        cal_coeff_data = [
            _pc(f"<b>{coeff_headers[0]}</b>", f"<b>{coeff_headers[1]}</b>"),
            _pc(coeff_labels.get("interp", _reg_label), reg_unc_text),
            _pc(f"A  [{PHYS_UNIT_SYMBOL}]",                _fmt_sci(_a0, 4)),
            _pc(f"B  [{PHYS_UNIT_SYMBOL}/LSB]",            _fmt_sci(_a1, 4)),
            _pc(f"C  [{PHYS_UNIT_SYMBOL}/LSB<super>2</super>]", _fmt_sci(_a2, 4)),
            _pc(f"E  [{PHYS_UNIT_SYMBOL}/LSB<super>3</super>]", _fmt_sci(_a3, 4)),
            _pc(f"u(A)  [{PHYS_UNIT_SYMBOL}]",              _fmt_sci(_u_a0, 2)),
            _pc(f"u(B)  [{PHYS_UNIT_SYMBOL}/LSB]",          _fmt_sci(_u_a1, 2)),
            _pc(f"u(C)  [{PHYS_UNIT_SYMBOL}/LSB<super>2</super>]", _fmt_sci(_u_a2, 2)),
            _pc(f"u(E)  [{PHYS_UNIT_SYMBOL}/LSB<super>3</super>]", _fmt_sci(_u_a3, 2)),
        ]
    elif _calib_model == "quadratic":
        _a0 = SENSOR_MODEL.get("_a0", CALIBRATION_RESULT.get("_a0", 0.0))
        _a1 = SENSOR_MODEL.get("_a1", CALIBRATION_RESULT.get("_a1", 0.0))
        _a2 = SENSOR_MODEL.get("_a2", CALIBRATION_RESULT.get("_a2", 0.0))
        _u_a0 = SENSOR_MODEL.get("_u_a0", CALIBRATION_RESULT.get("_u_a0", 0.0))
        _u_a1 = SENSOR_MODEL.get("_u_a1", CALIBRATION_RESULT.get("_u_a1", 0.0))
        _u_a2 = SENSOR_MODEL.get("_u_a2", CALIBRATION_RESULT.get("_u_a2", 0.0))

        def _pc(txt, val=""):
            return [
                p(f"<font size='8.2'>{txt}</font>", styles["body"]),
                p(f"<font size='8.2'>{val}</font>", styles["body"]),
            ]

        cal_coeff_data = [
            _pc(f"<b>{coeff_headers[0]}</b>", f"<b>{coeff_headers[1]}</b>"),
            _pc(coeff_labels.get("interp", _reg_label), reg_unc_text),
            _pc(f"A  [{PHYS_UNIT_SYMBOL}]",                _fmt_sci(_a0, 4)),
            _pc(f"B  [{PHYS_UNIT_SYMBOL}/LSB]",            _fmt_sci(_a1, 4)),
            _pc(f"C  [{PHYS_UNIT_SYMBOL}/LSB<super>2</super>]", _fmt_sci(_a2, 4)),
            _pc(f"u(A)  [{PHYS_UNIT_SYMBOL}]",              _fmt_sci(_u_a0, 2)),
            _pc(f"u(B)  [{PHYS_UNIT_SYMBOL}/LSB]",          _fmt_sci(_u_a1, 2)),
            _pc(f"u(C)  [{PHYS_UNIT_SYMBOL}/LSB<super>2</super>]", _fmt_sci(_u_a2, 2)),
        ]
    else:
        # linear
        _A_cal = SENSOR_MODEL.get("_A_cal", CALIBRATION_RESULT.get("_A_cal", 0.0))
        _B_cal = SENSOR_MODEL.get("_B_cal", CALIBRATION_RESULT.get("_B_cal", 0.0))

        def _pc(txt, val=""):
            return [
                p(f"<font size='8.2'>{txt}</font>", styles["body"]),
                p(f"<font size='8.2'>{val}</font>", styles["body"]),
            ]

        cal_coeff_data = [
            _pc(f"<b>{coeff_headers[0]}</b>", f"<b>{coeff_headers[1]}</b>"),
            _pc(coeff_labels.get("interp", _reg_label), reg_unc_text),
            _pc(f"A  [{PHYS_UNIT_SYMBOL}/LSB]", _fmt_sci(_A_cal, 4)),
            _pc(f"B  [{PHYS_UNIT_SYMBOL}]",      _fmt_sci(_B_cal, 4)),
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
