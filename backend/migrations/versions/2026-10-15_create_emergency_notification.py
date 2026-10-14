"""create emergency_notification table

Revision ID: 0005_emergency_notification
Revises: 0004_booking_proposal
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_emergency_notification"
down_revision: str | None = "0004_booking_proposal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emergency_notification",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="emergency_notification_status_valid_check",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversation.id"], name="emergency_notification_conversation_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_user.id"], name="emergency_notification_user_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"], name="emergency_notification_patient_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="emergency_notification_pkey"),
        sa.UniqueConstraint("idempotency_key", name="emergency_notification_idempotency_key_key"),
    )
    op.create_index("emergency_notification_conversation_id_idx", "emergency_notification", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("emergency_notification_conversation_id_idx", table_name="emergency_notification")
    op.drop_table("emergency_notification")
