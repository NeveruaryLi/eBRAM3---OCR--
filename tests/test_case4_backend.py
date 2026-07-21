from __future__ import annotations

import asyncio
import base64
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import AsyncMock, patch

import fitz
import httpx
from starlette.datastructures import Headers, UploadFile

from api.case4_routes import upload_translation_pdf
from api.pdf_chat import session_metadata, session_store
from cases.case4_pdf_translation import (
    Case4PdfDocument,
    Case4PdfTranslationHandler,
    PDF_RESULT_KEY,
    assemble_translated_pdf,
    build_agent_i_payload,
    extract_assistant_image_reference,
    extract_blocking_image_reference,
    render_page_png,
    validate_case4_pdf,
    _image_bytes_from_reference,
    _post_with_retry,
)


def _pdf_bytes(page_sizes: list[tuple[float, float]] | None = None) -> bytes:
    doc = fitz.open()
    for width, height in page_sizes or [(300, 500)]:
        page = doc.new_page(width=width, height=height)
        page.insert_text((30, 50), "Sales Contract HKD 8,160")
    data = doc.tobytes()
    doc.close()
    return data


def _png_bytes(width: int = 300, height: int = 500) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pixmap.clear_with(255)
    return pixmap.tobytes("png")


class Case4ValidationTests(unittest.TestCase):
    def test_accepts_pdf_and_records_original_page_sizes(self):
        document = validate_case4_pdf(
            _pdf_bytes([(300, 500), (612, 792)]),
            "contract.pdf",
            "en_to_zh_tw",
        )
        self.assertEqual(document.page_count, 2)
        self.assertEqual(document.page_sizes, [(300.0, 500.0), (612.0, 792.0)])
        self.assertEqual(document.direction, "en_to_zh_tw")

    def test_rejects_bad_direction_non_pdf_and_more_than_30_pages(self):
        with self.assertRaisesRegex(ValueError, "翻译方向"):
            validate_case4_pdf(_pdf_bytes(), "a.pdf", "auto")
        with self.assertRaisesRegex(ValueError, "PDF"):
            validate_case4_pdf(b"not a pdf", "a.pdf", "en_to_zh_tw")
        with self.assertRaisesRegex(ValueError, "30"):
            validate_case4_pdf(_pdf_bytes([(100, 100)] * 31), "a.pdf", "en_to_zh_tw")

    def test_rejects_files_larger_than_25_mb(self):
        with self.assertRaisesRegex(ValueError, "25 MB"):
            validate_case4_pdf(b"%PDF-" + b"0" * (25 * 1024 * 1024), "a.pdf", "en_to_zh_tw")

    def test_rejects_password_protected_pdf(self):
        doc = fitz.open()
        doc.new_page()
        encrypted = doc.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="secret",
        )
        doc.close()
        with self.assertRaisesRegex(ValueError, "加密"):
            validate_case4_pdf(encrypted, "protected.pdf", "en_to_zh_tw")

    def test_upload_endpoint_persists_case4_metadata(self):
        upload = UploadFile(
            BytesIO(_pdf_bytes([(300, 500), (300, 500)])),
            filename="contract.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )
        result = asyncio.run(upload_translation_pdf(upload, "zh_tw_to_en"))
        try:
            self.assertEqual(result["page_count"], 2)
            self.assertEqual(session_metadata[result["session_id"]]["case_type"], "case4")
            self.assertIsInstance(session_store[result["session_id"]][0], Case4PdfDocument)
        finally:
            session_store.pop(result["session_id"], None)
            session_metadata.pop(result["session_id"], None)


class AgentContractTests(unittest.TestCase):
    def test_payload_uses_one_image_and_disables_memory(self):
        payload = build_agent_i_payload("conv-1", _png_bytes(), "en_to_zh_tw", 2, 4)
        self.assertEqual(payload["response_mode"], "blocking")
        self.assertEqual(payload["conversation_config"], {
            "short_term_memory": False,
            "long_term_memory": False,
        })
        content = payload["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["image"][0]["format"], "png")
        self.assertIn("ENGLISH_TO_TRADITIONAL_CHINESE", content[0]["text"])
        self.assertIn("Page 2 of 4", content[0]["text"])

    def test_extracts_direct_url_or_base64_from_blocking_response(self):
        url = "https://assets.gptbots.ai/result.png"
        self.assertEqual(
            extract_blocking_image_reference({"output": [{"content": {"image": url}}]}),
            url,
        )
        image = {"base64_content": "aGVsbG8=", "format": "png"}
        self.assertEqual(
            extract_blocking_image_reference({"output": [{"content": {"image": [image]}}]}),
            image,
        )

    def test_messages_fallback_never_selects_user_upload(self):
        user_url = "https://assets.gptbots.ai/upload.png"
        assistant_url = "https://assets.gptbots.ai/translated.png"
        response = {
            "conversation_content": [
                {"role": "user", "content": [{"branch_content": [
                    {"type": "image", "image": [{"url": user_url}]}
                ]}]},
                {"role": "assistant", "message_id": "reply-1", "content": [{"branch_content": [
                    {"type": "image", "image": [{"url": assistant_url}]}
                ]}]},
            ]
        }
        self.assertEqual(
            extract_assistant_image_reference(response, message_id="reply-1"),
            assistant_url,
        )

    def test_base64_output_is_decoded_and_unofficial_url_is_rejected(self):
        png = _png_bytes()
        decoded = asyncio.run(_image_bytes_from_reference(
            None,
            {"base64_content": base64.b64encode(png).decode("ascii"), "format": "png"},
        ))
        self.assertEqual(decoded, png)
        with self.assertRaisesRegex(ValueError, "官方"):
            asyncio.run(_image_bytes_from_reference(None, "https://example.com/output.png"))

    def test_retryable_statuses_are_retried_but_401_is_not(self):
        statuses = iter([429, 500, 200])
        calls = 0

        def retry_transport(request):
            nonlocal calls
            calls += 1
            return httpx.Response(next(statuses), json={"ok": True}, request=request)

        async def retry_case():
            async with httpx.AsyncClient(transport=httpx.MockTransport(retry_transport)) as client:
                with patch(
                    "cases.case4_pdf_translation.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    return await _post_with_retry(
                        client,
                        "https://api-sg.gptbots.ai/v2/conversation/message",
                        headers={},
                        json={},
                    )

        self.assertEqual(asyncio.run(retry_case()).status_code, 200)
        self.assertEqual(calls, 3)

        unauthorized_calls = 0

        def unauthorized_transport(request):
            nonlocal unauthorized_calls
            unauthorized_calls += 1
            return httpx.Response(401, json={}, request=request)

        async def unauthorized_case():
            async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized_transport)) as client:
                return await _post_with_retry(
                    client,
                    "https://api-sg.gptbots.ai/v2/conversation/message",
                    headers={},
                    json={},
                )

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(unauthorized_case())
        self.assertEqual(unauthorized_calls, 1)


