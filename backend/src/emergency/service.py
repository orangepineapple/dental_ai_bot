import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.emergency import sender
from src.emergency.config import emergency_settings
from src.emergency.models import DELIVERED, FAILED, EmergencyNotification
from src.patients.models import Patient


@dataclass(frozen=True, slots=True)
class NotificationOutcome:
    notification_id: uuid.UUID
    status: str  # delivered | failed


def get_emergency_instructions() -> str:
    """Clinic-approved wording only (see EmergencyConfig.INSTRUCTIONS)."""
    return emergency_settings.INSTRUCTIONS


def _idempotency_key(conversation_id: uuid.UUID, summary: str) -> str:
    normalized = " ".join(summary.split()).lower()
    return hashlib.sha256(f"{conversation_id}:{normalized}".encode()).hexdigest()


async def notify_staff_of_emergency(session: AsyncSession, *, user_id: uuid.UUID, patient_id: uuid.UUID, conversation_id: uuid.UUID, summary: str) -> NotificationOutcome:
    """Record the report, send it to the staff channel, and store the real delivery result.

    The row is committed BEFORE sending so a crash mid-send leaves evidence it was
    attempted. Repeating the same summary in a conversation re-attempts delivery only if
    it has not been delivered yet.
    """
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise LookupError("patient not found")

    key = _idempotency_key(conversation_id, summary)
    query = select(EmergencyNotification).where(EmergencyNotification.idempotency_key == key)
    notification = (await session.execute(query)).scalar_one_or_none()
    if notification is None:
        notification = EmergencyNotification(
            conversation_id=conversation_id,
            user_id=user_id,
            patient_id=patient_id,
            summary=summary,
            idempotency_key=key,
        )
        session.add(notification)
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent identical call won the insert; use its row.
            await session.rollback()
            notification = (await session.execute(query)).scalar_one()
    if notification.status == DELIVERED:
        return NotificationOutcome(notification.id, DELIVERED)

    # Staff need to identify and reach the patient, so this goes to the staff channel only.
    payload = {
        "notification_id": str(notification.id),
        "type": "dental_emergency",
        "summary": summary,
        "patient": {
            "patient_id": str(patient.id),
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "phone_number": patient.phone_number,
        },
        "conversation_id": str(conversation_id),
        "created_at": notification.created_at.isoformat(),
    }
    notification.attempts += 1
    try:
        await sender.post_to_staff_channel(payload, idempotency_key=key)
    except sender.StaffChannelError as exc:
        notification.status = FAILED
        notification.last_error = str(exc)[:300]
    else:
        notification.status = DELIVERED
        notification.last_error = None
        notification.delivered_at = datetime.now(UTC)
    await session.commit()
    return NotificationOutcome(notification.id, notification.status)
