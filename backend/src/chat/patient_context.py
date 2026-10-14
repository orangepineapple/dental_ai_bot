"""A short, server-built summary of the logged-in patient, added to the system prompt.

Facts come from the database, never from the model. Keep it minimal: first names, visit
dates and appointment types only (no contact details, birth dates or ids).
"""

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments.models import BOOKED, COMPLETED, Appointment, AppointmentType
from src.chat.config import ChatConfig
from src.family.models import PatientGuardian
from src.patients.models import Patient

MAX_FAMILY_LISTED = 6


def _clean_name(value: str) -> str:
    # Names are user-entered text that ends up in a prompt: single line, short, no markup.
    return re.sub(r"[^\w' .-]", "", value).strip()[:40]


def _months_between(earlier: datetime, later: datetime) -> int:
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    return months - (1 if later.day < earlier.day else 0)


async def build_patient_context(session: AsyncSession, patient_id: uuid.UUID, config: ChatConfig, now: datetime | None = None) -> str:
    """Return a prompt section describing the patient, or "" if the patient is not found."""
    tz = ZoneInfo(config.CLINIC_TIMEZONE)
    now = now or datetime.now(tz)

    patient = await session.get(Patient, patient_id)
    if patient is None:
        return ""

    past = (
        select(Appointment.starts_at, AppointmentType.name)
        .join(AppointmentType, AppointmentType.code == Appointment.appointment_type_code)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status.in_((COMPLETED, BOOKED)),
            Appointment.starts_at < now,
        )
        .order_by(Appointment.starts_at.desc())
    )
    last_visit = (await session.execute(past.limit(1))).first()
    upcoming = (
        await session.execute(
            select(Appointment.starts_at, AppointmentType.name)
            .join(AppointmentType, AppointmentType.code == Appointment.appointment_type_code)
            .where(Appointment.patient_id == patient_id, Appointment.status == BOOKED, Appointment.starts_at >= now)
            .order_by(Appointment.starts_at)
            .limit(1)
        )
    ).first()
    dependents = (
        await session.execute(
            select(Patient.first_name)
            .join(PatientGuardian, PatientGuardian.dependent_patient_id == Patient.id)
            .where(PatientGuardian.guardian_patient_id == patient_id, PatientGuardian.can_book.is_(True))
            .order_by(Patient.first_name)
            .limit(MAX_FAMILY_LISTED)
        )
    ).scalars().all()

    def ago(starts_at: datetime) -> str:
        months = _months_between(starts_at.astimezone(tz), now)
        return "less than a month ago" if months < 1 else f"about {months} month{'s' if months != 1 else ''} ago"

    lines = [f"Patient: {_clean_name(patient.first_name) or 'unknown'}"]
    if last_visit:
        lines.append(f"Last visit: {_clean_name(last_visit.name)}, {ago(last_visit.starts_at)}")
    else:
        lines.append("Last visit: none on record; likely a new patient, so do not assume prior history or a reason for visiting")
    if upcoming:
        when = upcoming.starts_at.astimezone(tz)
        lines.append(f"Next appointment: {_clean_name(upcoming.name)} on {when:%A %Y-%m-%d} at {when:%-I:%M %p}")
    else:
        lines.append("Next appointment: none scheduled")
    names = [n for n in (_clean_name(d) for d in dependents) if n]
    if names:
        lines.append(f"Family members this patient can manage: {', '.join(names)}")

    if config.DEBUG_PRINT:
        print("User context")
        print(       "Patient context (from clinic records, for light personalization only: greet by first "
                "name, but do not assume why they are here and always use tools for current "
                "availability and appointment details):\n" + "\n".join(f"- {line}" for line in lines))

    return (
        "Patient context (from clinic records, for light personalization only: greet by first "
        "name, but do not assume why they are here and always use tools for current "
        "availability and appointment details):\n" + "\n".join(f"- {line}" for line in lines)
    )
