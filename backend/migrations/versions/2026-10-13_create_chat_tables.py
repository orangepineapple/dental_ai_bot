"""create chat tables

Revision ID: 0002_chat
Revises: 0001_patient_auth
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_chat"
down_revision: str | None = "0001_patient_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth_user.id"], name="chat_conversation_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="chat_conversation_pkey"),
    )
    op.create_index("chat_conversation_user_id_idx", "chat_conversation", ["user_id"])
    op.create_table(
        "chat_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            name="chat_message_conversation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="chat_message_pkey"),
    )
    op.create_index("chat_message_conversation_id_idx", "chat_message", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("chat_message_conversation_id_idx", table_name="chat_message")
    op.drop_table("chat_message")
    op.drop_index("chat_conversation_user_id_idx", table_name="chat_conversation")
    op.drop_table("chat_conversation")
