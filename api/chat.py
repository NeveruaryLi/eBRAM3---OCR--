import logging

import httpx
from fastapi import APIRouter, HTTPException

from model.config import API_KEY, CREATE_URL, MESSAGE_URL, agent_b_auth_headers, auth_headers, pick_conversation_id
from model.schemas import ChatBody, CreateConversationBody

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health():
    """健康检查，无需鉴权。"""
    return {"ok": True}


@router.post("/conversation")
async def create_conversation(body: CreateConversationBody):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="请在 .env 中配置 api_key")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            CREATE_URL,
            headers=auth_headers(),
            json={"user_id": body.user_id},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.post("/chat")
async def chat(body: ChatBody):
    """
    发送消息并返回 AI 回复。

    会话管理逻辑：
    - body.conversation_id 为 None  → 自动调用 GPTBots 创建新对话，返回新 conversation_id
    - body.conversation_id 有值     → 直接复用，跳过创建步骤（实现多轮上下文记忆）

    前端负责存储 conversation_id，每次请求带上它，实现同窗口内的连续对话。
    点击"新对话"时前端清空 conversation_id，下次请求即开启全新对话。
    """
    if not API_KEY:
        raise HTTPException(status_code=500, detail="请在 .env 中配置 api_key")

    logger.info(
        "chat: user_id=%s, text_len=%d, reuse_conv=%s",
        body.user_id, len(body.text), bool(body.conversation_id),
    )
    b_headers = agent_b_auth_headers()

    async with httpx.AsyncClient(timeout=120.0) as client:

        # ── 决定 conversation_id ────────────────────────────────────────────
        if body.conversation_id:
            # 复用已有 Agent B 对话，跳过创建步骤
            conversation_id = body.conversation_id
            logger.info("复用 Agent B conversation_id: %s", conversation_id)
        else:
            # 第一条消息：用 Agent B 创建新对话
            cr = await client.post(
                CREATE_URL,
                headers=b_headers,
                json={"user_id": body.user_id},
            )
            if cr.status_code >= 400:
                raise HTTPException(status_code=cr.status_code, detail=cr.text)
            conversation_id = pick_conversation_id(cr.json())
            if not conversation_id:
                raise HTTPException(
                    status_code=502,
                    detail=f"创建对话返回中未找到 conversation_id: {cr.json()}",
                )
            logger.info("创建新 Agent B conversation_id: %s", conversation_id)

        # ── 发送消息 ────────────────────────────────────────────────────────
        payload = {
            "conversation_id": conversation_id,
            "response_mode": body.response_mode,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": body.text}],
                }
            ],
        }
        mr = await client.post(MESSAGE_URL, headers=b_headers, json=payload)
        if mr.status_code >= 400:
            raise HTTPException(status_code=mr.status_code, detail=mr.text)
        message_data = mr.json()

    return {
        "conversation_id": conversation_id,   # 前端必须存储并在下一次请求中带上
        "message_response": message_data,
    }
