from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from api.pdf_chat import DocResult, _ask_agent_b_with_docs
from cases.case1_party_questions import Case1Handler


class AgentBDocumentRetryTests(unittest.TestCase):
    def test_retries_transient_server_errors_before_returning_reply(self):
        attempts = 0

        def transport(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(
                    500,
                    json={"error": "temporary model failure"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"answer": "综合分析完成"},
                request=request,
            )

        async def exercise() -> str:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(transport)
            ) as client:
                return await _ask_agent_b_with_docs(
                    client,
                    "conversation-test",
                    "请进行综合分析",
                    [
                        {
                            "base64_content": "IyBUZXN0",
                            "format": "md",
                            "name": "test.md",
                        }
                    ],
                )

        with patch("api.pdf_chat.asyncio.sleep", new=AsyncMock()) as sleep:
            result = asyncio.run(exercise())

        self.assertEqual(result, "综合分析完成")
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.await_count, 2)


class Case1AgentBErrorMessageTests(unittest.TestCase):
    def test_exhausted_server_error_is_user_friendly_and_hides_platform_url(self):
        request = httpx.Request(
            "POST",
            "https://api-sg.gptbots.ai/v2/conversation/message",
        )
        response = httpx.Response(500, request=request)
        platform_error = httpx.HTTPStatusError(
            "Server error '500 Internal Server Error' for url "
            "'https://api-sg.gptbots.ai/v2/conversation/message'",
            request=request,
            response=response,
        )

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, *_, **__):
                return httpx.Response(
                    200,
                    json={"conversation_id": "conversation-private"},
                    request=httpx.Request("POST", "https://example.invalid"),
                )

        async def collect_events():
            handler = Case1Handler()
            docs = {
                "session-test": [
                    DocResult(
                        filename="party-a.pdf",
                        summaries=["第一页摘要"],
                        merged_fields={"amounts": []},
                        party="甲方",
                    )
                ]
            }
            with patch(
                "cases.case1_party_questions.httpx.AsyncClient",
                return_value=FakeClient(),
            ), patch(
                "api.pdf_chat._ask_agent_b_with_docs",
                new=AsyncMock(side_effect=platform_error),
            ):
                return [
                    event
                    async for event in handler.analyze(
                        "session-test",
                        docs,
                        {},
                    )
                ]

        events = asyncio.run(collect_events())
        error_message = next(
            event.data["message"] for event in events if event.type == "error"
        )

        self.assertIn("服务暂时不可用", error_message)
        self.assertIn("自动重试", error_message)
        self.assertNotIn("api-sg.gptbots.ai", error_message)
        self.assertNotIn("conversation-private", error_message)


if __name__ == "__main__":
    unittest.main()
