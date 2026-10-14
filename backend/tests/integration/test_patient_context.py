from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.appointments.models import Appointment, Provider
from src.appointments.seed import seed_sample_data
from src.chat.config import chat_settings
from src.chat.patient_context import build_patient_context
from src.family.models import PatientGuardian
from tests.integration.test_appointments import make_person

pytestmark = pytest.mark.integration


async def test_new_patient_gets_first_name_and_no_history(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        person = await make_person(session, name="Jane")
        text = await build_patient_context(session, person.patient_id, chat_settings)

    assert "Patient: Jane" in text
    assert "none on record" in text
    assert "Next appointment: none scheduled" in text
    assert "Doe" not in text
    assert "cleaning" not in text.lower()


async def test_unknown_patient_returns_empty(session_factory: async_sessionmaker[AsyncSession]) -> None:
    import uuid

    async with session_factory() as session:
        assert await build_patient_context(session, uuid.uuid4(), chat_settings) == ""


async def test_history_upcoming_and_family(session_factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await seed_sample_data(session)
        parent = await make_person(session, name="Pat")
        child = await make_person(session, name="Sam")
        session.add(PatientGuardian(guardian_patient_id=parent.patient_id, dependent_patient_id=child.patient_id))
        provider = (await session.execute(Provider.__table__.select())).first()
        for start, status in ((now - timedelta(days=95), "completed"), (now + timedelta(days=5), "booked")):
            session.add(
                Appointment(
                    patient_id=parent.patient_id,
                    provider_id=provider.id,
                    appointment_type_code="exam",
                    starts_at=start,
                    ends_at=start + timedelta(minutes=30),
                    status=status,
                )
            )
        await session.commit()
        text = await build_patient_context(session, parent.patient_id, chat_settings, now=now)

    assert "Patient: Pat" in text
    assert "Last visit: Exam, about 3 months ago" in text
    assert "Next appointment: Exam on" in text
    assert "Sam" in text
