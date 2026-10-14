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


class ProposalResponse(CustomModel):
    """A booking awaiting the patient's Confirm / Decline click (rendered as a card)."""

    id: uuid.UUID
    kind: Literal["booking"] = "booking"
    appointment_type: str
    patient_first_name: str
    provider_name: str
    # ISO 8601 with the clinic's UTC offset, plus a ready-to-display version of it.
    starts_at: str
    display_time: str
    duration_minutes: int
    expires_at: datetime
    reason: str | None = None


class CancellationProposalResponse(CustomModel):
    """A cancellation awaiting the patient's Confirm / Keep click (rendered as a card)."""

    id: uuid.UUID
    kind: Literal["cancellation"] = "cancellation"
    appointment_type: str
    patient_first_name: str
    provider_name: str
    starts_at: str
    display_time: str
    expires_at: datetime
    reason: str | None = None
    late_cancellation: bool = False
    policy_note: str | None = None


PendingProposalResponse = ProposalResponse | CancellationProposalResponse


class ProposalResolution(CustomModel):
    status: Literal["confirmed", "declined", "failed", "expired", "superseded"]
    # Server-written text. This is also stored in the conversation as the assistant's reply.
    message: str
    proposal: PendingProposalResponse


class ChatResponse(CustomModel):
    conversation_id: uuid.UUID
    reply: str
    # True when the model provider failed and the canned fallback was returned.
    degraded: bool = False
    # Present while a booking or cancellation card is waiting for the patient.
    proposal: PendingProposalResponse | None = None


class ChatMessageResponse(CustomModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationSummary(CustomModel):
    id: uuid.UUID
    # First user message, truncated.
    title: str
    message_count: int
    updated_at: datetime
