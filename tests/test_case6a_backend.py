from __future__ import annotations

import asyncio
import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import httpx

from api.case6a_routes import (
    Case6AChatBody,
    Case6ASession,
    build_agent_k_payload,
    case6a_sessions,
    chat_case6a,
    create_case6a_session,
    delete_case6a_session,
    extract_assistant_text,
    extract_latest_assistant_message,
    extract_latest_assistant_text,
    _send_agent_k_message,
)


class Case6AAgentContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        case6a_sessions.clear()

    def test_payload_is_text_only_blocking_with_short_memory(self):
        payload = build_agent_k_payload("conv-private", "What services are available?")
        self.assertEqual(payload["response_mode"], "blocking")
        self.assertEqual(
            payload["conversation_config"],
            {"short_term_memory": True, "long_term_memory": False},
        )
        self.assertEqual(
            payload["messages"],
            [{"role": "user", "content": [{"type": "text", "text": "What services are available?"}]}],
        )

    def test_http_client_request_urls_are_not_logged_at_info(self):
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)

    def test_extracts_blocking_text_and_latest_assistant_fallback(self):
        self.assertEqual(
            extract_assistant_text(
                {"output": [{"content": [{"type": "text", "text": "Official answer"}]}]}
            ),
            "Official answer",
        )
        detail = {
            "conversation_content": [
                {"role": "assistant", "content": [{"type": "text", "text": "Old"}]},
                {"role": "user", "content": [{"type": "text", "text": "Question"}]},
                {
                    "role": "assistant",
                    "content": [{"branch_content": [{"type": "text", "text": "Latest"}]}],
                },
            ]
        }
        self.assertEqual(extract_latest_assistant_text(detail), "Latest")
        marker, text = extract_latest_assistant_message(detail)
        self.assertEqual(text, "Latest")
        self.assertNotEqual(marker, "")

    def test_assistant_message_marker_distinguishes_new_replies(self):
        old = {
            "conversation_content": [
                {
                    "role": "assistant",
                    "message_id": "reply-old",
                    "content": [{"type": "text", "text": "Old answer"}],
                }
            ]
        }
        new = {
            "conversation_content": old["conversation_content"] + [
                {
                    "role": "assistant",
                    "message_id": "reply-new",
                    "content": [{"type": "text", "text": "New answer"}],
                }
            ]
        }
        old_marker, _ = extract_latest_assistant_message(old)
        new_marker, new_text = extract_latest_assistant_message(new)
        self.assertEqual(old_marker, "reply-old")
        self.assertEqual((new_marker, new_text), ("reply-new", "New answer"))

    def test_create_session_hides_gptbots_conversation_id(self):
        with patch("api.case6a_routes.AGENT_K_API_KEY", "test-key"), patch(
            "api.case6a_routes._create_gptbots_conversation",
            new=AsyncMock(return_value="conv-secret"),
        ):
            result = asyncio.run(create_case6a_session())

        self.assertEqual(set(result), {"session_id", "expires_in"})
        self.assertNotIn("conversation_id", result)
        self.assertEqual(case6a_sessions[result["session_id"]].conversation_id, "conv-secret")

    def test_chat_reuses_private_conversation_and_never_returns_it(self):
        case6a_sessions["session-1"] = Case6ASession(conversation_id="conv-secret")
        with patch("api.case6a_routes.AGENT_K_API_KEY", "test-key"), patch(
            "api.case6a_routes._send_agent_k_message",
            new=AsyncMock(return_value="Grounded response"),
        ) as send:
            result = asyncio.run(
                chat_case6a(Case6AChatBody(session_id="session-1", message=" Fees? "))
            )

        send.assert_awaited_once_with("conv-secret", "Fees?")
        self.assertEqual(result, {"session_id": "session-1", "reply": "Grounded response"})
        self.assertNotIn("conv-secret", repr(result))

    def test_expired_busy_and_missing_configuration_are_explicit(self):
        expired = Case6ASession(conversation_id="conv-expired")
        expired.updated_at = datetime.utcnow() - timedelta(hours=3)
        case6a_sessions["expired"] = expired

        with patch("api.case6a_routes.AGENT_K_API_KEY", "test-key"):
            with self.assertRaises(HTTPException) as expired_error:
                asyncio.run(chat_case6a(Case6AChatBody(session_id="expired", message="Hello")))
        self.assertEqual(expired_error.exception.status_code, 410)
        self.assertEqual(expired_error.exception.detail["code"], "SESSION_EXPIRED")

        busy = Case6ASession(conversation_id="conv-busy")
        case6a_sessions["busy"] = busy

        async def exercise_busy():
            await busy.lock.acquire()
            try:
                await chat_case6a(Case6AChatBody(session_id="busy", message="Hello"))
            finally:
                busy.lock.release()

        with patch("api.case6a_routes.AGENT_K_API_KEY", "test-key"):
            with self.assertRaises(HTTPException) as busy_error:
                asyncio.run(exercise_busy())
        self.assertEqual(busy_error.exception.status_code, 409)
        self.assertEqual(busy_error.exception.detail["code"], "SESSION_BUSY")

        with patch("api.case6a_routes.AGENT_K_API_KEY", ""):
            with self.assertRaises(HTTPException) as config_error:
                asyncio.run(create_case6a_session())
        self.assertEqual(config_error.exception.status_code, 503)
        self.assertEqual(config_error.exception.detail["code"], "CASE6A_NOT_CONFIGURED")

    def test_delete_only_removes_application_session(self):
        case6a_sessions["session-1"] = Case6ASession(conversation_id="conv-secret")
        response = asyncio.run(delete_case6a_session("session-1"))
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("session-1", case6a_sessions)

    def test_message_retries_429_and_returns_blocking_answer(self):
        post_calls = 0

        def transport(request: httpx.Request) -> httpx.Response:
            nonlocal post_calls
            if request.method == "GET":
                return httpx.Response(200, json={"conversation_content": []}, request=request)
            post_calls += 1
            if post_calls < 3:
                return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
            return httpx.Response(
                200,
                json={"output": [{"type": "text", "text": "Grounded answer"}]},
                request=request,
            )

        real_client = httpx.AsyncClient
        with patch(
            "api.case6a_routes.httpx.AsyncClient",
            side_effect=lambda **_: real_client(transport=httpx.MockTransport(transport)),
        ), patch("api.case6a_routes.asyncio.sleep", new=AsyncMock()):
            answer = asyncio.run(_send_agent_k_message("conv-private", "Question"))

        self.assertEqual(answer, "Grounded answer")
        self.assertEqual(post_calls, 3)

    def test_server_error_never_recovers_with_previous_answer(self):
        old_detail = {
            "conversation_content": [
                {
                    "role": "assistant",
                    "message_id": "reply-old",
                    "content": [{"type": "text", "text": "Previous answer"}],
                }
            ]
        }

        def transport(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=old_detail, request=request)
            return httpx.Response(500, json={"error": "temporary"}, request=request)

        real_client = httpx.AsyncClient
        with patch(
            "api.case6a_routes.httpx.AsyncClient",
            side_effect=lambda **_: real_client(transport=httpx.MockTransport(transport)),
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(_send_agent_k_message("conv-private", "New question"))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.detail["code"], "AGENT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
