import uuid

from fastapi import APIRouter, status

from src.auth.dependencies import CurrentUser
from src.chat import service
from src.chat.constants import CHAT_ROUTE_PREFIX
from src.chat.dependencies import LLM, Config, Registry
from src.chat.schemas import ChatMessageResponse, ChatRequest, ChatResponse
from src.database import DbSession
from src.exceptions import ErrorResponse, NotFound

router = APIRouter(prefix=CHAT_ROUTE_PREFIX, tags=["chat"])

_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}


@router.post(
    "/messages",
    summary="Send a chat message",
    description=(
        "Runs one assistant turn (including any tool calls) and returns the reply. "
        "Omit `conversation_id` to start a new conversation. If the model provider is "
        "down the response is a safe canned message with `degraded: true`."
    ),
    responses=_ERRORS,
)
async def send_message( data: ChatRequest, user: CurrentUser, session: DbSession, llm: LLM, registry: Registry, config: Config) -> ChatResponse:
    conversation = await service.get_or_create_conversation(session, user, data.conversation_id)
    if conversation is None:
        raise NotFound()
    conversation_id = conversation.id  # read before the service expires the instance
    result = await service.handle_user_message(
        session=session,
        llm=llm,
        registry=registry,
        config=config,
        user=user,
        conversation=conversation,
        text=data.message,
    )
    return ChatResponse(
        conversation_id=conversation_id, reply=result.reply, degraded=result.degraded
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    summary="Conversation transcript",
    description="User and assistant messages only; tool traffic is not exposed.",
    responses=_ERRORS,
)
async def get_messages(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> list[ChatMessageResponse]:
    conversation = await service.get_or_create_conversation(session, user, conversation_id)
    if conversation is None:
        raise NotFound()
    rows = await service.list_visible_messages(session, conversation.id)
    return [ChatMessageResponse.model_validate(r, from_attributes=True) for r in rows]
