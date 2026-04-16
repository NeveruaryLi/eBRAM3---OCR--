import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from model.config import (
    API_KEY,
    CREATE_URL,
    MESSAGE_URL,
    DEFAULT_USER_ID,
    auth_headers,
    pick_conversation_id,
)
from model.utils import extract_gptbots_reply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


class GraphChatBody(BaseModel):
    text: str
    user_id: str = Field(default=DEFAULT_USER_ID)


@router.post("/chat")
async def graph_chat(body: GraphChatBody):
    """
    通过 GPTBots 返回 AI 回复（单轮，每次创建新对话）。
    与 /chat 的区别：不支持多轮上下文，每次请求均开启新 conversation。
    """
    if not API_KEY:
        raise HTTPException(status_code=500, detail="请在 .env 中配置 api_key")

    logger.info("graph_chat: user_id=%s, text_len=%d", body.user_id, len(body.text))

    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        cr = await client.post(
            CREATE_URL,
            headers=auth_headers(),
            json={"user_id": body.user_id},
        )
        if cr.status_code >= 400:
            raise HTTPException(status_code=cr.status_code, detail=cr.text)

        conv_id = pick_conversation_id(cr.json())
        if not conv_id:
            raise HTTPException(
                status_code=502,
                detail=f"无法获取 conversation_id：{cr.json()}",
            )

        payload = {
            "conversation_id": conv_id,
            "response_mode": "blocking",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": body.text}]}
            ],
        }
        mr = await client.post(MESSAGE_URL, headers=auth_headers(), json=payload)
        if mr.status_code >= 400:
            raise HTTPException(status_code=mr.status_code, detail=mr.text)

    reply = extract_gptbots_reply(mr.json())
    return {"response": reply, "conversation_id": conv_id}