class PdfPipelineTests(unittest.TestCase):
    def test_render_page_uses_rgb_without_alpha(self):
        doc = fitz.open(stream=_pdf_bytes(), filetype="pdf")
        png, dpi = render_page_png(doc[0], max_bytes=10 * 1024 * 1024)
        pixmap = fitz.Pixmap(png)
        self.assertEqual(dpi, 150)
        self.assertEqual(pixmap.alpha, 0)
        doc.close()

    def test_render_page_falls_back_through_configured_dpis(self):
        doc = fitz.open(stream=_pdf_bytes(), filetype="pdf")
        calls: list[int] = []

        def oversized_renderer(page, dpi):
            calls.append(dpi)
            return b"x" * (100 if dpi > 96 else 10)

        png, dpi = render_page_png(doc[0], max_bytes=20, renderer=oversized_renderer)
        self.assertEqual((len(png), dpi), (10, 96))
        self.assertEqual(calls, [150, 120, 96])
        doc.close()

    def test_assembled_pdf_preserves_page_order_and_sizes(self):
        sizes = [(300.0, 500.0), (612.0, 792.0)]
        result = assemble_translated_pdf([_png_bytes(), _png_bytes(400, 300)], sizes)
        doc = fitz.open(stream=result, filetype="pdf")
        self.assertEqual(doc.page_count, 2)
        self.assertEqual((doc[0].rect.width, doc[0].rect.height), sizes[0])
        self.assertEqual((doc[1].rect.width, doc[1].rect.height), sizes[1])
        doc.close()


class HandlerContractTests(unittest.TestCase):
    def test_report_is_pdf_only_and_followup_is_rejected(self):
        handler = Case4PdfTranslationHandler()
        pdf = _pdf_bytes()
        results = {
            "s1": {
                "case_type": "case4",
                "created_at": datetime.utcnow(),
                "results": {PDF_RESULT_KEY: {
                    "pdf_bytes": pdf,
                    "filename": "contract_譯文.pdf",
                    "conversation_id": "conv-1",
                }},
            }
        }
        self.assertEqual(handler.get_downloadable_keys("s1", results), [PDF_RESULT_KEY])
        generated = asyncio.run(handler.generate_report("s1", PDF_RESULT_KEY, "pdf", results))
        self.assertEqual(generated[0], pdf)
        self.assertEqual(generated[2], "application/pdf")
        with self.assertRaisesRegex(ValueError, "只提供 PDF"):
            asyncio.run(handler.generate_report("s1", PDF_RESULT_KEY, "docx", results))
        with self.assertRaisesRegex(ValueError, "不支持追问"):
            asyncio.run(handler.followup_chat("s1", "hello", None, results))


if __name__ == "__main__":
    unittest.main()
