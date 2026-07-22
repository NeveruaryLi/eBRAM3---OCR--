"""Case 6A eBRAM 官网知识库服务指导路由。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from model.config import (
    AGENT_K_API_KEY,
    CREATE_URL,
    MESSAGES_URL,
    MESSAGE_URL,
    agent_k_auth_headers,
    pick_conversation_id,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/case6a", tags=["case6a"])

SESSION_TTL_SECONDS = 7200
CLEANUP_INTERVAL_SECONDS = 1800
MAX_MESSAGE_CHARS = 4000
GPTBOTS_TIMEOUT_SECONDS = 180.0


class Case6AChatBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


@dataclass
class Case6ASession:
    conversation_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


case6a_sessions: dict[str, Case6ASession] = {}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _ensure_configured() -> None:
    if not AGENT_K_API_KEY:
        raise _error(503, "CASE6A_NOT_CONFIGURED", "Case 6A 服务暂未配置，请联系管理员。")


def build_agent_k_payload(conversation_id: str, message: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": message}],
            }
        ],
        "conversation_config": {
            "short_term_memory": True,
            "long_term_memory": False,
        },
    }


def _collect_text_blocks(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            text = value["text"].strip()
            if text:
                output.append(text)
        for child in value.values():
            _collect_text_blocks(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_text_blocks(child, output)


def extract_assistant_text(payload: dict[str, Any]) -> str:
    """从 blocking 响应中兼容提取 Assistant 文本。"""
    direct_keys = ("answer", "response", "output_text", "text", "message")
    for container in (payload, payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in direct_keys:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    texts: list[str] = []
    _collect_text_blocks(payload.get("output") or payload.get("data") or {}, texts)
    return "\n".join(dict.fromkeys(texts))


def extract_latest_assistant_text(payload: dict[str, Any]) -> str:
    """从会话详情中取得最后一条含文本的 Assistant 回复。"""
    messages = payload.get("conversation_content", [])
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        texts: list[str] = []
        _collect_text_blocks(message.get("content", []), texts)
        if texts:
            return "\n".join(dict.fromkeys(texts))
    return ""


async def _conversation_detail(client: httpx.AsyncClient, conversation_id: str) -> str:
    try:
        response = await client.get(
            MESSAGES_URL,
            headers=agent_k_auth_headers(),
            params={"conversation_id": conversation_id, "page": 1, "page_size": 100},
        )
        if response.status_code >= 400:
            return ""
        return extract_latest_assistant_text(response.json())
    except (httpx.HTTPError, ValueError):
        return ""


async def _create_gptbots_conversation(user_id: str) -> str:
    delay = 1.0
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    CREATE_URL,
                    headers=agent_k_auth_headers(),
                    json={"user_id": user_id},
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt == 2:
                    raise _error(503, "AGENT_UNAVAILABLE", "暂时无法连接服务，请稍后重试。") from exc
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    code = "AGENT_BUSY" if response.status_code == 429 else "AGENT_UNAVAILABLE"
                    raise _error(503, code, "服务当前繁忙，请稍后重试。")
                retry_after = response.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay *= 2
                continue
            if response.status_code in (401, 403):
                raise _error(503, "AGENT_AUTH_FAILED", "Case 6A 服务配置无效，请联系管理员。")
            if response.status_code >= 400:
                raise _error(502, "AGENT_REQUEST_REJECTED", "无法建立服务会话，请稍后重试。")

            conversation_id = pick_conversation_id(response.json())
            if not conversation_id:
                raise _error(502, "AGENT_INVALID_RESPONSE", "服务返回了无效的会话信息。")
            return conversation_id

    raise _error(503, "AGENT_UNAVAILABLE", "暂时无法连接服务，请稍后重试。")


async def _send_agent_k_message(conversation_id: str, message: str) -> str:
    payload = build_agent_k_payload(conversation_id, message)
    delay = 1.0
    async with httpx.AsyncClient(timeout=GPTBOTS_TIMEOUT_SECONDS) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    MESSAGE_URL,
                    headers=agent_k_auth_headers(),
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                recovered = await _conversation_detail(client, conversation_id)
                if recovered:
                    return recovered
                code = "AGENT_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "AGENT_UNAVAILABLE"
                status = 504 if code == "AGENT_TIMEOUT" else 503
                raise _error(status, code, "服务暂未返回结果，请稍后重试。") from exc

            if response.status_code == 429:
                if attempt == 2:
                    raise _error(503, "AGENT_BUSY", "服务当前繁忙，请稍后重试。")
                retry_after = response.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay *= 2
                continue

            if response.status_code >= 500:
                recovered = await _conversation_detail(client, conversation_id)
                if recovered:
                    return recovered
                raise _error(503, "AGENT_UNAVAILABLE", "服务当前不可用，请稍后重试。")
            if response.status_code in (401, 403):
                raise _error(503, "AGENT_AUTH_FAILED", "Case 6A 服务配置无效，请联系管理员。")
            if response.status_code >= 400:
                raise _error(502, "AGENT_REQUEST_REJECTED", "问题未能发送，请检查内容后重试。")

            try:
                answer = extract_assistant_text(response.json())
            except ValueError:
                answer = ""
            if not answer:
                answer = await _conversation_detail(client, conversation_id)
            if not answer:
                raise _error(502, "AGENT_EMPTY_RESPONSE", "服务未返回有效答案，请稍后重试。")
            return answer

    raise _error(503, "AGENT_UNAVAILABLE", "服务当前不可用，请稍后重试。")


def _is_expired(session: Case6ASession) -> bool:
    return session.updated_at < datetime.utcnow() - timedelta(seconds=SESSION_TTL_SECONDS)


@router.post("/session")
async def create_case6a_session() -> dict[str, Any]:
    _ensure_configured()
    session_id = str(uuid.uuid4())
    user_id = f"c6a_{session_id.replace('-', '')[:24]}"
    conversation_id = await _create_gptbots_conversation(user_id)
    case6a_sessions[session_id] = Case6ASession(conversation_id=conversation_id)
    logger.info("Case 6A 会话已创建：session=%s", session_id[:8])
    return {"session_id": session_id, "expires_in": SESSION_TTL_SECONDS}


@router.post("/session/chat")
async def chat_case6a(body: Case6AChatBody) -> dict[str, str]:
    _ensure_configured()
    message = body.message.strip()
    if not message:
        raise _error(422, "EMPTY_MESSAGE", "请输入问题。")

    session = case6a_sessions.get(body.session_id)
    if not session:
        raise _error(410, "SESSION_EXPIRED", "该对话已过期，请开启新对话。")
    if _is_expired(session):
        case6a_sessions.pop(body.session_id, None)
        raise _error(410, "SESSION_EXPIRED", "该对话已过期，请开启新对话。")
    if session.lock.locked():
        raise _error(409, "SESSION_BUSY", "上一条问题仍在处理中，请稍候。")

    async with session.lock:
        reply = await _send_agent_k_message(session.conversation_id, message)
        session.updated_at = datetime.utcnow()
    return {"session_id": body.session_id, "reply": reply}


@router.delete("/session/{session_id}", status_code=204)
async def delete_case6a_session(session_id: str) -> Response:
    case6a_sessions.pop(session_id, None)
    return Response(status_code=204)


async def start_case6a_cleanup_task() -> None:
    """定期清理两小时未活动的 Case 6A 应用侧会话。"""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        expired = [key for key, session in list(case6a_sessions.items()) if _is_expired(session)]
        for session_id in expired:
            case6a_sessions.pop(session_id, None)
        if expired:
            logger.info("Case 6A 会话清理完成：删除 %d 个", len(expired))
