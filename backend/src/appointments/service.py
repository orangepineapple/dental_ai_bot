"""Scheduling service. Every rule that matters is enforced here, not in the prompt.

Conventions:
- Functions take the caller's session and COMMIT their own writes, so a returned
  success always means the transaction committed.
- Business-rule outcomes are raised as AppointmentError subclasses (nothing written).
  The caller must roll back the session when it catches one (it may hold row locks).
- "Not yours" and "does not exist" are the same NotFound, so ids cannot be probed.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments.config import AppointmentConfig, appointment_settings
from src.appointments.exceptions import (
    NeedsConfirmation,
    NotAuthorized,
    NotFound,
    PolicyViolation,
    RequiresStaff,
    SlotUnavailable,
    UnknownAppointmentType,
)
from src.appointments.models import (
    BOOKED,
    CANCELLED,
    Appointment,
    AppointmentAudit,
    AppointmentType,
    ClinicClosure,
    Provider,
    ProviderAppointmentType,
    ProviderWorkingHours,
)
from src.appointments.scheduling import WorkingWindow, candidate_starts, overlaps
from src.appointments.slot_token import SlotClaims, issue_slot_token, verify_slot_token
from src.family.service import manageable_patient_ids
from src.patients.models import Patient

DEFAULT_STAFF_NOTE = "This type of appointment has to be arranged with the clinic staff."


@dataclass(frozen=True, slots=True)
class SlotOption:
    token: str
    starts_at: datetime
    provider_id: uuid.UUID
    provider_name: str


@dataclass(frozen=True, slots=True)
class SlotSearchResult:
    appointment_type: AppointmentType
    slots: list[SlotOption]


@dataclass(frozen=True, slots=True)
class AppointmentView:
    id: uuid.UUID
    status: str
    appointment_type_code: str
    appointment_type_name: str
    starts_at: datetime
    ends_at: datetime
    provider_name: str
    patient_id: uuid.UUID
    patient_first_name: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    appointment: AppointmentView
    late_cancellation: bool
    policy_note: str | None


@dataclass(frozen=True, slots=True)
class RescheduleOutcome:
    appointment: AppointmentView
    old_starts_at: datetime


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tz(config: AppointmentConfig) -> ZoneInfo:
    return ZoneInfo(config.CLINIC_TIMEZONE)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def booking_idempotency_key(conversation_id: uuid.UUID, patient_id: uuid.UUID, claims: SlotClaims) -> str:
    """Same conversation + patient + provider + start time => same key => one appointment."""
    raw = f"{conversation_id}:{patient_id}:{claims.provider_id}:{_utc(claims.starts_at).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _age_years(birth_date: date, on: date) -> int:
    return on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))


def _view_select():
    return (
        select(
            Appointment,
            Provider.name.label("provider_name"),
            AppointmentType.name.label("type_name"),
            Patient.first_name.label("patient_first_name"),
        )
        .join(Provider, Provider.id == Appointment.provider_id)
        .join(AppointmentType, AppointmentType.code == Appointment.appointment_type_code)
        .join(Patient, Patient.id == Appointment.patient_id)
    )


def _to_view(row: Any) -> AppointmentView:
    appt: Appointment = row.Appointment
    return AppointmentView(
        id=appt.id,
        status=appt.status,
        appointment_type_code=appt.appointment_type_code,
        appointment_type_name=row.type_name,
        starts_at=appt.starts_at,
        ends_at=appt.ends_at,
        provider_name=row.provider_name,
        patient_id=appt.patient_id,
        patient_first_name=row.patient_first_name,
        reason=appt.reason,
    )


async def _load_view(session: AsyncSession, appointment_id: uuid.UUID) -> AppointmentView:
    row = (await session.execute(_view_select().where(Appointment.id == appointment_id))).one()
    return _to_view(row)


async def _get_active_type(session: AsyncSession, code: str) -> AppointmentType:
    appt_type = await session.get(AppointmentType, code)
    if appt_type is None or not appt_type.is_active:
        raise UnknownAppointmentType(f"'{code}' is not an appointment type the clinic offers.")
    return appt_type


async def _lock_provider(session: AsyncSession, provider_id: uuid.UUID) -> Provider:
    """Row lock that serialises concurrent bookings for one provider."""
    provider = (
        await session.execute(select(Provider).where(Provider.id == provider_id).with_for_update())
    ).scalar_one_or_none()
    if provider is None or not provider.is_active:
        raise SlotUnavailable("That time slot is no longer available.")
    return provider


async def _count_overlaps(session: AsyncSession, column: Any, value: uuid.UUID, start: datetime, end: datetime, exclude_id: uuid.UUID | None) -> int:
    query = select(func.count()).select_from(Appointment).where(
        column == value,
        Appointment.status == BOOKED,
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )
    if exclude_id is not None:
        query = query.where(Appointment.id != exclude_id)
    return (await session.execute(query)).scalar_one()


async def _load_windows(session: AsyncSession, provider_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[WorkingWindow]]:
    rows = await session.execute(
        select(ProviderWorkingHours).where(ProviderWorkingHours.provider_id.in_(provider_ids))
    )
    windows: dict[uuid.UUID, list[WorkingWindow]] = {pid: [] for pid in provider_ids}
    for row in rows.scalars():
        windows[row.provider_id].append(WorkingWindow(row.weekday, row.start_time, row.end_time))
    return windows


async def _validate_slot(session: AsyncSession, provider: Provider, appt_type: AppointmentType, starts_at: datetime, now: datetime, config: AppointmentConfig) -> None:
    """Re-validate an offered slot against current data (hours may have changed)."""
    tz = _tz(config)
    unavailable = SlotUnavailable("That time slot is no longer available.")
    if starts_at < now + timedelta(minutes=config.MIN_NOTICE_MINUTES):
        raise SlotUnavailable("That time is too soon to book. Please choose a later time.")
    if starts_at > now + timedelta(days=config.MAX_ADVANCE_DAYS):
        raise SlotUnavailable("That time is too far in the future to book.")

    offers_type = await session.get(ProviderAppointmentType, (provider.id, appt_type.code))
    if offers_type is None:
        raise unavailable
    local_day = starts_at.astimezone(tz).date()
    if await session.get(ClinicClosure, local_day) is not None:
        raise unavailable
    windows = (await _load_windows(session, [provider.id]))[provider.id]
    starts = candidate_starts(
        local_day,
        windows,
        timedelta(minutes=appt_type.duration_minutes),
        timedelta(minutes=config.SLOT_INTERVAL_MINUTES),
        tz,
    )
    if _utc(starts_at) not in starts:
        raise unavailable


async def _check_patient_rules(session: AsyncSession, patient_id: uuid.UUID, appt_type: AppointmentType, start: datetime, end: datetime, now: datetime, exclude_id: uuid.UUID | None, config: AppointmentConfig) -> None:
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise NotAuthorized("That patient could not be found.")
    if appt_type.min_age_years is not None:
        local_day = start.astimezone(_tz(config)).date()
        if _age_years(patient.birth_date, local_day) < appt_type.min_age_years:
            raise PolicyViolation(
                f"{appt_type.name} is not available for this patient's age. Clinic staff can help."
            )
    if await _count_overlaps(session, Appointment.patient_id, patient_id, start, end, exclude_id):
        raise PolicyViolation("This patient already has an appointment at that time.")
    limit = config.MAX_ACTIVE_APPOINTMENTS_PER_PATIENT
    if limit is not None and exclude_id is None:
        active = (
            await session.execute(
                select(func.count()).select_from(Appointment).where(
                    Appointment.patient_id == patient_id,
                    Appointment.status == BOOKED,
                    Appointment.ends_at > now,
                )
            )
        ).scalar_one()
        if active >= limit:
            raise PolicyViolation("This patient has reached the maximum number of upcoming appointments.")


@dataclass(frozen=True, slots=True)
class BookingPlan:
    appt_type: AppointmentType
    provider: Provider
    start: datetime
    end: datetime


async def plan_booking(session: AsyncSession, *, target_patient_id: uuid.UUID, claims: SlotClaims, now: datetime, config: AppointmentConfig, lock: bool) -> BookingPlan:
    """Check every booking rule for an offered slot. Raises AppointmentError if it fails.

    lock=True takes the provider row lock (use inside the booking transaction).
    lock=False is a read-only pre-check, used when only proposing a booking.
    """
    appt_type = await _get_active_type(session, claims.appointment_type)
    if appt_type.requires_staff_booking:
        raise RequiresStaff(appt_type.escalation_note or DEFAULT_STAFF_NOTE)

    start = _utc(claims.starts_at)
    end = start + timedelta(minutes=appt_type.duration_minutes)
    if lock:
        provider = await _lock_provider(session, claims.provider_id)
    else:
        provider = await session.get(Provider, claims.provider_id)
        if provider is None or not provider.is_active:
            raise SlotUnavailable("That time slot is no longer available.")
    await _validate_slot(session, provider, appt_type, start, now, config)
    if await _count_overlaps(session, Appointment.provider_id, provider.id, start, end, None):
        raise SlotUnavailable("That time slot was just taken. Search for available times again.")
    await _check_patient_rules(session, target_patient_id, appt_type, start, end, now, None, config)
    return BookingPlan(appt_type, provider, start, end)


def _audit(appointment_id: uuid.UUID, action: str, actor_user_id: uuid.UUID, details: dict[str, Any]) -> AppointmentAudit:
    return AppointmentAudit(appointment_id=appointment_id, action=action, actor_user_id=actor_user_id, details=details)


async def _load_authorized_for_update(session: AsyncSession, actor_patient_id: uuid.UUID, appointment_id: uuid.UUID) -> Appointment:
    appt = (
        await session.execute(
            select(Appointment).where(Appointment.id == appointment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if appt is None or appt.patient_id not in await manageable_patient_ids(session, actor_patient_id):
        raise NotFound("No such appointment was found for this patient.")
    return appt


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
async def list_appointment_types(session: AsyncSession) -> list[AppointmentType]:
    result = await session.execute(
        select(AppointmentType).where(AppointmentType.is_active.is_(True)).order_by(AppointmentType.name)
    )
    return list(result.scalars().all())


async def get_available_slots(session: AsyncSession, *, appointment_type_code: str, start_date: date, end_date: date, provider_id: uuid.UUID | None = None, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> SlotSearchResult:
    """Read-only. Offers slots; reserves nothing."""
    now = now or datetime.now(UTC)
    appt_type = await _get_active_type(session, appointment_type_code)
    if appt_type.requires_staff_booking:
        raise RequiresStaff(appt_type.escalation_note or DEFAULT_STAFF_NOTE)

    tz = _tz(config)
    first = max(start_date, now.astimezone(tz).date())
    last = min(end_date, now.astimezone(tz).date() + timedelta(days=config.MAX_ADVANCE_DAYS))
    if last < first:
        return SlotSearchResult(appt_type, [])

    provider_query = (
        select(Provider)
        .join(ProviderAppointmentType, ProviderAppointmentType.provider_id == Provider.id)
        .where(
            ProviderAppointmentType.appointment_type_code == appt_type.code,
            Provider.is_active.is_(True),
        )
    )
    if provider_id is not None:
        provider_query = provider_query.where(Provider.id == provider_id)
    providers = list((await session.execute(provider_query)).scalars().all())
    if not providers:
        return SlotSearchResult(appt_type, [])

    provider_ids = [p.id for p in providers]
    windows = await _load_windows(session, provider_ids)
    closures = set(
        (
            await session.execute(
                select(ClinicClosure.closure_date).where(
                    ClinicClosure.closure_date.between(first, last)
                )
            )
        )
        .scalars()
        .all()
    )
    range_start = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    range_end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(UTC)
    booked_rows = await session.execute(
        select(Appointment.provider_id, Appointment.starts_at, Appointment.ends_at).where(
            Appointment.status == BOOKED,
            Appointment.provider_id.in_(provider_ids),
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        )
    )
    booked: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {pid: [] for pid in provider_ids}
    for pid, starts_at, ends_at in booked_rows.all():
        booked[pid].append((starts_at, ends_at))

    duration = timedelta(minutes=appt_type.duration_minutes)
    interval = timedelta(minutes=config.SLOT_INTERVAL_MINUTES)
    earliest = now + timedelta(minutes=config.MIN_NOTICE_MINUTES)
    expires_at = now + timedelta(minutes=config.SLOT_TOKEN_TTL_MINUTES)

    options: list[SlotOption] = []
    day = first
    while day <= last:
        if day not in closures:
            for provider in providers:
                for start in candidate_starts(day, windows[provider.id], duration, interval, tz):
                    if start < earliest:
                        continue
                    if any(overlaps(start, start + duration, b0, b1) for b0, b1 in booked[provider.id]):
                        continue
                    token = issue_slot_token(
                        config.slot_secret,
                        SlotClaims(provider.id, appt_type.code, start, expires_at),
                    )
                    options.append(SlotOption(token, start, provider.id, provider.name))
        day += timedelta(days=1)

    options.sort(key=lambda o: (o.starts_at, o.provider_name))
    return SlotSearchResult(appt_type, options[: config.MAX_SLOTS_RETURNED])


async def list_patient_appointments(session: AsyncSession, *, actor_patient_id: uuid.UUID, patient_id: uuid.UUID | None, include_past: bool, limit: int, now: datetime | None = None) -> list[AppointmentView]:
    """Appointments for the actor, or one dependent they may manage."""
    now = now or datetime.now(UTC)
    allowed = await manageable_patient_ids(session, actor_patient_id)
    target = patient_id or actor_patient_id
    if target not in allowed:
        raise NotAuthorized("You are not authorized to view that patient's appointments.")
    query = _view_select().where(Appointment.patient_id == target)
    if not include_past:
        query = query.where(Appointment.status == BOOKED, Appointment.ends_at > now)
    query = query.order_by(Appointment.starts_at).limit(limit)
    return [_to_view(row) for row in (await session.execute(query)).all()]


async def get_appointment_details(session: AsyncSession, *, actor_patient_id: uuid.UUID, appointment_id: uuid.UUID) -> AppointmentView:
    row = (await session.execute(_view_select().where(Appointment.id == appointment_id))).one_or_none()
    if row is None or row.Appointment.patient_id not in await manageable_patient_ids(session, actor_patient_id):
        raise NotFound("No such appointment was found for this patient.")
    return _to_view(row)


async def get_booking_status(session: AsyncSession, *, actor_patient_id: uuid.UUID, target_patient_id: uuid.UUID, conversation_id: uuid.UUID, slot_token: str, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> AppointmentView | None:
    """Ground truth for an uncertain/retried booking: the appointment, or None if not booked."""
    now = now or datetime.now(UTC)
    if target_patient_id not in await manageable_patient_ids(session, actor_patient_id):
        raise NotAuthorized("You are not authorized to manage that patient's appointments.")
    claims = verify_slot_token(config.slot_secret, slot_token, now, allow_expired=True)
    key = booking_idempotency_key(conversation_id, target_patient_id, claims)
    return await _find_booked_by_key(session, key)


async def _find_booked_by_key(session: AsyncSession, key: str) -> AppointmentView | None:
    row = (
        await session.execute(
            _view_select().where(Appointment.idempotency_key == key, Appointment.status == BOOKED)
        )
    ).one_or_none()
    return _to_view(row) if row else None


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
async def book_appointment(session: AsyncSession, *, actor_user_id: uuid.UUID, actor_patient_id: uuid.UUID, target_patient_id: uuid.UUID, conversation_id: uuid.UUID, slot_token: str, confirmed: bool, reason: str | None = None, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> AppointmentView:
    if not confirmed:
        raise NeedsConfirmation("Ask the patient to explicitly confirm the date, time and type first.")
    now = now or datetime.now(UTC)
    if target_patient_id not in await manageable_patient_ids(session, actor_patient_id):
        raise NotAuthorized("You are not authorized to book for that patient.")

    claims = verify_slot_token(config.slot_secret, slot_token, now)
    key = booking_idempotency_key(conversation_id, target_patient_id, claims)
    existing = await _find_booked_by_key(session, key)
    if existing is not None:
        return existing

    plan = await plan_booking(session, target_patient_id=target_patient_id, claims=claims, now=now, config=config, lock=True)
    appt_type, provider, start, end = plan.appt_type, plan.provider, plan.start, plan.end

    appointment_id = uuid.uuid4()
    session.add(
        Appointment(
            id=appointment_id,
            patient_id=target_patient_id,
            booked_by_user_id=actor_user_id,
            provider_id=provider.id,
            appointment_type_code=appt_type.code,
            starts_at=start,
            ends_at=end,
            status=BOOKED,
            idempotency_key=key,
            reason=reason,
        )
    )
    session.add(_audit(appointment_id, "booked", actor_user_id, {"starts_at": start.isoformat()}))
    try:
        # Flush inside the try: the exclusion/unique constraints fire here.
        await session.commit()
    except IntegrityError:
        # Lost a race (same key, or the overlap exclusion constraint). Re-read the truth.
        await session.rollback()
        existing = await _find_booked_by_key(session, key)
        if existing is not None:
            return existing
        raise SlotUnavailable("That time slot was just taken. Search for available times again.") from None
    return await _load_view(session, appointment_id)


async def reschedule_appointment(session: AsyncSession, *, actor_user_id: uuid.UUID, actor_patient_id: uuid.UUID, appointment_id: uuid.UUID, new_slot_token: str, confirmed: bool, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> RescheduleOutcome:
    if not confirmed:
        raise NeedsConfirmation("Ask the patient to explicitly confirm the new date and time first.")
    now = now or datetime.now(UTC)

    appt = await _load_authorized_for_update(session, actor_patient_id, appointment_id)
    if appt.status != BOOKED:
        raise PolicyViolation("Only an upcoming booked appointment can be rescheduled.")

    claims = verify_slot_token(config.slot_secret, new_slot_token, now)
    new_start = _utc(claims.starts_at)
    old_start = appt.starts_at
    if claims.provider_id == appt.provider_id and new_start == _utc(old_start):
        return RescheduleOutcome(await _load_view(session, appt.id), old_start)  # idempotent retry

    if _utc(old_start) <= now:
        raise PolicyViolation("That appointment has already started or passed.")
    if _utc(old_start) - now < timedelta(hours=config.LATE_CHANGE_WINDOW_HOURS):
        raise PolicyViolation(
            f"Appointments cannot be rescheduled within {config.LATE_CHANGE_WINDOW_HOURS} hours of "
            "the start time. The patient should contact the clinic staff."
        )
    if claims.appointment_type != appt.appointment_type_code:
        raise PolicyViolation("The new time must be for the same type of appointment.")

    appt_type = await _get_active_type(session, appt.appointment_type_code)
    new_end = new_start + timedelta(minutes=appt_type.duration_minutes)
    provider = await _lock_provider(session, claims.provider_id)
    await _validate_slot(session, provider, appt_type, new_start, now, config)
    if await _count_overlaps(session, Appointment.provider_id, provider.id, new_start, new_end, appt.id):
        raise SlotUnavailable("That time slot was just taken. Search for available times again.")
    await _check_patient_rules(session, appt.patient_id, appt_type, new_start, new_end, now, appt.id, config)

    details = {
        "old": {"provider_id": str(appt.provider_id), "starts_at": _utc(old_start).isoformat()},
        "new": {"provider_id": str(provider.id), "starts_at": new_start.isoformat()},
    }
    appt.provider_id = provider.id
    appt.starts_at = new_start
    appt.ends_at = new_end
    # The old key described the old slot; clear it so a retry of the original booking
    # can never resurrect or collide with this row.
    appt.idempotency_key = None
    session.add(_audit(appt.id, "rescheduled", actor_user_id, details))
    appointment_pk = appt.id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise SlotUnavailable("That time slot was just taken. Search for available times again.") from None
    return RescheduleOutcome(await _load_view(session, appointment_pk), old_start)


async def cancel_appointment(session: AsyncSession, *, actor_user_id: uuid.UUID, actor_patient_id: uuid.UUID, appointment_id: uuid.UUID, confirmed: bool, reason: str | None = None, now: datetime | None = None, config: AppointmentConfig = appointment_settings) -> CancelOutcome:
    if not confirmed:
        raise NeedsConfirmation("Ask the patient to explicitly confirm the cancellation first.")
    now = now or datetime.now(UTC)

    appt = await _load_authorized_for_update(session, actor_patient_id, appointment_id)
    if appt.status == CANCELLED:
        return CancelOutcome(await _load_view(session, appt.id), False, None)  # idempotent
    if appt.status != BOOKED:
        raise PolicyViolation("Only an upcoming booked appointment can be cancelled.")
    if _utc(appt.starts_at) <= now:
        raise PolicyViolation("That appointment has already started or passed.")

    late = _utc(appt.starts_at) - now < timedelta(hours=config.LATE_CHANGE_WINDOW_HOURS)
    if late and config.LATE_CANCEL_ACTION == "requires_staff":
        raise RequiresStaff(
            config.LATE_CANCEL_POLICY_NOTE
            or "This appointment is too close to cancel here. The patient should contact the clinic staff."
        )

    appt.status = CANCELLED
    appt.cancelled_at = now
    appt.cancellation_reason = reason
    session.add(_audit(appt.id, "cancelled", actor_user_id, {"late": late}))
    appointment_pk = appt.id
    await session.commit()
    note = config.LATE_CANCEL_POLICY_NOTE if late else None
    return CancelOutcome(await _load_view(session, appointment_pk), late, note)
