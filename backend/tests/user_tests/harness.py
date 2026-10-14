"""Shared helpers for the manual user-test scenarios.

Each scenario_*.py file RESETS the development database, seeds one known state and prints
the login to use in the frontend. Run from backend/:

    python -m tests.user_tests.scenario_01_new_patient --yes

WARNING: this TRUNCATES every table in the database DATABASE_URL points at. Use a
development database, never one holding data you care about. Pytest does not collect
these files (they are not named test_*.py); the integration tests use TEST_DATABASE_URL.
"""

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments import models as _appointments_models  # noqa: F401  (registers tables)
from src.appointments.config import appointment_settings
from src.appointments.models import (
    BOOKED,
    CANCELLED,
    COMPLETED,
    Appointment,
    ClinicClosure,
    Provider,
)
from src.appointments.seed import seed_sample_data
from src.auth import models as _auth_models  # noqa: F401
from src.auth.models import AuthUser
from src.auth.utils import hash_password
from src.chat import models as _chat_models  # noqa: F401
from src.emergency import models as _emergency_models  # noqa: F401
from src.family import models as _family_models  # noqa: F401
from src.family.models import PatientGuardian
from src.models import Base
from src.patients.models import Patient

PASSWORD = "Test-Password-123"
CLINIC_TZ = ZoneInfo(appointment_settings.CLINIC_TIMEZONE)

__all__ = [
    "BOOKED", "CANCELLED", "COMPLETED", "PASSWORD", "Account", "Person",
    "add_appointment", "add_closure", "add_person", "guardian_of", "clinic_dt", "workday_dt", "providers", "run",
]


@dataclass
class Person:
    patient_id: uuid.UUID
    first_name: str
    email: str | None = None
    user_id: uuid.UUID | None = None


@dataclass
class Account:
    """A login to use in the frontend."""

    label: str
    email: str
    password: str = PASSWORD


def clinic_dt(days: int, hour: int, minute: int = 0) -> datetime:
    """A UTC datetime at hour:minute CLINIC-LOCAL, `days` from today.

    Weekends are skipped (forward for future days, backward for past days) because the
    sample clinic works Monday-Friday.
    """
    day = datetime.now(CLINIC_TZ).date() + timedelta(days=days)
    step = 1 if days >= 0 else -1
    while day.weekday() >= 5:
        day += timedelta(days=step)
    return datetime.combine(day, time(hour, minute), tzinfo=CLINIC_TZ).astimezone(UTC)


def workday_dt(n: int, hour: int, minute: int = 0) -> datetime:
    """Like clinic_dt but `n` is the nth FUTURE business day, so different n are always different dates."""
    day = datetime.now(CLINIC_TZ).date()
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return datetime.combine(day, time(hour, minute), tzinfo=CLINIC_TZ).astimezone(UTC)


async def add_person(
    session: AsyncSession, first: str, last: str, birth: date, *, email: str | None = None
) -> Person:
    """A patient. With `email` they also get a login; without, they are a managed dependent."""
    patient = Patient(first_name=first, last_name=last, birth_date=birth)
    session.add(patient)
    await session.flush()
    person = Person(patient.id, first)
    if email:
        user = AuthUser(email=email.lower(), password_hash=hash_password(PASSWORD), patient_id=patient.id)
        session.add(user)
        await session.flush()
        person.email, person.user_id = user.email, user.id
    return person


def guardian_of(session: AsyncSession, guardian: Person, dependent: Person, *, can_book: bool = True) -> None:
    session.add(
        PatientGuardian(
            guardian_patient_id=guardian.patient_id, dependent_patient_id=dependent.patient_id, can_book=can_book
        )
    )


async def providers(session: AsyncSession) -> dict[str, Provider]:
    rows = (await session.execute(Provider.__table__.select())).all()
    return {row.name: row for row in rows}


async def add_appointment(
    session: AsyncSession,
    person: Person,
    provider: Provider,
    type_code: str,
    starts_at: datetime,
    *,
    status: str = BOOKED,
    reason: str | None = None,
) -> None:
    from src.appointments.models import AppointmentType

    appt_type = await session.get(AppointmentType, type_code)
    session.add(
        Appointment(
            patient_id=person.patient_id,
            booked_by_user_id=person.user_id,
            provider_id=provider.id,
            appointment_type_code=type_code,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=appt_type.duration_minutes),
            status=status,
            reason=reason,
            cancelled_at=datetime.now(UTC) if status == CANCELLED else None,
        )
    )


async def add_closure(session: AsyncSession, workdays: int, reason: str) -> date:
    local = workday_dt(workdays, 12).astimezone(CLINIC_TZ).date()
    session.add(ClinicClosure(closure_date=local, reason=reason))
    return local


async def _reset(session: AsyncSession) -> None:
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    await session.commit()
    await seed_sample_data(session)


def run(title: str, seed: Callable[[AsyncSession], Awaitable[list[Account]]]) -> None:
    """Entry point for a scenario: confirm, reset the DB, seed, print the logins."""
    from src.config import settings
    from src.database import SessionFactory, engine

    db_url = str(settings.DATABASE_URL)
    target = db_url.rsplit("@", 1)[-1]

    async def _main() -> None:
        async with SessionFactory() as session:
            await _reset(session)
            accounts = await seed(session)
            await session.commit()
        await engine.dispose()
        print(f"\nScenario seeded: {title}")
        for acct in accounts:
            print(f"  [{acct.label}] log in as  {acct.email}  /  {acct.password}")
        print("\nNow follow the message script in the scenario file's header comment.\n")

    if "--yes" not in sys.argv:
        print(f"This will ERASE ALL DATA in: {target}\nRe-run with --yes to continue.")
        sys.exit(1)
    asyncio.run(_main())
