"""create appointment and family guardian tables

Revision ID: 0003_appointments
Revises: 0002_chat
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_appointments"
down_revision: str | None = "0002_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # uuid equality inside a gist exclusion constraint.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "patient_guardian",
        sa.Column("guardian_patient_id", sa.Uuid(), nullable=False),
        sa.Column("dependent_patient_id", sa.Uuid(), nullable=False),
        sa.Column("can_book", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "guardian_patient_id <> dependent_patient_id", name="patient_guardian_not_self_check"
        ),
        sa.ForeignKeyConstraint(
            ["guardian_patient_id"],
            ["patient.id"],
            name="patient_guardian_guardian_patient_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dependent_patient_id"],
            ["patient.id"],
            name="patient_guardian_dependent_patient_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "guardian_patient_id", "dependent_patient_id", name="patient_guardian_pkey"
        ),
    )
    op.create_index(
        "patient_guardian_dependent_patient_id_idx", "patient_guardian", ["dependent_patient_id"]
    )

    op.create_table(
        "appointment_type",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("min_age_years", sa.Integer(), nullable=True),
        sa.Column("requires_staff_booking", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("escalation_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("duration_minutes > 0", name="appointment_type_duration_positive_check"),
        sa.PrimaryKeyConstraint("code", name="appointment_type_pkey"),
    )

    op.create_table(
        "provider",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="provider_pkey"),
    )

    op.create_table(
        "provider_appointment_type",
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider.id"],
            name="provider_appointment_type_provider_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_type_code"],
            ["appointment_type.code"],
            name="provider_appointment_type_appointment_type_code_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "provider_id", "appointment_type_code", name="provider_appointment_type_pkey"
        ),
    )

    op.create_table(
        "provider_working_hours",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="provider_working_hours_weekday_range_check"),
        sa.CheckConstraint("end_time > start_time", name="provider_working_hours_end_after_start_check"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider.id"],
            name="provider_working_hours_provider_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="provider_working_hours_pkey"),
    )
    op.create_index(
        "provider_working_hours_provider_id_idx", "provider_working_hours", ["provider_id"]
    )

    op.create_table(
        "clinic_closure",
        sa.Column("closure_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("closure_date", name="clinic_closure_pkey"),
    )

    op.create_table(
        "appointment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("booked_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_code", sa.String(length=64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="booked", nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=300), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("ends_at > starts_at", name="appointment_ends_after_starts_check"),
        sa.CheckConstraint(
            "status IN ('booked', 'cancelled', 'completed', 'no_show')",
            name="appointment_status_valid_check",
        ),
        postgresql.ExcludeConstraint(
            ("provider_id", "="),
            (sa.text("tstzrange(starts_at, ends_at)"), "&&"),
            name="appointment_provider_no_overlap_excl",
            using="gist",
            where=sa.text("status = 'booked'"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patient.id"], name="appointment_patient_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["booked_by_user_id"],
            ["auth_user.id"],
            name="appointment_booked_by_user_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["provider.id"], name="appointment_provider_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["appointment_type_code"],
            ["appointment_type.code"],
            name="appointment_appointment_type_code_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="appointment_pkey"),
    )
    op.create_index(
        "appointment_idempotency_key_booked_key",
        "appointment",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "appointment_patient_id_starts_at_idx", "appointment", ["patient_id", "starts_at"]
    )
    op.create_index(
        "appointment_provider_id_starts_at_idx", "appointment", ["provider_id", "starts_at"]
    )

    op.create_table(
        "appointment_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointment.id"],
            name="appointment_audit_appointment_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["auth_user.id"],
            name="appointment_audit_actor_user_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="appointment_audit_pkey"),
    )
    op.create_index("appointment_audit_appointment_id_idx", "appointment_audit", ["appointment_id"])


def downgrade() -> None:
    op.drop_table("appointment_audit")
    op.drop_table("appointment")
    op.drop_table("clinic_closure")
    op.drop_table("provider_working_hours")
    op.drop_table("provider_appointment_type")
    op.drop_table("provider")
    op.drop_table("appointment_type")
    op.drop_table("patient_guardian")
