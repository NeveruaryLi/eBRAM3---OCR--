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
        self.assertNotIn("form_entries", javascript)
        self.assertNotIn("formEntriesInput", html)
        self.assertIn("removeTemplate", javascript)
        self.assertIn("removeMaterial", javascript)
        self.assertIn("addMaterials", javascript)
        self.assertIn("state.busy || Boolean(state.sessionId)", javascript)
        self.assertIn("if (!response.ok)", javascript)
        self.assertIn("history-title-button", javascript)
        self.assertIn("title.type = 'button'", javascript)
        self.assertIn("syncUploadControls", javascript)
        self.assertIn("clearView(false);", javascript)
        self.assertIn("conflict_resolutions", javascript)
        self.assertIn("accepted_field_ids", javascript)
        self.assertIn("/preview", javascript)
        self.assertIn("createPreview", javascript)
        self.assertIn("setReviewDirty", javascript)
        self.assertIn("optional", javascript)
        self.assertIn("/case6b/session/", javascript)
        self.assertIn("ebram_c6b_history", javascript)
        self.assertIn("#5d6f91", css.lower())

    def test_page_has_accessible_workflow_controls(self):
        html = (ROOT / "static" / "case6b.html").read_text("utf-8")
        self.assertIn('id="templateInput"', html)
        self.assertIn('id="materialsInput"', html)
        self.assertIn('id="templatePreflight"', html)
        self.assertNotIn('id="templateConfirmBtn"', html)
        self.assertIn('id="conflictPanel"', html)
        self.assertIn('id="previewFrame"', html)
        self.assertIn('id="reviewFilters"', html)
        self.assertIn('id="dirtyBadge"', html)
        self.assertIn('保存并刷新预览', html)
        self.assertIn('生成正式文档', html)
        self.assertIn('id="resetTaskBtn"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertNotIn('id="chatInput"', html)


if __name__ == "__main__":
    unittest.main()
