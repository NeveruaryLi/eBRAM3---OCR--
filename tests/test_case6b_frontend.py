import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Case6BFrontendContractTests(unittest.TestCase):
    def test_case6b_page_and_assets_are_wired(self):
        app_source = (ROOT / "app.py").read_text("utf-8")
        index = (ROOT / "static" / "index.html").read_text("utf-8")
        html = (ROOT / "static" / "case6b.html").read_text("utf-8")
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        css = (ROOT / "static" / "case6b.css").read_text("utf-8")

        self.assertIn('@app.get("/case6b")', app_source)
        self.assertIn('href="/case6b"', index)
        self.assertIn("服务协议草案生成", html)
        self.assertIn("template_file", javascript)
        self.assertIn("material_files", javascript)
        self.assertIn("/case6b/session/", javascript)
        self.assertIn("ebram_c6b_history", javascript)
        self.assertIn("#5d6f91", css.lower())

    def test_page_has_accessible_workflow_controls(self):
        html = (ROOT / "static" / "case6b.html").read_text("utf-8")
        self.assertIn('id="templateInput"', html)
        self.assertIn('id="materialsInput"', html)
        self.assertIn('id="resetTaskBtn"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertNotIn('id="chatInput"', html)


if __name__ == "__main__":
    unittest.main()
