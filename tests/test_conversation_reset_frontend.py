from __future__ import annotations

import re
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
                self.assertIn("const backendSessionId = session.backendSessionId || null;", script)
                self.assertIn("const backendSessionId = target?.backendSessionId || null;", script)
                self.assertNotIn("id === currentSessionId ? multiSessionId", script)
                self.assertNotIn("id === currentSessionId ? scrapeSessionId", script)
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

    def test_all_history_pages_use_shared_three_dot_delete_menu(self):
        common = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
        self.assertIn("createHistoryMenu", common)
        self.assertIn("history-menu-btn", common)
        self.assertIn("history-dropdown-item danger", common)
        self.assertIn("activeHistoryMenuButton", common)
        self.assertIn("closeHistoryMenus({ restoreFocus: true })", common)
        for case_name in ("case1", "case2", "case5", "case6a", "case6b"):
            with self.subTest(case=case_name):
                script = (ROOT / "static" / f"{case_name}.js").read_text(
                    encoding="utf-8"
                )
                self.assertIn("createHistoryMenu", script)

    def test_case1_case2_case5_history_switches_are_state_isolated(self):
        backend_state = {
            "case1": "multiSessionId",
            "case2": "multiSessionId",
            "case5": "scrapeSessionId",
        }
        for case_name, state_name in backend_state.items():
            with self.subTest(case=case_name):
                script = (ROOT / "static" / f"{case_name}.js").read_text(
                    encoding="utf-8"
                )
                match = re.search(
                    r"function switchToSession\(id\) \{(?P<body>.*?)\n\}",
                    script,
                    re.S,
                )
                self.assertIsNotNone(match)
                body = match.group("body")
                self.assertIn("if (busy) return;", body)
                self.assertIn("if (id === currentSessionId) return;", body)
                self.assertIn(
                    f"{state_name} = session.backendSessionId || null;",
                    body,
                )
                self.assertIn(
                    "resetCurrentView({ preserveHistory: true });",
                    body,
                )
                self.assertNotIn("setBusy(false);", body)


if __name__ == "__main__":
    unittest.main()
