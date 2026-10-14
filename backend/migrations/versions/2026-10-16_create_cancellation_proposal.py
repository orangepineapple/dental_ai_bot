"""create cancellation_proposal table

Revision ID: 0006_cancellation_proposal
Revises: 0005_emergency_notification
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_cancellation_proposal"
down_revision: str | None = "0005_emergency_notification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cancellation_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'declined', 'expired', 'failed', 'superseded')",
            name="cancellation_proposal_status_valid_check",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            name="cancellation_proposal_conversation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_user.id"],
            name="cancellation_proposal_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointment.id"],
            name="cancellation_proposal_appointment_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="cancellation_proposal_pkey"),
    )
    op.create_index(
        "cancellation_proposal_conversation_pending_key",
        "cancellation_proposal",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "cancellation_proposal_conversation_pending_key", table_name="cancellation_proposal"
    )
    op.drop_table("cancellation_proposal")
