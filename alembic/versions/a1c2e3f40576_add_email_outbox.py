"""add email_outbox table

Revision ID: a1c2e3f40576
Revises: f0a1b2c3d4e5
Create Date: 2026-06-03 09:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c2e3f40576"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("template", sa.String(length=100), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_outbox_id"), "email_outbox", ["id"])
    op.create_index(op.f("ix_email_outbox_status"), "email_outbox", ["status"])
    op.create_index(
        op.f("ix_email_outbox_next_attempt_at"), "email_outbox", ["next_attempt_at"]
    )
    # Hot path for the drain worker: claim ready-to-send rows in creation order.
    op.create_index(
        "ix_email_outbox_status_next_attempt_at",
        "email_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_next_attempt_at", table_name="email_outbox")
    op.drop_index(op.f("ix_email_outbox_next_attempt_at"), table_name="email_outbox")
    op.drop_index(op.f("ix_email_outbox_status"), table_name="email_outbox")
    op.drop_index(op.f("ix_email_outbox_id"), table_name="email_outbox")
    op.drop_table("email_outbox")
