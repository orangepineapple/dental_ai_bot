import uuid

from fastapi import APIRouter, status

from src.auth.dependencies import CurrentUser
from src.chat import service
from src.chat.constants import CHAT_ROUTE_PREFIX
from src.chat.dependencies import LLM, Config, Registry
from src.chat.exceptions import ConversationFull
from src.appointments.exceptions import NotFound as ProposalNotFound
from src.chat.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ConversationSummary,
    ProposalResolution,
    ProposalResponse,
)
from src.database import DbSession
from src.exceptions import ErrorResponse, NotFound

router = APIRouter(prefix=CHAT_ROUTE_PREFIX, tags=["chat"])

_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


@router.post("/messages",
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
    user_id = user.id
    # A turn adds a user message and a reply, so refuse if it could pass the cap.
    count = await service.count_visible_messages(session, conversation_id)
    if count + 2 > config.MAX_CONVERSATION_MESSAGES:
        raise ConversationFull()
    result = await service.handle_user_message(
        session=session,
        llm=llm,
        registry=registry,
        config=config,
        user=user,
        conversation=conversation,
        text=data.message,
    )
    proposal = await service.get_pending_proposal(session, user_id, conversation_id)
    return ChatResponse(
        conversation_id=conversation_id,
        reply=result.reply,
        degraded=result.degraded,
        proposal=proposal,
    )


@router.get("/conversations/{conversation_id}/messages",
    summary="Conversation transcript",
    description="User and assistant messages only; tool traffic is not exposed.",
    responses=_ERRORS,
)
async def get_messages(conversation_id: uuid.UUID, user: CurrentUser, session: DbSession) -> list[ChatMessageResponse]:
    conversation = await service.get_or_create_conversation(session, user, conversation_id)
    if conversation is None:
        raise NotFound()
    rows = await service.list_visible_messages(session, conversation.id)
    return [ChatMessageResponse.model_validate(r, from_attributes=True) for r in rows]


@router.get("/conversations",
    summary="List conversations",
    description="The caller's conversations, most recently active first.",
    responses=_ERRORS,
)
async def list_conversations(user: CurrentUser, session: DbSession) -> list[ConversationSummary]:
    return await service.list_conversations(session, user)


@router.get("/conversations/{conversation_id}/proposal",
    summary="Pending booking confirmation",
    description="The booking card still waiting for the patient, or null.",
    responses=_ERRORS,
)
async def get_pending_proposal(conversation_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ProposalResponse | None:
    conversation = await service.get_or_create_conversation(session, user, conversation_id)
    if conversation is None:
        raise NotFound()
    return await service.get_pending_proposal(session, user.id, conversation.id)


@router.post("/proposals/{proposal_id}/confirm",
    summary="Confirm a proposed booking",
    description=(
        "The patient's explicit approval. This is the only way a chat booking is created; "
        "it is idempotent, so a repeated click returns the first result."
    ),
    responses=_ERRORS,
)
async def confirm_proposal(proposal_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ProposalResolution:
    try:
        return await service.resolve_proposal(session, user, proposal_id, "confirm")
    except ProposalNotFound:
        raise NotFound() from None


@router.post("/proposals/{proposal_id}/decline",
    summary="Decline a proposed booking",
    responses=_ERRORS,
)
async def decline_proposal(proposal_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ProposalResolution:
    try:
        return await service.resolve_proposal(session, user, proposal_id, "decline")
    except ProposalNotFound:
        raise NotFound() from None
