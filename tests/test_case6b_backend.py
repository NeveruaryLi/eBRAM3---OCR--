import io
import json
import unittest
from pathlib import Path

import fitz
from docx import Document
from fastapi.testclient import TestClient

from api.pdf_chat import session_metadata, session_results_store, session_store
from app import app
from cases.case6b_service_agreement import (
    Case6BSession,
    MaterialRecord,
    apply_review_changes,
    build_agent_l_fill_payload,
    build_agent_l_template_payload,
    extract_template_manifest,
    extract_xlsx_markdown,
    image_to_pdf,
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
        self.assertEqual("case6b", session_metadata[payload["session_id"]]["case_type"])

    def test_review_unknown_session_returns_410(self):
        response = self.client.get("/case6b/session/missing/review")
        self.assertEqual(410, response.status_code)
        self.assertEqual("SESSION_EXPIRED", response.json()["detail"]["code"])


if __name__ == "__main__":
    unittest.main()
