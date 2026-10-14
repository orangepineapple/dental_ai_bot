"""create booking_proposal table

Revision ID: 0004_booking_proposal
Revises: 0003_appointments
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_booking_proposal"
down_revision: str | None = "0003_appointments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("target_patient_id", sa.Uuid(), nullable=False),
        sa.Column("slot_token", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_code", sa.String(length=64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'declined', 'expired', 'failed', 'superseded')",
            name="booking_proposal_status_valid_check",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            name="booking_proposal_conversation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name="booking_proposal_user_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_patient_id"],
            ["patient.id"],
            name="booking_proposal_target_patient_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider.id"],
            name="booking_proposal_provider_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_type_code"],
            ["appointment_type.code"],
            name="booking_proposal_appointment_type_code_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointment.id"],
            name="booking_proposal_appointment_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="booking_proposal_pkey"),
    )
    op.create_index(
        "booking_proposal_conversation_pending_key",
        "booking_proposal",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("booking_proposal_conversation_pending_key", table_name="booking_proposal")
    op.drop_table("booking_proposal")
