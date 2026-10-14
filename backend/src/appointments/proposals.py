"""Booking proposals: the human-in-the-loop confirmation step.

The model can only *propose*. The appointment is created when the authenticated patient
presses Confirm in the chat UI, which hits an HTTP endpoint the model cannot call.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments import service
from src.appointments.config import AppointmentConfig, appointment_settings
from src.appointments.exceptions import AppointmentError, NotAuthorized, NotFound
from src.appointments.models import (
    PROPOSAL_PENDING,
    AppointmentType,
    BookingProposal,
    CancellationProposal,
    Provider,
)
from src.appointments.slot_token import verify_slot_token
from src.family.service import manageable_patient_ids
from src.patients.models import Patient

Action = Literal["confirm", "decline"]


@dataclass(frozen=True, slots=True)
class ProposalView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    appointment_type_name: str
    starts_at: datetime
    ends_at: datetime
    provider_name: str
    patient_first_name: str
    expires_at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    status: str  # confirmed | declined | failed | expired | superseded
    message: str  # server-written, shown to the patient and stored in the conversation
    proposal: ProposalView
    changed: bool  # False when this was an idempotent repeat


def format_local(value: datetime, config: AppointmentConfig = appointment_settings) -> str:
    local = value.astimezone(ZoneInfo(config.CLINIC_TIMEZONE))
    hour = local.hour % 12 or 12
    return f"{local:%A, %B} {local.day}, {local.year} at {hour}:{local:%M} {local:%p} {local:%Z}"


def _view_query():
    return (
        select(
            BookingProposal,
            AppointmentType.name.label("type_name"),
            Provider.name.label("provider_name"),
            Patient.first_name.label("patient_first_name"),
        )
        .join(AppointmentType, AppointmentType.code == BookingProposal.appointment_type_code)
        .join(Provider, Provider.id == BookingProposal.provider_id)
        .join(Patient, Patient.id == BookingProposal.target_patient_id)
    )


def _to_view(row) -> ProposalView:
    p: BookingProposal = row.BookingProposal
    return ProposalView(
        id=p.id,
        conversation_id=p.conversation_id,
        status=p.status,
        appointment_type_name=row.type_name,
        starts_at=p.starts_at,
        ends_at=p.ends_at,
        provider_name=row.provider_name,
        patient_first_name=row.patient_first_name,
        expires_at=p.expires_at,
        reason=p.reason,
    )


async def _load_view(session: AsyncSession, proposal_id: uuid.UUID) -> ProposalView:
    return _to_view((await session.execute(_view_query().where(BookingProposal.id == proposal_id))).one())


async def create_booking_proposal(session: AsyncSession, *, actor_user_id: uuid.UUID, actor_patient_id: uuid.UUID, target_patient_id: uuid.UUID, conversation_id: uuid.UUID, slot_token: str, reason: str | None = None, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> ProposalView:
    """Validate the slot now and store a pending proposal. Books nothing, reserves nothing."""
    now = now or datetime.now(UTC)
    if target_patient_id not in await manageable_patient_ids(session, actor_patient_id):
        raise NotAuthorized("You are not authorized to book for that patient.")
    claims = verify_slot_token(config.slot_secret, slot_token, now)
    plan = await service.plan_booking(
        session, target_patient_id=target_patient_id, claims=claims, now=now, config=config, lock=False
    )

    # A newer suggestion replaces the older one; the partial unique index enforces one.
    for model in (BookingProposal, CancellationProposal):
        await session.execute(
            update(model)
            .where(model.conversation_id == conversation_id, model.status == PROPOSAL_PENDING)
            .values(status="superseded", resolved_at=now)
        )
    proposal_id = uuid.uuid4()
    session.add(
        BookingProposal(
            id=proposal_id,
            conversation_id=conversation_id,
            user_id=actor_user_id,
            target_patient_id=target_patient_id,
            slot_token=slot_token,
            provider_id=plan.provider.id,
            appointment_type_code=plan.appt_type.code,
            starts_at=plan.start,
            ends_at=plan.end,
            reason=reason,
            expires_at=now + timedelta(minutes=config.PROPOSAL_TTL_MINUTES),
        )
    )
    await session.commit()
    return await _load_view(session, proposal_id)


async def get_pending_proposal(session: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime | None = None) -> ProposalView | None:
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            _view_query().where(
                BookingProposal.conversation_id == conversation_id,
                BookingProposal.user_id == user_id,
                BookingProposal.status == PROPOSAL_PENDING,
                BookingProposal.expires_at > now,
            )
        )
    ).one_or_none()
    return _to_view(row) if row else None


async def _finish(session: AsyncSession, proposal_id: uuid.UUID, status: str, message: str, now: datetime, appointment_id: uuid.UUID | None = None) -> None:
    await session.execute(
        update(BookingProposal)
        .where(BookingProposal.id == proposal_id)
        .values(status=status, result_message=message, resolved_at=now, appointment_id=appointment_id)
    )
    await session.commit()


_REPEAT_MESSAGES = {
    "declined": "This request was already declined. Nothing was booked.",
    "expired": "This confirmation expired. Ask the assistant to find a time again.",
    "superseded": "A newer suggestion replaced this one.",
    "failed": "This booking could not be completed. Ask the assistant to find a time again.",
}


async def resolve_proposal(session: AsyncSession, *, user_id: uuid.UUID, actor_patient_id: uuid.UUID, proposal_id: uuid.UUID, action: Action, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> ProposalOutcome:
    """Apply the patient's click. Safe to repeat: a second click returns the first result."""
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(BookingProposal)
            .where(BookingProposal.id == proposal_id, BookingProposal.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("No such confirmation was found.")

    # Plain values: later commits/rollbacks expire ORM instances, which cannot lazy-load.
    status = row.status
    conversation_id = row.conversation_id
    target_patient_id = row.target_patient_id
    slot_token = row.slot_token
    reason = row.reason
    expires_at = row.expires_at
    stored_message = row.result_message

    if status != PROPOSAL_PENDING:
        view = await _load_view(session, proposal_id)
        message = stored_message if status == "confirmed" else _REPEAT_MESSAGES.get(status, "")
        await session.rollback()  # release the row lock; nothing changed
        return ProposalOutcome(status, message or "", view, changed=False)

    if now >= expires_at:
        message = _REPEAT_MESSAGES["expired"]
        await _finish(session, proposal_id, "expired", message, now)
        return ProposalOutcome("expired", message, await _load_view(session, proposal_id), True)

    if action == "decline":
        message = "Okay, I have not booked anything. Would you like to look at a different time?"
        await _finish(session, proposal_id, "declined", message, now)
        return ProposalOutcome("declined", message, await _load_view(session, proposal_id), True)

    try:
        booked = await service.book_appointment(
            session,
            actor_user_id=user_id,
            actor_patient_id=actor_patient_id,
            target_patient_id=target_patient_id,
            conversation_id=conversation_id,
            slot_token=slot_token,
            confirmed=True,
            reason=reason,
            now=now,
            config=config,
        )
    except AppointmentError as exc:
        await session.rollback()
        message = f"I could not complete that booking: {exc}"
        await _finish(session, proposal_id, "failed", message, now)
        return ProposalOutcome("failed", message, await _load_view(session, proposal_id), True)

    message = (
        f"Done! {booked.patient_first_name}'s {booked.appointment_type_name} appointment is "
        f"booked for {format_local(booked.starts_at, config)} with {booked.provider_name}."
    )
    await _finish(session, proposal_id, "confirmed", message, now, booked.id)
    return ProposalOutcome("confirmed", message, await _load_view(session, proposal_id), True)
