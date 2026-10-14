"""Model-facing scheduling tools.

Handlers are thin: map validated args + server-side identity to a service call, then
map the outcome to a minimal dict. All rules live in src/appointments/service.py.

Rules that apply to every tool in this file (from CLAUDE.md):
- Identity comes from `ctx` (set by the server from the JWT), never from `args`.
- Return only fields the model needs to talk to the patient.
- Never claim success unless the DB transaction committed (the service guarantees it).
"""

import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from src.appointments import cancel_proposals, proposals, service
from src.appointments.config import appointment_settings
from src.appointments.exceptions import AppointmentError
from src.tools.base import ToolArgs, ToolContext, ToolDefinition

MAX_SLOT_SEARCH_DAYS = 31
MAX_LISTED_APPOINTMENTS = 20

def _local_iso(value: datetime) -> str:
    return value.astimezone(ZoneInfo(appointment_settings.CLINIC_TIMEZONE)).isoformat()


def _appointment_dict(view: service.AppointmentView) -> dict[str, Any]:
    return {
        "appointment_id": str(view.id),
        "status": view.status,
        "appointment_type": view.appointment_type_name,
        "starts_at": _local_iso(view.starts_at),
        "provider_name": view.provider_name,
        "patient_first_name": view.patient_first_name,
    }


def _error_dict(exc: AppointmentError) -> dict[str, Any]:
    out: dict[str, Any] = {"status": exc.status, "message": str(exc)}
    if exc.code:
        out["error"] = exc.code
    return out


async def _guarded(ctx: ToolContext, call) -> dict[str, Any]:
    """Run a service call; turn business outcomes into tool results and release any locks."""
    try:
        return await call()
    except AppointmentError as exc:
        await ctx.session.rollback()
        return _error_dict(exc)
    except Exception:
        # Leave the session usable for the next tool call; the registry reports the failure.
        await ctx.session.rollback()
        raise


# --------------------------------------------------------------------------- #
# get_available_slots
# --------------------------------------------------------------------------- #
class GetAvailableSlotsArgs(ToolArgs):
    appointment_type: str = Field(
        min_length=1,
        max_length=64,
        description="Appointment type code, e.g. 'cleaning', 'exam', 'filling'.",
    )
    start_date: date = Field(description="First day to search, YYYY-MM-DD, clinic local time.")
    end_date: date = Field(description="Last day to search, YYYY-MM-DD, inclusive.")
    provider_id: uuid.UUID | None = Field(
        default=None, description="Only if the patient asked for a specific provider."
    )

    @model_validator(mode="after")
    def _check_range(self) -> "GetAvailableSlotsArgs":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if (self.end_date - self.start_date).days >= MAX_SLOT_SEARCH_DAYS:
            raise ValueError(f"date range must be at most {MAX_SLOT_SEARCH_DAYS} days")
        return self


async def get_available_slots(args: GetAvailableSlotsArgs, ctx: ToolContext) -> dict[str, Any]:
    """Return open slots. READ-ONLY: never reserves or holds a slot."""

    async def call() -> dict[str, Any]:
        result = await service.get_available_slots(
            ctx.session,
            appointment_type_code=args.appointment_type,
            start_date=args.start_date,
            end_date=args.end_date,
            provider_id=args.provider_id,
        )
        return {
            "status": "ok",
            "appointment_type": result.appointment_type.code,
            "duration_minutes": result.appointment_type.duration_minutes,
            "slots": [
                {
                    "slot_id": slot.token,
                    "starts_at": _local_iso(slot.starts_at),
                    "provider_name": slot.provider_name,
                }
                for slot in result.slots
            ],
        }

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# propose_booking
# --------------------------------------------------------------------------- #
class ProposeBookingArgs(ToolArgs):
    slot_id: str = Field(
        min_length=1, max_length=512, description="slot_id returned by get_available_slots."
    )
    patient_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Only when booking for a family member: an id from get_authorized_family_members. "
            "Omit to book for the logged-in patient."
        ),
    )
    reason: str | None = Field(default=None, max_length=300, description="Optional visit reason.")


