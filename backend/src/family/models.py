import uuid

from sqlalchemy import CheckConstraint, ForeignKey, true
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, TimestampMixin


class PatientGuardian(TimestampMixin, Base):
    """A guardian who may manage a dependent's care. Created by clinic staff, never by the model."""

    __tablename__ = "patient_guardian"
    __table_args__ = (
        CheckConstraint("guardian_patient_id <> dependent_patient_id", name="not_self"),
    )

    guardian_patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), primary_key=True
    )
    dependent_patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    can_book: Mapped[bool] = mapped_column(default=True, server_default=true())
