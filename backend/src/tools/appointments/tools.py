"""Model-facing scheduling tools.

STATUS: schemas and registration are final; the handlers are placeholders that
return `not_implemented`. Each handler's docstring is the implementation spec.
Fill them in AFTER the supporting service/DB layer exists in `src/appointments`,
and keep the handlers thin: authorize -> call service -> map to a minimal dict.

Rules that apply to every tool in this file (from CLAUDE.md):
- Identity comes from `ctx` (set by the server from the JWT), never from `args`.
- Re-validate in service code; the Pydantic model here is only the first gate.
- Return only fields the model needs to talk to the patient. No internal ids that
  are not required for a follow-up call, no PHI beyond the appointment itself.
- Never claim success unless the DB transaction committed.
"""

import uuid
from datetime import date
from typing import Any

from pydantic import Field, model_validator

from src.tools.base import ToolArgs, ToolContext, ToolDefinition

MAX_SLOT_SEARCH_DAYS = 31


def _not_implemented(tool_name: str) -> dict[str, Any]:
    # Worded so the model tells the patient the truth and offers a human instead.
    return {
        "status": "not_implemented",
        "tool": tool_name,
        "message": (
            "This action is not available yet and nothing was changed. Tell the patient "
            "you cannot do this right now and that the clinic staff can help."
        ),
    }


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
    """Return open appointment slots. READ-ONLY: must never reserve or hold a slot.

    Implementation spec
    -------------------
    Tables needed (none exist yet; create via Alembic):
      - appointment_type(code PK, name, duration_minutes, is_active,
            requires_provider_role, ...)           # also used by get_appointment_types
      - provider(id, name, role, is_active)
      - provider_working_hours(provider_id, weekday, start_time, end_time)
      - clinic_hours / clinic_closure(date, reason)   # holidays, closures
      - appointment(id, patient_id, provider_id, appointment_type_code, starts_at,
            ends_at, status['booked'|'cancelled'|'completed'|'no_show'],
            idempotency_key UNIQUE, created_at, updated_at)

    Steps:
      1. Look up `args.appointment_type`; unknown/inactive -> return
         {"status": "error", "error": "unknown_appointment_type"}. Take the
         duration and any eligibility/escalation rule from the type row (do NOT
         let the model pass a duration). If the type requires escalation
         (e.g. emergency/pain), return {"status": "requires_staff"} instead.
      2. Clamp the range to [today(clinic tz), +MAX_SLOT_SEARCH_DAYS]; never
         return slots in the past or inside the minimum-notice window.
      3. For each eligible provider (filtered by `args.provider_id` if given),
         generate candidate slots from working hours minus clinic closures minus
         existing non-cancelled `appointment` rows (overlap check on
         [starts_at, ends_at)). Convert to clinic timezone for display.
      4. Return at most ~20 slots, earliest first.

    Return shape:
      {
        "status": "ok",
        "appointment_type": "cleaning",
        "duration_minutes": 60,
        "slots": [
          {"slot_id": "<opaque id encoding provider+start, see below>",
           "starts_at": "2026-11-02T09:00:00-05:00",
           "provider_name": "Dr. Lee"}
        ]
      }
      or {"status": "ok", "slots": []} when nothing is free (model should offer
      a wider range or staff handoff).

    `slot_id` should be an opaque, server-signed token (provider_id + starts_at +
    appointment_type [+ expiry]) so book_appointment can verify it was issued by
    the server rather than invented by the model. It is NOT a reservation.
    """
    return _not_implemented("get_available_slots")


# --------------------------------------------------------------------------- #
# book_appointment
# --------------------------------------------------------------------------- #
class BookAppointmentArgs(ToolArgs):
    slot_id: str = Field(
        min_length=1, max_length=512, description="slot_id returned by get_available_slots."
    )
    confirmed: bool = Field(
        description=(
            "True ONLY after the patient explicitly said yes to this exact date, time and "
            "appointment type in their latest message."
        )
    )
    patient_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Only when booking for a family member: an id from get_authorized_family_members. "
            "Omit to book for the logged-in patient."
        ),
    )
    reason: str | None = Field(default=None, max_length=300, description="Optional visit reason.")


async def book_appointment(args: BookAppointmentArgs, ctx: ToolContext) -> dict[str, Any]:
    """Book one appointment for the logged-in patient (or an authorized dependent).

    Implementation spec
    -------------------
    Tables: appointment, appointment_type, provider (see get_available_slots),
    plus the family/guardian link table for dependents:
      - patient_guardian(guardian_patient_id, dependent_patient_id, can_book BOOL)

    Steps (all inside ONE DB transaction on `ctx.session`):
      1. If `args.confirmed` is not True -> {"status": "needs_confirmation"}; no
         writes. (Stronger option: have get_available_slots/a "propose" step
         persist a pending confirmation row and require its token here, so a
         model cannot self-assert `confirmed`.)
      2. Target patient = `args.patient_id` or `ctx.patient_id`. If it is not
         `ctx.patient_id`, require a patient_guardian row with can_book = true;
         otherwise return {"status": "error", "error": "not_authorized"}.
      3. Verify and decode `slot_id` (signature, expiry, appointment type).
      4. Idempotency key = sha256(f"{ctx.conversation_id}:{target}:{slot_id}").
         If an appointment with that key exists, return it as success (retry safe).
         Back this with a UNIQUE constraint, not just a SELECT.
      5. Re-check availability under lock: SELECT ... FOR UPDATE on the provider's
         overlapping appointments (or an exclusion constraint on
         (provider_id, tstzrange(starts_at, ends_at)) WHERE status = 'booked').
         If taken -> {"status": "slot_unavailable"}; model should re-run
         get_available_slots.
      6. Enforce policy: no double-booking the same patient at the same time,
         min notice, max future horizon, active-appointment limits.
      7. INSERT appointment (status 'booked'), commit.

    Return shape:
      {"status": "booked", "appointment_id": "<uuid>", "appointment_type": "cleaning",
       "starts_at": "<iso8601 clinic tz>", "provider_name": "Dr. Lee",
       "patient_first_name": "..."}   # first name only, to confirm who it is for
      Other statuses: needs_confirmation | slot_unavailable | not_authorized |
      policy_violation (with a short patient-safe "reason").
    Never return "booked" unless the commit succeeded.
    """
    return _not_implemented("book_appointment")


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


