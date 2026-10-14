import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.family.models import PatientGuardian


async def manageable_patient_ids(session: AsyncSession, patient_id: uuid.UUID) -> set[uuid.UUID]:
    """The patient themself plus every dependent they are allowed to book for."""
    result = await session.execute(
        select(PatientGuardian.dependent_patient_id).where(
            PatientGuardian.guardian_patient_id == patient_id, PatientGuardian.can_book.is_(True)
        )
    )
    return {patient_id, *result.scalars().all()}