async def propose_booking(args: ProposeBookingArgs, ctx: ToolContext) -> dict[str, Any]:
    """Show the patient a confirmation card. This does NOT book anything.

    The appointment is only created when the patient presses Confirm on the card, via an
    authenticated endpoint the model cannot call.
    """

    async def call() -> dict[str, Any]:
        view = await proposals.create_booking_proposal(
            ctx.session,
            actor_user_id=ctx.user_id,
            actor_patient_id=ctx.patient_id,
            target_patient_id=args.patient_id or ctx.patient_id,
            conversation_id=ctx.conversation_id,
            slot_token=args.slot_id,
            reason=args.reason,
        )
        return {
            "status": "awaiting_patient_confirmation",
            "appointment_type": view.appointment_type_name,
            "starts_at": _local_iso(view.starts_at),
            "provider_name": view.provider_name,
            "patient_first_name": view.patient_first_name,
            "message": (
                "A confirmation card with Confirm and Decline buttons is now shown to the "
                "patient. Nothing is booked yet. Do not say it is booked; briefly ask the "
                "patient to review and confirm the card."
            ),
        }

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# reschedule_appointment
# --------------------------------------------------------------------------- #
class RescheduleAppointmentArgs(ToolArgs):
    appointment_id: uuid.UUID = Field(description="Existing appointment to move.")
    new_slot_id: str = Field(
        min_length=1, max_length=512, description="slot_id returned by get_available_slots."
    )
    confirmed: bool = Field(
        description=(
            "True ONLY after the patient explicitly confirmed moving this appointment to the "
            "new date and time in their latest message."
        )
    )


async def reschedule_appointment(args: RescheduleAppointmentArgs, ctx: ToolContext) -> dict[str, Any]:
    """Move an existing appointment to a new slot atomically."""

    async def call() -> dict[str, Any]:
        outcome = await service.reschedule_appointment(
            ctx.session,
            actor_user_id=ctx.user_id,
            actor_patient_id=ctx.patient_id,
            appointment_id=args.appointment_id,
            new_slot_token=args.new_slot_id,
            confirmed=args.confirmed,
        )
        return {
            **_appointment_dict(outcome.appointment),
            "status": "rescheduled",
            "old_starts_at": _local_iso(outcome.old_starts_at),
        }

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# propose_cancellation
# --------------------------------------------------------------------------- #
class ProposeCancellationArgs(ToolArgs):
    appointment_id: uuid.UUID = Field(
        description="Appointment to cancel, from get_patient_appointments."
    )
    reason: str | None = Field(default=None, max_length=300, description="Optional reason.")


async def propose_cancellation(args: ProposeCancellationArgs, ctx: ToolContext) -> dict[str, Any]:
    """Show the patient a cancellation card. This does NOT cancel anything.

    The appointment is only cancelled when the patient presses Confirm on the card, via an
    authenticated endpoint the model cannot call.
    """

    async def call() -> dict[str, Any]:
        view = await cancel_proposals.create_cancellation_proposal(
            ctx.session,
            actor_user_id=ctx.user_id,
            actor_patient_id=ctx.patient_id,
            conversation_id=ctx.conversation_id,
            appointment_id=args.appointment_id,
            reason=args.reason,
        )
        result: dict[str, Any] = {
            "status": "awaiting_patient_confirmation",
            "appointment_type": view.appointment_type_name,
            "starts_at": _local_iso(view.starts_at),
            "provider_name": view.provider_name,
            "patient_first_name": view.patient_first_name,
            "late_cancellation": view.late_cancellation,
            "message": (
                "A confirmation card with Confirm and Keep buttons is now shown to the "
                "patient. Nothing is cancelled yet. Do not say it is cancelled; briefly ask "
                "the patient to review and confirm the card."
            ),
        }
        if view.policy_note:
            result["policy_note"] = view.policy_note
        return result

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# get_appointment_types
# --------------------------------------------------------------------------- #
class GetAppointmentTypesArgs(ToolArgs):
    pass


async def get_appointment_types(args: GetAppointmentTypesArgs, ctx: ToolContext) -> dict[str, Any]:
    """Reference list of services: durations, eligibility and escalation rules."""
    types = await service.list_appointment_types(ctx.session)
    return {
        "status": "ok",
        "appointment_types": [
            {
                "code": t.code,
                "name": t.name,
                "description": t.description,
                "duration_minutes": t.duration_minutes,
                "minimum_age_years": t.min_age_years,
                "bookable_by_assistant": not t.requires_staff_booking,
                "staff_note": t.escalation_note if t.requires_staff_booking else None,
            }
            for t in types
        ],
    }


# --------------------------------------------------------------------------- #
# get_patient_appointments
# --------------------------------------------------------------------------- #
class GetPatientAppointmentsArgs(ToolArgs):
    patient_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Provide the currently booked appointments and possibly past appointments for a patient"
        ),
    )
    include_past: bool = Field(default=False, description="Also include past and cancelled ones.")


