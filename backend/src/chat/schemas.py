import uuid
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from src.chat.config import chat_settings
from src.models import CustomModel


class ChatRequest(CustomModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=chat_settings.MAX_INPUT_CHARS)
    # Omit to start a new conversation.
    conversation_id: uuid.UUID | None = None


class ChatResponse(CustomModel):
    conversation_id: uuid.UUID
    reply: str
    # True when the model provider failed and the canned fallback was returned.
    degraded: bool = False


class ChatMessageResponse(CustomModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
