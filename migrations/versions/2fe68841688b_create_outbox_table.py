"""Create outbox table.

Revision ID: 2fe68841688b
Revises: 3d14b33d91d3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "2fe68841688b"
down_revision: str | Sequence[str] | None = "3d14b33d91d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent')",
            name="ck_outbox_status",
        ),
    )

    op.create_index(
        "ix_outbox_pending_created_at_id",
        "outbox",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_pending_created_at_id",
        table_name="outbox",
    )
    op.drop_table("outbox")
