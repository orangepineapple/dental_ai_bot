"""SAMPLE development data. The clinic must supply its real types, providers and hours.

    python -m src.appointments.seed

Idempotent: safe to run repeatedly.
"""

import asyncio
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.appointments.models import (
    AppointmentType,
    Provider,
    ProviderAppointmentType,
    ProviderWorkingHours,
)

SAMPLE_TYPES = [
    {"code": "cleaning", "name": "Cleaning", "duration_minutes": 60, "description": "Routine cleaning."},
    {"code": "exam", "name": "Exam", "duration_minutes": 30, "description": "Routine dental exam."},
    {"code": "filling", "name": "Filling", "duration_minutes": 60, "description": "Cavity filling."},
    {
        "code": "wisdom_tooth_extraction",
        "name": "Wisdom tooth extraction",
        "duration_minutes": 90,
        "requires_staff_booking": True,
        "escalation_note": "SAMPLE: this procedure is arranged by clinic staff after a consultation.",
    },
]
SAMPLE_PROVIDERS = ["Dr. Lee", "Dr. Patel"]
SAMPLE_HOURS = [(time(9), time(12)), (time(13), time(17))]  # Monday-Friday, clinic local time


async def seed_sample_data(session: AsyncSession) -> None:
    for data in SAMPLE_TYPES:
        if await session.get(AppointmentType, data["code"]) is None:
            session.add(AppointmentType(**data))
    await session.flush()

    for name in SAMPLE_PROVIDERS:
        provider = (
            await session.execute(select(Provider).where(Provider.name == name))
        ).scalar_one_or_none()
        if provider is not None:
            continue
        provider = Provider(name=name, role="dentist")
        session.add(provider)
        await session.flush()
        for data in SAMPLE_TYPES:
            session.add(
                ProviderAppointmentType(provider_id=provider.id, appointment_type_code=data["code"])
            )
        for weekday in range(5):
            for start, end in SAMPLE_HOURS:
                session.add(
                    ProviderWorkingHours(
                        provider_id=provider.id, weekday=weekday, start_time=start, end_time=end
                    )
                )
    await session.commit()


async def _main() -> None:
    from src.database import SessionFactory, engine

    async with SessionFactory() as session:
        await seed_sample_data(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
