import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import AuthUser
from src.chat.agent import AgentResult, Message, run_agent_loop
from src.chat.config import ChatConfig
from src.chat.llm import LLMClient
from src.chat.models import ChatConversation, ChatMessage
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry


async def get_or_create_conversation(
    session: AsyncSession, user: AuthUser, conversation_id: uuid.UUID | None
) -> ChatConversation | None:
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


async def load_history(
    session: AsyncSession, conversation_id: uuid.UUID, limit: int
) -> list[Message]:
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
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

    def ctx_factory(tool_call_id: str) -> ToolContext:
        return ToolContext(
            session=session,
            user_id=user_id,
            patient_id=patient_id,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
        )

    result = await run_agent_loop(
        llm=llm, registry=registry, history=history, ctx_factory=ctx_factory, config=config
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
