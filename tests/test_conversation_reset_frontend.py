from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConversationResetFrontendTests(unittest.TestCase):
    def test_all_conversation_pages_except_case4_expose_reset_control(self):
        for case_name in ("case1", "case2", "case5", "case6a"):
            with self.subTest(case=case_name):
                html = (ROOT / "static" / f"{case_name}.html").read_text(encoding="utf-8")
                self.assertIn('id="resetConversationBtn"', html)
                self.assertIn("重置当前对话", html)

        case4 = (ROOT / "static" / "case4.html").read_text(encoding="utf-8")
        self.assertNotIn('id="resetConversationBtn"', case4)

    def test_case6a_exposes_mobile_reset_control(self):
        html = (ROOT / "static" / "case6a.html").read_text(encoding="utf-8")
        self.assertIn('id="mobileResetBtn"', html)
        self.assertIn('aria-label="重置当前对话"', html)

    def test_scripts_reset_active_history_in_place_and_delete_backend_session(self):
        for case_name in ("case1", "case2", "case5"):
            with self.subTest(case=case_name):
                script = (ROOT / "static" / f"{case_name}.js").read_text(encoding="utf-8")
                self.assertIn("resetCurrentConversation", script)
                self.assertIn("resetConversationBtn", script)
                self.assertIn("/pdf/session/", script)
                self.assertIn("session.messages = []", script)
                self.assertIn("session.title = '新对话'", script)
                self.assertNotIn("resetConversationBtn.addEventListener('click', createNewSession)", script)

        case6a = (ROOT / "static" / "case6a.js").read_text(encoding="utf-8")
        self.assertIn("resetCurrentConversation", case6a)
        self.assertIn("/case6a/session/", case6a)
        self.assertIn("session.messages = []", case6a)
        self.assertIn("session.title = '新对话'", case6a)

    def test_reset_control_uses_shared_visual_language(self):
        styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".reset-chat-btn", styles)
        self.assertIn(".reset-chat-btn:disabled", styles)


if __name__ == "__main__":
    unittest.main()
