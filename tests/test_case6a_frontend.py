from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Case6AFrontendContractTests(unittest.TestCase):
    def test_page_uses_public_name_and_required_controls(self):
        html = (ROOT / "static" / "case6a.html").read_text(encoding="utf-8")
        self.assertIn("eBRAM 服务指导助手", html)
        self.assertIn("Service Assistant", html)
        self.assertNotIn("Agent K", html)
        for element_id in (
            "newChatBtn",
            "historyList",
            "messagesList",
            "textInput",
            "sendBtn",
            "assistantStatus",
            "sidebarToggle",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("DOMPurify", html)
        self.assertIn("marked", html)

    def test_script_keeps_history_private_and_sanitizes_markdown(self):
        script = (ROOT / "static" / "case6a.js").read_text(encoding="utf-8")
        self.assertIn("ebram_c6a_history", script)
        self.assertIn("DOMPurify.sanitize", script)
        self.assertIn("ebram.org", script)
        self.assertIn("textContent", script)
        self.assertNotIn("conversation_id", script)
        self.assertIn("/case6a/session/chat", script)

    def test_homepage_and_app_expose_case6a(self):
        index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('href="/case6a"', index)
        self.assertIn("服务指导助手", index)
        self.assertIn('@app.get("/case6a")', app)
        self.assertIn("case6a_router", app)

    def test_chat_surface_has_no_decorative_side_rails(self):
        css = (ROOT / "static" / "case6a.css").read_text(encoding="utf-8")
        self.assertNotIn(".c6-main::before", css)


if __name__ == "__main__":
    unittest.main()
