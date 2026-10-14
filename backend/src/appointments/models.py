import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Time,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, TimestampMixin

BOOKED = "booked"
CANCELLED = "cancelled"
COMPLETED = "completed"
NO_SHOW = "no_show"
APPOINTMENT_STATUSES = (BOOKED, CANCELLED, COMPLETED, NO_SHOW)


class AppointmentType(TimestampMixin, Base):
    """A service the clinic offers. Durations and rules come from here, never from the model."""

    __tablename__ = "appointment_type"
    __table_args__ = (CheckConstraint("duration_minutes > 0", name="duration_positive"),)

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    # Eligibility: minimum patient age in years at the appointment date.
    min_age_years: Mapped[int | None] = mapped_column(Integer)
    # Escalation: the assistant must not book this type; staff do.
    requires_staff_booking: Mapped[bool] = mapped_column(default=False, server_default="false")
    escalation_note: Mapped[str | None] = mapped_column(Text)


class Provider(TimestampMixin, Base):
    __tablename__ = "provider"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class ProviderAppointmentType(Base):
    """Which appointment types a provider performs."""

    __tablename__ = "provider_appointment_type"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider.id", ondelete="CASCADE"), primary_key=True
    )
    appointment_type_code: Mapped[str] = mapped_column(
        ForeignKey("appointment_type.code", ondelete="CASCADE"), primary_key=True
    )


class ProviderWorkingHours(Base):
    """Weekly recurring availability in CLINIC LOCAL time. weekday: Monday=0 .. Sunday=6."""

    __tablename__ = "provider_working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class ClinicClosure(Base):
    """A whole day the clinic is closed (holiday etc.), in clinic local date."""

    __tablename__ = "clinic_closure"

    closure_date: Mapped[date] = mapped_column(primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(255))


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointment"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint(
            "status IN ('booked', 'cancelled', 'completed', 'no_show')", name="status_valid"
        ),
        # A provider can never hold two booked appointments that overlap in time.
        ExcludeConstraint(
            ("provider_id", "="),
            (text("tstzrange(starts_at, ends_at)"), "&&"),
            name="appointment_provider_no_overlap_excl",
            using="gist",
            where=text("status = 'booked'"),
        ),
        # Retries of the same booking collapse to one live row. Cancelled rows keep their
        # key but drop out of the index, so the same slot can be booked again later.
        Index(
            "appointment_idempotency_key_booked_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status = 'booked'"),
        ),
        Index("appointment_patient_id_starts_at_idx", "patient_id", "starts_at"),
        Index("appointment_provider_id_starts_at_idx", "provider_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id", ondelete="RESTRICT"))
    # Who actually made the booking (a guardian may book for a dependent).
    booked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL")
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provider.id", ondelete="RESTRICT"))
    appointment_type_code: Mapped[str] = mapped_column(
        ForeignKey("appointment_type.code", ondelete="RESTRICT")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=BOOKED, server_default=BOOKED)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(300))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(300))


class AppointmentAudit(Base):
    """Append-only history of changes to an appointment."""

    __tablename__ = "appointment_audit"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL")
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


PROPOSAL_PENDING = "pending"


class BookingProposal(Base):
    """A booking the model suggested, awaiting the patient's click on the confirmation card.

    Only an authenticated HTTP request from the patient can turn this into an
    appointment; the model has no tool that books.
    """

    __tablename__ = "booking_proposal"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'declined', 'expired', 'failed', 'superseded')",
            name="status_valid",
        ),
        # At most one live proposal per conversation.
        Index(
            "booking_proposal_conversation_pending_key",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_conversation.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth_user.id", ondelete="CASCADE"))
    target_patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE")
    )
    slot_token: Mapped[str] = mapped_column(Text)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provider.id", ondelete="CASCADE"))
    appointment_type_code: Mapped[str] = mapped_column(
        ForeignKey("appointment_type.code", ondelete="CASCADE")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(16), default=PROPOSAL_PENDING, server_default=PROPOSAL_PENDING
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL")
    )
    result_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