async def reschedule_appointment(
    args: RescheduleAppointmentArgs, ctx: ToolContext
) -> dict[str, Any]:
    """Move an existing appointment to a new slot atomically.

    Implementation spec
    -------------------
    Tables: appointment, appointment_type, provider, patient_guardian,
    clinic_policy (or config) for the reschedule window.

    Steps (one transaction):
      1. `confirmed` must be True, else {"status": "needs_confirmation"}.
      2. Load the appointment FOR UPDATE. Authorize: its patient_id is
         `ctx.patient_id` or a dependent with patient_guardian.can_book. A missing
         row and an unauthorized row must produce the SAME response
         ({"status": "not_found"}) so ids cannot be probed.
      3. Must be status 'booked' and in the future.
      4. Policy: reject inside the clinic's late-change window (e.g. < 24h)
         with {"status": "policy_violation", "reason": "..."} and suggest staff.
         The window is clinic policy: read it from config, do not hard-code it.
      5. Verify `new_slot_id`; its appointment type/duration must match the
         existing appointment (or be allowed by the type's rules).
      6. Re-check the new slot's availability under lock (same method as
         book_appointment). Do the old-slot release and new-slot claim in this
         same transaction so the patient is never left with zero or two slots.
         Simplest: UPDATE the same appointment row's provider_id/starts_at/ends_at
         and write an appointment_audit row (old and new values).
      7. Idempotent: if the appointment is already at the target slot, return ok.

    Return shape:
      {"status": "rescheduled", "appointment_id": "<uuid>",
       "old_starts_at": "<iso>", "new_starts_at": "<iso>", "provider_name": "..."}
      Other statuses: needs_confirmation | not_found | slot_unavailable |
      policy_violation.
    """
    return _not_implemented("reschedule_appointment")


# --------------------------------------------------------------------------- #
# cancel_appointment
# --------------------------------------------------------------------------- #
class CancelAppointmentArgs(ToolArgs):
    appointment_id: uuid.UUID = Field(description="Appointment to cancel.")
    confirmed: bool = Field(
        description=(
            "True ONLY after the patient explicitly confirmed cancelling this appointment "
            "in their latest message."
        )
    )
    reason: str | None = Field(default=None, max_length=300, description="Optional reason.")


async def cancel_appointment(args: CancelAppointmentArgs, ctx: ToolContext) -> dict[str, Any]:
    """Cancel an appointment, applying the clinic's cancellation policy.

    Implementation spec
    -------------------
    Tables: appointment (status, cancelled_at, cancellation_reason), patient_guardian,
    appointment_audit, clinic_policy/config (cancellation window, fee rules).

    Steps (one transaction):
      1. `confirmed` must be True, else {"status": "needs_confirmation"}.
      2. Load FOR UPDATE and authorize exactly as reschedule_appointment
         (same "not_found" for missing and unauthorized).
      3. Already 'cancelled' -> return {"status": "cancelled"} (idempotent).
         Only 'booked' future appointments can be cancelled.
      4. Policy: if inside the late-cancellation window, still allow only if clinic
         policy says so, and include {"late_cancellation": true, "policy_note": "..."}
         using clinic-provided wording. Do not invent fees; if the policy requires a
         human decision return {"status": "requires_staff"}.
      5. UPDATE status = 'cancelled', cancelled_at = now(), cancellation_reason;
         soft-cancel (never DELETE) so history and audit remain. Commit.

    Return shape:
      {"status": "cancelled", "appointment_id": "<uuid>", "starts_at": "<iso>",
       "late_cancellation": false}
      Other statuses: needs_confirmation | not_found | policy_violation |
      requires_staff.
    """
    return _not_implemented("cancel_appointment")


APPOINTMENT_TOOLS: list[ToolDefinition] = [
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
        name="book_appointment",
        description=(
            "Book an appointment into a slot from get_available_slots. Only call after the "
            "patient explicitly confirmed the exact date, time and type."
        ),
        args_model=BookAppointmentArgs,
        handler=book_appointment,
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
        name="cancel_appointment",
        description=(
            "Cancel an existing appointment. Only call after the patient explicitly confirmed."
        ),
        args_model=CancelAppointmentArgs,
        handler=cancel_appointment,
    ),
]
