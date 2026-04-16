from pydantic import BaseModel, Field

from model.config import DEFAULT_USER_ID


class CreateConversationBody(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID)


class ChatBody(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID)
    text: str
    response_mode: str = "blocking"
    conversation_id: str | None = Field(
        default=None,
        description="已有对话 ID。提供则复用，不提供则自动创建新对话。",
    )
