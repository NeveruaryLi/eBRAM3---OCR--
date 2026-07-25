from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from lxml import html


MODULE_PATH = Path(__file__).resolve().parents[1] / "Case_6A_KB" / "build_case6a_kb.py"
SPEC = importlib.util.spec_from_file_location("case6a_kb_builder", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Case6AKnowledgeBuilderTests(unittest.TestCase):
    def test_resolves_pdfjs_file_to_official_absolute_url(self) -> None:
        tree = html.fromstring(
            '<iframe src="/viewer/web/viewer.html?file=/rules/official.pdf?v=2"></iframe>'
        )
        self.assertEqual(
            MODULE.resolve_embedded_pdf("https://www.ebram.org/rules", tree),
            "https://www.ebram.org/rules/official.pdf?v=2",
        )

    def test_pdfjs_url_encodes_spaces_in_official_filename(self) -> None:
        tree = html.fromstring(
            '<iframe src="/viewer/web/viewer.html?file=/guide/User Quick Guide.pdf"></iframe>'
        )
        self.assertEqual(
            MODULE.resolve_embedded_pdf("https://www.ebram.org/guide/", tree),
            "https://www.ebram.org/guide/User%20Quick%20Guide.pdf",
        )

    def test_html_cleaner_keeps_structure_and_removes_navigation(self) -> None:
        payload = b"""
        <html><head><title>Official Service</title></head><body><main>
          <ul class="nav year-nav"><li>Unrelated menu</li></ul>
          <h1>Official Service</h1>
          <p>Exact official wording describing the service, application process, support channel, security measures and published fee information.</p>
          <div class="description">Direct service description kept even when the website does not wrap it in a paragraph.</div>
          <table><tr><th>Fee</th><th>Amount</th></tr><tr><td>Filing</td><td>HKD 1</td></tr></table>
        </main></body></html>
        """
        title, markdown = MODULE.html_to_markdown(payload, "https://www.ebram.org/service")
        self.assertEqual(title, "Official Service")
        self.assertIn("# Official Service", markdown)
        self.assertIn("| Fee | Amount |", markdown)
        self.assertIn("Direct service description kept", markdown)
        self.assertNotIn("Unrelated menu", markdown)

    def test_source_set_covers_all_grounded_customer_questions(self) -> None:
        covered = {
            question
            for source in MODULE.SOURCES
            for question in source.questions.split(";")
            if question
        }
        self.assertTrue({f"Q{index:02d}" for index in range(1, 10)} <= covered)

    def test_extracts_complete_pdf_page_range(self) -> None:
        source = "metadata\n\n## Page 1\n\nOne\n\n## Page 2\n\nTwo\n\n## Page 3\n\nThree\n"
        self.assertEqual(
            MODULE.extract_page_range(source, 2, 3),
            "## Page 2\n\nTwo\n\n## Page 3\n\nThree",
        )


if __name__ == "__main__":
    unittest.main()
