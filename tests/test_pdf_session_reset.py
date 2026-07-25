from __future__ import annotations

import asyncio
import unittest

from api.pdf_chat import (
    delete_pdf_session,
    session_metadata,
    session_results_store,
    session_store,
)


class PdfSessionResetTests(unittest.TestCase):
    def tearDown(self) -> None:
        session_store.clear()
        session_results_store.clear()
        session_metadata.clear()

    def test_delete_pdf_session_clears_all_shared_stores_and_is_idempotent(self):
        session_store["session-reset"] = [object()]
        session_results_store["session-reset"] = {"results": {"甲方": "result"}}
        session_metadata["session-reset"] = {"case_type": "case1"}

        response = asyncio.run(delete_pdf_session("session-reset"))

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("session-reset", session_store)
        self.assertNotIn("session-reset", session_results_store)
        self.assertNotIn("session-reset", session_metadata)

        repeated = asyncio.run(delete_pdf_session("session-reset"))
        self.assertEqual(repeated.status_code, 204)


if __name__ == "__main__":
    unittest.main()
