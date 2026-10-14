"""create slot_offer

Revision ID: 0007_slot_offer
Revises: 0006_cancellation_proposal
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_slot_offer"
down_revision: str | None = "0006_cancellation_proposal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slot_offer",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("slot_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            name="slot_offer_conversation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="slot_offer_pkey"),
    )
    op.create_index(
        "slot_offer_conversation_code_key", "slot_offer", ["conversation_id", "code"], unique=True
    )


def downgrade() -> None:
    op.drop_index("slot_offer_conversation_code_key", table_name="slot_offer")
    op.drop_table("slot_offer")
