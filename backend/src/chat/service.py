import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments import proposals
from src.appointments.config import appointment_settings
from src.auth.models import AuthUser
from src.chat.agent import AgentResult, Message, run_agent_loop
from src.chat.config import ChatConfig
from src.chat.constants import FALLBACK_REPLY, TRUNCATED_REPLY
from src.chat.llm import LLMClient
from src.chat.models import ChatConversation, ChatMessage
from src.chat.patient_context import build_patient_context
from src.chat.schemas import ConversationSummary, ProposalResolution, ProposalResponse
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry


async def get_or_create_conversation(session: AsyncSession, user: AuthUser, conversation_id: uuid.UUID | None) -> ChatConversation | None:
    """Return the user's conversation, a new one, or None if the id is not theirs."""
    if conversation_id is None:
        conversation = ChatConversation(user_id=user.id)
        session.add(conversation)
        await session.flush()
        return conversation
    # Filtering on user_id makes "someone else's" indistinguishable from "missing".
    result = await session.execute(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id, ChatConversation.user_id == user.id
        )
    )
    return result.scalar_one_or_none()


def _to_provider_message(row: ChatMessage) -> Message:
    message: Message = {"role": row.role, "content": row.content}
    if row.tool_calls:
        message["tool_calls"] = row.tool_calls
    if row.tool_call_id:
        message["tool_call_id"] = row.tool_call_id
    return message


# Canned replies stay in the visible transcript but are not fed back to the model, where
# it would imitate them or read them as its own prior answer.
_CANNED_REPLIES = (FALLBACK_REPLY, TRUNCATED_REPLY)
# content is NULL on tool-call rows, and NOT IN over NULL would drop them, so match it explicitly.
_NOT_CANNED = or_(ChatMessage.role != "assistant", ChatMessage.content.is_(None), ChatMessage.content.not_in(_CANNED_REPLIES))


async def load_history(session: AsyncSession, conversation_id: uuid.UUID, limit: int) -> list[Message]:
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, _NOT_CANNED)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    rows = list(reversed(result.scalars().all()))
    # A window cut mid tool-exchange would start with an orphaned tool result,
    # which providers reject.
    while rows and rows[0].role == "tool":
        rows.pop(0)
    return [_to_provider_message(row) for row in rows]


async def list_visible_messages(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[ChatMessage]:
    """User-visible transcript: user and assistant text only (no tool traffic)."""
    result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role.in_(("user", "assistant")),
            ChatMessage.content.is_not(None),
        )
        .order_by(ChatMessage.id)
    )
    return list(result.scalars().all())


_VISIBLE = (ChatMessage.role.in_(("user", "assistant")), ChatMessage.content.is_not(None))
_TITLE_CHARS = 60


async def count_visible_messages(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, *_VISIBLE)
    )
    return result.scalar_one()


async def list_conversations(
    session: AsyncSession, user: AuthUser, limit: int = 50) -> list[ConversationSummary]:
    """The user's conversations, most recently active first. Empty ones are skipped."""
    first_user_message = (
        select(ChatMessage.content)
        .where(
            ChatMessage.conversation_id == ChatConversation.id,
            ChatMessage.role == "user",
        )
        .order_by(ChatMessage.id)
        .limit(1)
        .correlate(ChatConversation)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            ChatConversation.id,
            ChatConversation.updated_at,
            first_user_message.label("title"),
            func.count(ChatMessage.id).label("message_count"),
        )
        .join(ChatMessage, ChatMessage.conversation_id == ChatConversation.id)
        .where(ChatConversation.user_id == user.id, *_VISIBLE)
        .group_by(ChatConversation.id)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
    )
    summaries = []
    for row in result.all():
        title = (row.title or "").strip()
        if len(title) > _TITLE_CHARS:
            title = title[: _TITLE_CHARS - 1].rstrip() + "…"
        summaries.append(
            ConversationSummary(
                id=row.id,
                title=title or "New conversation",
                message_count=row.message_count,
                updated_at=row.updated_at,
            )
        )
    return summaries


def _persist(
    session: AsyncSession, conversation_id: uuid.UUID, messages: list[dict[str, Any]]
) -> None:
    for message in messages:
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                role=message["role"],
                content=message.get("content"),
                tool_calls=message.get("tool_calls"),
                tool_call_id=message.get("tool_call_id"),
            )
        )


async def handle_user_message(
    *,
    session: AsyncSession,
    llm: LLMClient,
    registry: ToolRegistry,
    config: ChatConfig,
    user: AuthUser,
    conversation: ChatConversation,
    text: str,
) -> AgentResult:
    # Plain values: rollback() below expires ORM instances, which cannot lazy-load in async.
    conversation_id = conversation.id
    user_id, patient_id = user.id, user.patient_id

    # Commit the user's message first so it is on record even if the turn fails.
    _persist(session, conversation_id, [{"role": "user", "content": text}])
    await session.commit()

    history = await load_history(session, conversation_id, config.HISTORY_MESSAGE_LIMIT)
    try:
        patient_context = await build_patient_context(session, patient_id, config)
    except Exception:
        # Context is a nicety; a failure must never block the chat turn.
        await session.rollback()
        patient_context = ""

    def ctx_factory(tool_call_id: str) -> ToolContext:
        return ToolContext(
            session=session,
            user_id=user_id,
            patient_id=patient_id,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
        )

    result = await run_agent_loop(
        llm=llm, registry=registry, history=history, ctx_factory=ctx_factory, config=config, patient_context=patient_context
    )

    # Tools own their own transactions; make sure none left the session mid-transaction
    # before recording the turn.
    await session.rollback()
    _persist(session, conversation_id, result.new_messages)
    await session.execute(
        update(ChatConversation)
        .where(ChatConversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    await session.commit()
    return result


def proposal_response(view: proposals.ProposalView) -> ProposalResponse:
    tz = ZoneInfo(appointment_settings.CLINIC_TIMEZONE)
    return ProposalResponse(
        id=view.id,
        appointment_type=view.appointment_type_name,
        patient_first_name=view.patient_first_name,
        provider_name=view.provider_name,
        starts_at=view.starts_at.astimezone(tz).isoformat(),
        display_time=proposals.format_local(view.starts_at),
        duration_minutes=int((view.ends_at - view.starts_at).total_seconds() // 60),
        expires_at=view.expires_at,
        reason=view.reason,
    )


async def get_pending_proposal(session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> ProposalResponse | None:
    view = await proposals.get_pending_proposal(
        session, user_id=user_id, conversation_id=conversation_id
    )
    return proposal_response(view) if view else None


async def resolve_proposal(session: AsyncSession, user: AuthUser, proposal_id: uuid.UUID, action: proposals.Action) -> ProposalResolution:
    """Apply the patient's Confirm/Decline click and record the outcome in the transcript."""
    user_id, patient_id = user.id, user.patient_id  # plain values; see handle_user_message
    outcome = await proposals.resolve_proposal(
        session,
        user_id=user_id,
        actor_patient_id=patient_id,
        proposal_id=proposal_id,
        action=action,
    )
    if outcome.changed:
        # The server's own words are the official record; the model never narrates a booking.
        conversation_id = outcome.proposal.conversation_id
        _persist(session, conversation_id, [{"role": "assistant", "content": outcome.message}])
        await session.execute(
            update(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        await session.commit()
    return ProposalResolution(
        status=outcome.status, message=outcome.message, proposal=proposal_response(outcome.proposal)
    )
