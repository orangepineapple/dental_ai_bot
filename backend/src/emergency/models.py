import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, TimestampMixin

PENDING = "pending"
DELIVERED = "delivered"
FAILED = "failed"
NOTIFICATION_STATUSES = (PENDING, DELIVERED, FAILED)


class EmergencyNotification(TimestampMixin, Base):
    """A report to clinic staff and whether it was actually delivered.

    `delivered` is set only after the staff channel answered with a 2xx. A row existing
    means "we tried", nothing more.
    """

    __tablename__ = "emergency_notification"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'delivered', 'failed')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_conversation.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth_user.id", ondelete="CASCADE"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    # sha256(conversation, summary): a retried tool call collapses onto the same row.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, server_default=PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