async def get_patient_appointments(args: GetPatientAppointmentsArgs, ctx: ToolContext) -> dict[str, Any]:
    """List the logged-in patient's (or an authorized dependent's) appointments."""

    async def call() -> dict[str, Any]:
        views = await service.list_patient_appointments(
            ctx.session,
            actor_patient_id=ctx.patient_id,
            patient_id=args.patient_id,
            include_past=args.include_past,
            limit=MAX_LISTED_APPOINTMENTS,
        )
        return {"status": "ok", "appointments": [_appointment_dict(v) for v in views]}

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# get_appointment_details
# --------------------------------------------------------------------------- #
class GetAppointmentDetailsArgs(ToolArgs):
    appointment_id: uuid.UUID = Field(description="Appointment id from get_patient_appointments.")


async def get_appointment_details(args: GetAppointmentDetailsArgs, ctx: ToolContext) -> dict[str, Any]:
    """Details of one appointment, only if the patient may manage it."""

    async def call() -> dict[str, Any]:
        view = await service.get_appointment_details(
            ctx.session, actor_patient_id=ctx.patient_id, appointment_id=args.appointment_id
        )
        return {**_appointment_dict(view), "ends_at": _local_iso(view.ends_at), "reason": view.reason}

    return await _guarded(ctx, call)


# --------------------------------------------------------------------------- #
# get_booking_status
# --------------------------------------------------------------------------- #
class GetBookingStatusArgs(ToolArgs):
    slot_id: str = Field(
        min_length=1, max_length=512, description="The slot_id of the booking attempt to check."
    )
    patient_id: uuid.UUID | None = Field(
        default=None, description="Only if the booking was for a family member."
    )


async def get_booking_status(args: GetBookingStatusArgs, ctx: ToolContext) -> dict[str, Any]:
    """Ground truth after a timeout or error during booking. Never guess; call this."""

    async def call() -> dict[str, Any]:
        view = await service.get_booking_status(
            ctx.session,
            actor_patient_id=ctx.patient_id,
            target_patient_id=args.patient_id or ctx.patient_id,
            conversation_id=ctx.conversation_id,
            slot_token=args.slot_id,
        )
        if view is None:
            return {"status": "not_booked", "message": "No booking exists for that slot."}
        return {**_appointment_dict(view), "status": "booked"}

    return await _guarded(ctx, call)


APPOINTMENT_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_appointment_types",
        description=(
            "List the appointment types the clinic offers with durations, eligibility and "
            "whether staff must arrange them. Use to find the right type code."
        ),
        args_model=GetAppointmentTypesArgs,
        handler=get_appointment_types,
    ),
    ToolDefinition(
        name="get_patient_appointments",
        description="List the logged-in patient's upcoming appointments (or a family member's).",
        args_model=GetPatientAppointmentsArgs,
        handler=get_patient_appointments,
    ),
    ToolDefinition(
        name="get_appointment_details",
        description="Get the details of one appointment by id.",
        args_model=GetAppointmentDetailsArgs,
        handler=get_appointment_details,
    ),
    ToolDefinition(
        name="get_booking_status",
        description=(
            "Check whether a booking attempt actually succeeded. Use after a timeout or "
            "internal error instead of assuming."
        ),
        args_model=GetBookingStatusArgs,
        handler=get_booking_status,
    ),
    ToolDefinition(
        name="get_available_slots",
        description=(
            "Find open appointment times for an appointment type within a date range. "
            "Read-only: it does not hold or book anything."
        ),
        args_model=GetAvailableSlotsArgs,
        handler=get_available_slots,
    ),
    ToolDefinition(
        name="propose_booking",
        description=(
            "After the patient picks a slot from get_available_slots, show them a confirmation "
            "card for it. This does not book: the patient confirms with a button."
        ),
        args_model=ProposeBookingArgs,
        handler=propose_booking,
    ),
    ToolDefinition(
        name="reschedule_appointment",
        description=(
            "Move an existing appointment to a new slot from get_available_slots. Only call "
            "after the patient explicitly confirmed."
        ),
        args_model=RescheduleAppointmentArgs,
        handler=reschedule_appointment,
    ),
    ToolDefinition(
        name="propose_cancellation",
        description=(
            "When the patient wants to cancel an appointment, show them a confirmation card "
            "for it. This does not cancel: the patient confirms with a button."
        ),
        args_model=ProposeCancellationArgs,
        handler=propose_cancellation,
    ),
]
