"""Cancellation proposals: the human-in-the-loop step for cancelling an appointment.

The model can only *propose*. The appointment is cancelled when the authenticated patient
presses Confirm on the card, which hits an HTTP endpoint the model cannot call.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments import service
from src.appointments.config import AppointmentConfig, appointment_settings
from src.appointments.exceptions import AppointmentError, NotFound, PolicyViolation, RequiresStaff
from src.appointments.models import (
    BOOKED,
    PROPOSAL_PENDING,
    Appointment,
    AppointmentType,
    BookingProposal,
    CancellationProposal,
    Provider,
)
from src.appointments.proposals import Action, format_local
from src.patients.models import Patient


@dataclass(frozen=True, slots=True)
class CancellationView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    appointment_id: uuid.UUID
    appointment_type_name: str
    starts_at: datetime
    ends_at: datetime
    provider_name: str
    patient_first_name: str
    expires_at: datetime
    reason: str | None
    late_cancellation: bool
    policy_note: str | None


@dataclass(frozen=True, slots=True)
class CancellationOutcome:
    status: str  # confirmed | declined | failed | expired | superseded
    message: str  # server-written, shown to the patient and stored in the conversation
    proposal: CancellationView
    changed: bool  # False when this was an idempotent repeat


def _view_query():
    return (
        select(
            CancellationProposal,
            Appointment.starts_at.label("starts_at"),
            Appointment.ends_at.label("ends_at"),
            AppointmentType.name.label("type_name"),
            Provider.name.label("provider_name"),
            Patient.first_name.label("patient_first_name"),
        )
        .join(Appointment, Appointment.id == CancellationProposal.appointment_id)
        .join(AppointmentType, AppointmentType.code == Appointment.appointment_type_code)
        .join(Provider, Provider.id == Appointment.provider_id)
        .join(Patient, Patient.id == Appointment.patient_id)
    )


def _is_late(starts_at: datetime, now: datetime, config: AppointmentConfig) -> bool:
    return starts_at.astimezone(UTC) - now < timedelta(hours=config.LATE_CHANGE_WINDOW_HOURS)


def _to_view(row, now: datetime, config: AppointmentConfig) -> CancellationView:
    p: CancellationProposal = row.CancellationProposal
    late = _is_late(row.starts_at, now, config)
    return CancellationView(
        id=p.id,
        conversation_id=p.conversation_id,
        status=p.status,
        appointment_id=p.appointment_id,
        appointment_type_name=row.type_name,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        provider_name=row.provider_name,
        patient_first_name=row.patient_first_name,
        expires_at=p.expires_at,
        reason=p.reason,
        late_cancellation=late,
        policy_note=config.LATE_CANCEL_POLICY_NOTE if late else None,
    )


async def _load_view(session: AsyncSession, proposal_id: uuid.UUID, now: datetime, config: AppointmentConfig) -> CancellationView:
    row = (await session.execute(_view_query().where(CancellationProposal.id == proposal_id))).one()
    return _to_view(row, now, config)


async def create_cancellation_proposal(session: AsyncSession, *, actor_user_id: uuid.UUID, actor_patient_id: uuid.UUID, conversation_id: uuid.UUID, appointment_id: uuid.UUID, reason: str | None = None, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> CancellationView:
    """Check the appointment can be cancelled now and store a pending proposal. Cancels nothing."""
    now = now or datetime.now(UTC)
    # Raises NotFound for a missing or not-manageable appointment.
    appt = await service.get_appointment_details(
        session, actor_patient_id=actor_patient_id, appointment_id=appointment_id
    )
    if appt.status != BOOKED:
        raise PolicyViolation("Only an upcoming booked appointment can be cancelled.")
    if appt.starts_at.astimezone(UTC) <= now:
        raise PolicyViolation("That appointment has already started or passed.")
    if _is_late(appt.starts_at, now, config) and config.LATE_CANCEL_ACTION == "requires_staff":
        raise RequiresStaff(
            config.LATE_CANCEL_POLICY_NOTE
            or "This appointment is too close to cancel here. The patient should contact the clinic staff."
        )

    # Only one card is live per conversation: a newer suggestion replaces any older one.
    for model in (CancellationProposal, BookingProposal):
        await session.execute(
            update(model)
            .where(model.conversation_id == conversation_id, model.status == PROPOSAL_PENDING)
            .values(status="superseded", resolved_at=now)
        )
    proposal_id = uuid.uuid4()
    session.add(
        CancellationProposal(
            id=proposal_id,
            conversation_id=conversation_id,
            user_id=actor_user_id,
            appointment_id=appointment_id,
            reason=reason,
            expires_at=now + timedelta(minutes=config.PROPOSAL_TTL_MINUTES),
        )
    )
    await session.commit()
    return await _load_view(session, proposal_id, now, config)


async def get_pending_cancellation(session: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> CancellationView | None:
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            _view_query().where(
                CancellationProposal.conversation_id == conversation_id,
                CancellationProposal.user_id == user_id,
                CancellationProposal.status == PROPOSAL_PENDING,
                CancellationProposal.expires_at > now,
            )
        )
    ).one_or_none()
    return _to_view(row, now, config) if row else None


async def _finish(session: AsyncSession, proposal_id: uuid.UUID, status: str, message: str, now: datetime) -> None:
    await session.execute(
        update(CancellationProposal)
        .where(CancellationProposal.id == proposal_id)
        .values(status=status, result_message=message, resolved_at=now)
    )
    await session.commit()


_REPEAT_MESSAGES = {
    "declined": "This request was already declined. Nothing was cancelled.",
    "expired": "This confirmation expired. Ask the assistant to start the cancellation again.",
    "superseded": "A newer suggestion replaced this one.",
    "failed": "This cancellation could not be completed. Please contact the clinic.",
}


async def resolve_cancellation(session: AsyncSession, *, user_id: uuid.UUID, actor_patient_id: uuid.UUID, proposal_id: uuid.UUID, action: Action, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> CancellationOutcome:
    """Apply the patient's click. Safe to repeat: a second click returns the first result."""
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(CancellationProposal)
            .where(CancellationProposal.id == proposal_id, CancellationProposal.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("No such confirmation was found.")

    # Plain values: later commits/rollbacks expire ORM instances, which cannot lazy-load.
    status = row.status
    appointment_id = row.appointment_id
    reason = row.reason
    expires_at = row.expires_at
    stored_message = row.result_message

    if status != PROPOSAL_PENDING:
        view = await _load_view(session, proposal_id, now, config)
        message = stored_message if status == "confirmed" else _REPEAT_MESSAGES.get(status, "")
        await session.rollback()  # release the row lock; nothing changed
        return CancellationOutcome(status, message or "", view, changed=False)

    if now >= expires_at:
        message = _REPEAT_MESSAGES["expired"]
        await _finish(session, proposal_id, "expired", message, now)
        return CancellationOutcome("expired", message, await _load_view(session, proposal_id, now, config), True)

    if action == "decline":
        message = "Okay, I have not cancelled anything. Is there anything else I can help with?"
        await _finish(session, proposal_id, "declined", message, now)
        return CancellationOutcome("declined", message, await _load_view(session, proposal_id, now, config), True)

    try:
        outcome = await service.cancel_appointment(
            session,
            actor_user_id=user_id,
            actor_patient_id=actor_patient_id,
            appointment_id=appointment_id,
            confirmed=True,
            reason=reason,
            now=now,
            config=config,
        )
    except AppointmentError as exc:
        await session.rollback()
        message = f"I could not complete that cancellation: {exc}"
        await _finish(session, proposal_id, "failed", message, now)
        return CancellationOutcome("failed", message, await _load_view(session, proposal_id, now, config), True)

    cancelled = outcome.appointment
    message = (
        f"Done. {cancelled.patient_first_name}'s {cancelled.appointment_type_name} appointment on "
        f"{format_local(cancelled.starts_at, config)} with {cancelled.provider_name} has been cancelled."
    )
    if outcome.policy_note:
        message = f"{message} {outcome.policy_note}"
    await _finish(session, proposal_id, "confirmed", message, now)
    return CancellationOutcome("confirmed", message, await _load_view(session, proposal_id, now, config), True)
