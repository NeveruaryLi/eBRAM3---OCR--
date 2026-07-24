import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from docx import Document
from fastapi.testclient import TestClient

from api.pdf_chat import session_metadata, session_results_store, session_store
from app import app
from cases.case6b_service_agreement import (
    Case6BSession,
    MaterialRecord,
    apply_review_changes,
    build_template_preflight,
    build_agent_l_fill_payload,
    build_agent_l_template_payload,
    extract_csv_markdown,
    extract_docx_markdown,
    extract_template_manifest,
    extract_xlsx_markdown,
    image_to_pdf,
    ocr_submission_filename,
    parse_agent_json,
    render_draft_docx,
    validate_material,
    validate_template,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "input_example" / "Usecase 6B" / "Input documents"
TEMPLATE = SAMPLES / "UC6B_0_Format - Service Agreement.docx"
FIXTURES = ROOT / "GPTbots_.bot" / "generated" / "case6b" / "fixtures"


class Case6BCoreTests(unittest.TestCase):
    def test_sample_template_has_37_stable_fields(self):
        template = validate_template(TEMPLATE.read_bytes(), TEMPLATE.name)
        manifest = extract_template_manifest(template.content)
        self.assertEqual(37, len(manifest["fields"]))
        self.assertEqual("p002_f01", manifest["fields"][0]["field_id"])
        self.assertEqual(
            "t000_r000_c001_p003_f01",
            manifest["fields"][-1]["field_id"],
        )
        self.assertEqual("service_agreement_v1", manifest["profile_id"])
        self.assertTrue(manifest["template_confirmed"])
        self.assertEqual("30 days", manifest["template_defaults"]["termination_notice"])
        self.assertEqual("10 days", manifest["template_defaults"]["materials_return"])

    def test_bracket_placeholders_are_detected_for_unknown_templates(self):
        output = io.BytesIO()
        document = Document()
        document.add_heading("Consulting Agreement")
        document.add_paragraph("Between [Provider_Name] and [Client_Name].")
        document.save(output)
        template = validate_template(output.getvalue(), "brackets.docx")
        manifest = extract_template_manifest(template.content)
        self.assertEqual(2, len(manifest["fields"]))
        self.assertFalse(manifest["template_confirmed"])
        self.assertEqual("bracket", manifest["fields"][0]["placeholder_type"])
        preflight = build_template_preflight(manifest)
        self.assertEqual("detected", preflight["recognition_mode"])
        self.assertEqual(2, preflight["field_count"])

    def test_rejects_docx_without_placeholders(self):
        output = io.BytesIO()
        document = Document()
        document.add_paragraph("No blank fields")
        document.save(output)
        with self.assertRaisesRegex(ValueError, "下划线"):
            validate_template(output.getvalue(), "plain.docx")

    def test_extracts_xlsx_with_sheet_and_cell_evidence(self):
        content = (
            SAMPLES / "UC6B_5_Scope, Service Components & Pricing Breakdown.xlsx"
        ).read_bytes()
        material = validate_material(content, "pricing.xlsx")
        markdown, units = extract_xlsx_markdown(material.content, material.filename)
        self.assertEqual(1, units)
        self.assertIn("Service Component,Included,KPIs", markdown)
        self.assertIn("A2: Remote Helpdesk (8×5)", markdown)
        self.assertIn("F5: 10000", markdown)

    def test_extracts_docx_and_csv_materials_with_source_locations(self):
        docx = io.BytesIO()
        document = Document()
        document.add_paragraph("Provider CR Number: 2897654")
        document.save(docx)
        docx_material = validate_material(docx.getvalue(), "intake.docx")
        docx_markdown, docx_units = extract_docx_markdown(
            docx_material.content, docx_material.filename
        )
        csv_content = b"service,price\\nHelpdesk,HKD 20000 per month\\n"
        csv_material = validate_material(csv_content, "pricing.csv")
        csv_markdown, csv_units = extract_csv_markdown(
            csv_material.content, csv_material.filename
        )
        self.assertEqual("docx", docx_material.file_type)
        self.assertEqual(1, docx_units)
        self.assertIn("paragraph:1", docx_markdown)
        self.assertEqual("csv", csv_material.file_type)
        self.assertEqual(1, csv_units)
        self.assertIn("row:2", csv_markdown)

    def test_image_is_wrapped_as_single_page_pdf(self):
        content = (SAMPLES / "UC6B_9_Teams_chatlog.jfif").read_bytes()
        material = validate_material(content, "chat.jfif")
        pdf = image_to_pdf(material.content)
        document = fitz.open(stream=pdf, filetype="pdf")
        try:
            self.assertEqual(1, document.page_count)
            self.assertGreater(document[0].rect.width, 0)
        finally:
            document.close()

    def test_wrapped_images_are_submitted_to_ocr_with_pdf_filename(self):
        self.assertEqual("chat.pdf", ocr_submission_filename("chat.jfif", "image"))
        self.assertEqual(
            "evidence.pdf",
            ocr_submission_filename("evidence.pdf", "pdf"),
        )

    def test_agent_l_payloads_have_exact_two_phase_contract(self):
        template = validate_template(TEMPLATE.read_bytes(), TEMPLATE.name)
        manifest = extract_template_manifest(template.content)
        first = build_agent_l_template_payload("conv", template, manifest)
        second = build_agent_l_fill_payload(
            "conv",
            {"template_language": "en", "fields": manifest["fields"]},
            {"sources": []},
        )
        self.assertIn("[CASE6B_PHASE:TEMPLATE_PARSE]", json.dumps(first))
        self.assertIn("[CASE6B_PHASE:FIELD_FILL]", json.dumps(second))
        self.assertTrue(first["conversation_config"]["short_term_memory"])
        self.assertTrue(second["conversation_config"]["short_term_memory"])

    def test_agent_json_parser_accepts_fenced_object(self):
        parsed = parse_agent_json('```json\n{"fields": [], "conflicts": []}\n```')
        self.assertEqual([], parsed["fields"])

    def test_review_edit_invalidates_generated_files_and_increments_version(self):
        fields = json.loads((FIXTURES / "expected_fill.json").read_text("utf-8"))[
            "fields"
        ]
        session = Case6BSession(
            template=validate_template(TEMPLATE.read_bytes(), TEMPLATE.name),
            materials=[
                MaterialRecord("m1", "facts.pdf", "pdf", b"%PDF-1.4", 1)
            ],
            field_manifest=json.loads(
                (FIXTURES / "template_field_manifest.json").read_text("utf-8")
            ),
            fields=fields,
            review_version=1,
            generated_docx=b"old",
            generated_pdf=b"old",
        )
        updated = apply_review_changes(
            session,
            version=1,
            field_updates=[{"field_id": "p002_f01", "value": "1 August 2026"}],
            service_rows=[],
        )
        self.assertEqual(2, updated)
        changed = next(f for f in session.fields if f["field_id"] == "p002_f01")
        self.assertEqual("USER_CONFIRMED", changed["status"])
        self.assertEqual("1 August 2026", changed["value"])
        self.assertIsNone(session.generated_docx)
        self.assertIsNone(session.generated_pdf)

    def test_unchanged_review_value_keeps_agent_evidence(self):
        fields = json.loads((FIXTURES / "expected_fill.json").read_text("utf-8"))[
            "fields"
        ]
        session = Case6BSession(
            template=validate_template(TEMPLATE.read_bytes(), TEMPLATE.name),
            materials=[],
            field_manifest=json.loads(
                (FIXTURES / "template_field_manifest.json").read_text("utf-8")
            ),
            fields=fields,
            review_version=1,
        )
        target = next(field for field in fields if field["field_id"] == "p002_f02")
        evidence = list(target["evidence"])
        apply_review_changes(
            session,
            version=1,
            field_updates=[
                {"field_id": "p002_f02", "value": target["value"]}
            ],
            service_rows=[],
        )
        self.assertEqual("FILLED", target["status"])
        self.assertEqual(evidence, target["evidence"])

    def test_docx_fill_preserves_signature_blanks_and_removes_unused_services(self):
        fields = json.loads((FIXTURES / "expected_fill.json").read_text("utf-8"))[
            "fields"
        ]
        output = render_draft_docx(
            TEMPLATE.read_bytes(),
            json.loads(
                (FIXTURES / "template_field_manifest.json").read_text("utf-8")
            ),
            fields,
            "en",
        )
        document = Document(io.BytesIO(output))
        text = "\n".join(p.text for p in document.paragraphs)
        self.assertIn("ServiceStar Solutions Limited", text)
        self.assertIn("[TO BE CONFIRMED]", text)
        self.assertEqual(4, text.count("(Price:"))
        signature_text = "\n".join(
            cell.text for table in document.tables for row in table.rows for cell in row.cells
        )
        self.assertIn("Signature:_________________________", signature_text)

    def test_review_supports_optional_and_blank_service_rows(self):
        manifest = json.loads(
            (FIXTURES / "template_field_manifest.json").read_text("utf-8")
        )
        fields = json.loads((FIXTURES / "expected_fill.json").read_text("utf-8"))[
            "fields"
        ]
        session = Case6BSession(
            template=validate_template(TEMPLATE.read_bytes(), TEMPLATE.name),
            materials=[],
            field_manifest=manifest,
            fields=fields,
            review_version=1,
        )
        apply_review_changes(
            session,
            version=1,
            field_updates=[],
            service_rows=[
                {
                    "group_index": 5,
                    "action": "optional",
                    "name": "Optional On-site Support",
                    "price": "HKD 900 per hour (minimum 2 hours)",
                },
                {
                    "group_index": 7,
                    "action": "blank",
                    "name": "",
                    "price": "",
                },
            ],
        )
        row5 = [
            item
            for item in session.fields
            if next(
                field
                for field in manifest["fields"]
                if field["field_id"] == item["field_id"]
            ).get("group_index")
            == 5
        ]
        row7 = [
            item
            for item in session.fields
            if next(
                field
                for field in manifest["fields"]
                if field["field_id"] == item["field_id"]
            ).get("group_index")
            == 7
        ]
        self.assertTrue(all(item["service_action"] == "optional" for item in row5))
        self.assertTrue(all(item["status"] == "KEEP_BLANK" for item in row7))
        self.assertTrue(all(item["source_type"] == "user_confirmed" for item in row5))


class Case6BRouteTests(unittest.TestCase):
    def setUp(self):
        session_store.clear()
        session_results_store.clear()
        session_metadata.clear()
        self.client = TestClient(app)

    def test_upload_rejects_pdf_template(self):
        response = self.client.post(
            "/case6b/session/upload",
            files=[
                ("template_file", ("template.pdf", b"%PDF-1.4", "application/pdf")),
                (
                    "material_files",
                    ("facts.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"),
                ),
            ],
        )
        self.assertEqual(400, response.status_code)

    def test_upload_sample_returns_session_and_37_fields(self):
        response = self.client.post(
            "/case6b/session/upload",
            files=[
                (
                    "template_file",
                    (TEMPLATE.name, TEMPLATE.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                ),
                (
                    "material_files",
                    (
                        "pricing.xlsx",
                        (
                            SAMPLES
                            / "UC6B_5_Scope, Service Components & Pricing Breakdown.xlsx"
                        ).read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual(37, payload["placeholder_count"])
        self.assertEqual(1, len(payload["materials"]))
        self.assertEqual("service_agreement_v1", payload["template"]["profile_id"])
        self.assertTrue(payload["template"]["confirmed"])
        self.assertIn("defaults", payload["template"])
        self.assertEqual("case6b", session_metadata[payload["session_id"]]["case_type"])

    def test_unknown_template_requires_preflight_confirmation(self):
        template_stream = io.BytesIO()
        document = Document()
        document.add_paragraph("Agreement between [Provider_Name] and [Client_Name].")
        document.save(template_stream)
        response = self.client.post(
            "/case6b/session/upload",
            files=[
                (
                    "template_file",
                    (
                        "unknown.docx",
                        template_stream.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
                (
                    "material_files",
                    (
                        "pricing.xlsx",
                        (
                            SAMPLES
                            / "UC6B_5_Scope, Service Components & Pricing Breakdown.xlsx"
                        ).read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )
        self.assertEqual(200, response.status_code, response.text)
        session_id = response.json()["session_id"]
        self.assertFalse(response.json()["template"]["confirmed"])
        detail = self.client.get(f"/case6b/session/{session_id}/template")
        self.assertEqual(200, detail.status_code)
        confirmed = self.client.patch(
            f"/case6b/session/{session_id}/template",
            json={"version": 1, "confirmed": True},
        )
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        self.assertTrue(confirmed.json()["confirmed"])

    def test_review_unknown_session_returns_410(self):
        response = self.client.get("/case6b/session/missing/review")
        self.assertEqual(410, response.status_code)
        self.assertEqual("SESSION_EXPIRED", response.json()["detail"]["code"])

    def _ready_session(self) -> str:
        session_id = "case6b-ready"
        manifest = json.loads(
            (FIXTURES / "template_field_manifest.json").read_text("utf-8")
        )
        fields = json.loads((FIXTURES / "expected_fill.json").read_text("utf-8"))[
            "fields"
        ]
        session = Case6BSession(
            template=validate_template(TEMPLATE.read_bytes(), TEMPLATE.name),
            materials=[],
            field_manifest=manifest,
            fields=fields,
            review_version=1,
        )
        session_store[session_id] = [session]
        session_metadata[session_id] = {"case_type": "case6b"}
        session_results_store[session_id] = {
            "case_type": "case6b",
            "results": {"协议草案": {"conversation_id": "private"}},
        }
        return session_id

    def test_review_rejects_stale_version_and_signature_edit(self):
        session_id = self._ready_session()
        stale = self.client.patch(
            f"/case6b/session/{session_id}/review",
            json={"version": 9, "field_updates": [], "service_rows": []},
        )
        self.assertEqual(409, stale.status_code)
        signature = self.client.patch(
            f"/case6b/session/{session_id}/review",
            json={
                "version": 1,
                "field_updates": [
                    {
                        "field_id": "t000_r000_c000_p002_f01",
                        "value": "signed",
                    }
                ],
                "service_rows": [],
            },
        )
        self.assertEqual(400, signature.status_code)

    def test_finalize_requires_unresolved_confirmation_and_keeps_docx_on_pdf_failure(self):
        session_id = self._ready_session()
        blocked = self.client.post(
            f"/case6b/session/{session_id}/finalize",
            json={"version": 1, "allow_unresolved": False},
        )
        self.assertEqual(409, blocked.status_code)
        with patch(
            "api.case6b_routes.convert_docx_to_pdf",
            side_effect=RuntimeError("PDF unavailable"),
        ):
            finalized = self.client.post(
                f"/case6b/session/{session_id}/finalize",
                json={"version": 1, "allow_unresolved": True},
            )
        self.assertEqual(200, finalized.status_code, finalized.text)
        self.assertTrue(finalized.json()["docx_ready"])
        self.assertFalse(finalized.json()["pdf_ready"])
        report = self.client.post(
            "/pdf/session/report",
            data={
                "session_id": session_id,
                "party": "协议草案",
                "output_format": "docx",
            },
        )
        self.assertEqual(200, report.status_code)
        self.assertTrue(report.content.startswith(b"PK"))

    def test_finalize_is_blocked_by_unresolved_conflicts(self):
        session_id = self._ready_session()
        session_store[session_id][0].conflicts = [
            {
                "conflict_id": "term-1",
                "semantic_key": "agreement_term",
                "candidates": [{"value": "12 months"}, {"value": "24 months"}],
                "resolved": False,
            }
        ]
        blocked = self.client.post(
            f"/case6b/session/{session_id}/finalize",
            json={"version": 1, "allow_unresolved": True},
        )
        self.assertEqual(409, blocked.status_code)
        self.assertEqual("UNRESOLVED_CONFLICTS", blocked.json()["detail"]["code"])

    def test_single_material_retry_rejects_unknown_or_nonfailed_target(self):
        session_id = self._ready_session()
        session = session_store[session_id][0]
        session.materials = [
            validate_material(
                (SAMPLES / "UC6B_6_Heads of Terms.pdf").read_bytes(),
                "UC6B_6_Heads of Terms.pdf",
            )
        ]
        unknown = self.client.post(
            f"/case6b/session/{session_id}/retry?material_id=missing"
        )
        self.assertEqual(404, unknown.status_code)
        not_failed = self.client.post(
            f"/case6b/session/{session_id}/retry"
            f"?material_id={session.materials[0].material_id}"
        )
        self.assertEqual(400, not_failed.status_code)
        self.assertEqual(
            "MATERIAL_NOT_FAILED",
            not_failed.json()["detail"]["code"],
        )


if __name__ == "__main__":
    unittest.main()
