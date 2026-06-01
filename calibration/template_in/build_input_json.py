"""
build_input_json.py
===================
Merges five source JSON files into certificato_funzione_input.json.

Source files and what lives in each
-------------------------------------
measurestream_company.json
    Lab identity only: address, phone, email, accreditation line,
    static legal/organisational text (accreditation_body,
    reproduction_conditions, traceability_statement).

calibration_method.json
    Everything specific to one measurand/procedure:
    sensor & reference instrument data, procedure code, traceability,
    NTC model, coverage factor, method-specific PDF labels (column
    headers, notes_lines, page4 titles, coeff_labels, intro_text),
    method-specific observations and conclusions,
    the "Expanded uncertainty" statement fragment.

client_company.json
    Static client identity reused across all jobs for this customer:
    company name, address, default receiver, location.

job.json
    Per-order data — fill a fresh copy per calibration job:
    certificate/request IDs, all dates, lab environment readings,
    executing and authorising personnel + signature name.

base_input.json
    Two kinds of content:
    (1) Generic structural PDF labels identical across all measurands
        and labs (general_data_labels, procedure_row_labels, etc.).
    (2) Runtime-computed placeholders (0.0 / 0) and the two generic
        observations overwritten by analisi_calib_data.py.

Merge priority (highest wins):  job > client > method > base

Computed fields
---------------
observations   = base observations_base + method observations_method
conclusions    = from calibration_method
statements     = base statements_base + method statements_method
footer_left_text = "{lab department} — Calibration certificate {certificate_id}"

Usage
-----
  python template_in/build_input_json.py
  python template_in/build_input_json.py --method calibration_method_humidity.json
  python template_in/build_input_json.py --client client_acme.json --job job_20260601.json
  python template_in/build_input_json.py --out path/to/other.json
"""
from __future__ import annotations

import argparse
import json
import copy
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str) -> dict:
    path = HERE / name
    return json.loads(path.read_text(encoding="utf-8"))


def strip_comments(obj):
    """Recursively remove '_comment' keys — human docs in source files,
    not wanted in the computed output read by the pipeline."""
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if k != "_comment"}
    if isinstance(obj, list):
        return [strip_comments(i) for i in obj]
    return obj


def merge(
    method_file: str = "calibration_method.json",
    client_file: str = "client_company.json",
    job_file: str = "job.json",
) -> dict:
    ms   = load("measurestream_company.json")
    meth = load(method_file)
    cl   = load(client_file)
    job  = load(job_file)
    base = load("base_input.json")

    # ── company_data ──────────────────────────────────────────────────────
    # Lab identity from measurestream_company.json.
    # document_id is per-job → pulled from job.json.
    company_data = copy.deepcopy(ms["company_data"])
    company_data["document_id"] = job["calibration_specific_data"]["document_id"]

    # ── organization_data ─────────────────────────────────────────────────
    # Static lab legal text from measurestream_company.json;
    # per-job personnel (executed_by, authorised_by, signature_name)
    # from job.json merged on top.
    organization_data = copy.deepcopy(ms["organization_data"])
    organization_data.update(job.get("organization_data", {}))

    # ── sensor_method_template ────────────────────────────────────────────
    # Base from calibration_method.json; per-unit fields (model, serial_number)
    # from job.json merged on top.
    sensor_method_template = copy.deepcopy(meth["sensor_method_template"])
    sensor_method_template.update(job.get("sensor_method_template", {}))

    # ── calibration_specific_data ─────────────────────────────────────────
    # Layer 1 — method-stable fields (certificate_title, conditions).
    calibration_specific_data = copy.deepcopy(
        meth.get("calibration_specific_data_method", {})
    )
    # Layer 2 — static client fields (customer, receiver, location).
    calibration_specific_data.update(cl["calibration_specific_data"])
    # Layer 3 — per-job fields (IDs, dates, environment). Highest priority.
    calibration_specific_data.update(job["calibration_specific_data"])
    # Layer 4 — pipeline-computed fields (page_number, total_pages).
    calibration_specific_data.update(
        base.get("calibration_specific_data_computed", {})
    )

    # ── calculated_calibration_values ─────────────────────────────────────
    # observations = generic base lines + method-specific lines (joined).
    # conclusions  = from calibration_method.json.
    # measurement rows are 0.0 placeholders overwritten by analisi_calib_data.py.
    obs_base   = base["calculated_calibration_values"].get("observations_base", [])
    obs_method = meth.get("calculated_calibration_values_method", {}).get(
        "observations_method", []
    )
    conclusions = meth["calculated_calibration_values_method"]["conclusions"]

    calculated_calibration_values = {
        "measurements": copy.deepcopy(
            base["calculated_calibration_values"]["measurements"]
        ),
        "observations": obs_base + obs_method,
        "conclusions": conclusions,
    }

    # ── pdf_template_data ─────────────────────────────────────────────────
    # Layer 1 — generic structural labels from base_input.json
    #            (layout constants: general_data_labels, procedure_row_labels,
    #            approval_labels, etc.).
    pdf_template_data = copy.deepcopy(base.get("pdf_template_data_base", {}))

    # statements = base statements_base + method statements_method (joined).
    stmt_base   = pdf_template_data.pop("statements_base", [])
    stmt_method = meth.get("pdf_template_data_method", {}).get("statements_method", [])
    pdf_template_data["statements"] = stmt_base + stmt_method

    # Layer 2 — method-specific labels (column headers, notes_lines, page4,
    #            coeff_labels, intro_text) from calibration_method.json.
    #            Skip statements_method — already merged above.
    for key, value in meth.get("pdf_template_data_method", {}).items():
        if key != "statements_method":
            pdf_template_data[key] = value

    # Layer 3 — footer_left_text computed from lab department + job certificate ID.
    cert_id = job["calibration_specific_data"]["certificate_id"]
    pdf_template_data["footer_left_text"] = (
        f"{ms['company_data']['department']} \u2014 Calibration certificate {cert_id}"
    )

    # ── _calibration_result (top-level, outside template_parts) ──────────
    # OLS regression placeholders — overwritten by analisi_calib_data.py.
    calibration_result = copy.deepcopy(base["_calibration_result"])

    result = {
        "template_parts": {
            "company_data": company_data,
            "organization_data": organization_data,
            "sensor_method_template": sensor_method_template,
            "calibration_specific_data": calibration_specific_data,
            "calculated_calibration_values": calculated_calibration_values,
            "pdf_template_data": pdf_template_data,
        },
        "_calibration_result": calibration_result,
    }
    return strip_comments(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build certificato_funzione_input.json from source JSONs."
    )
    parser.add_argument(
        "--method",
        default="calibration_method.json",
        help="Method/instrument JSON (default: calibration_method.json)",
    )
    parser.add_argument(
        "--client",
        default="client_company.json",
        help="Client identity JSON (default: client_company.json)",
    )
    parser.add_argument(
        "--job",
        default="job.json",
        help="Per-job data JSON (default: job.json)",
    )
    parser.add_argument(
        "--out",
        default=str(HERE / "certificato_funzione_input.json"),
        help="Output path (default: template_in/certificato_funzione_input.json)",
    )
    args = parser.parse_args()

    merged = merge(
        method_file=args.method,
        client_file=args.client,
        job_file=args.job,
    )
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
