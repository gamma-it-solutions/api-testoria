"""Rename role slugs and add role constraints

Revision ID: f7a3b2c1d9e0
Revises: 8c23843e1a84
Create Date: 2026-03-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a3b2c1d9e0"
down_revision: str | None = "8c23843e1a84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename old role slugs before adding the CHECK constraint
    op.execute("UPDATE users SET role = 'lead' WHERE role = 'project_manager'")
    op.execute("UPDATE users SET role = 'read_only' WHERE role = 'viewer'")

    # Change server default from 'tester' to 'lead'
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=50),
        server_default="lead",
        existing_nullable=False,
    )

    # Add CHECK constraint to enforce valid role values
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('no_access', 'read_only', 'tester', 'lead', 'admin')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")

    # Restore server default
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=50),
        server_default="tester",
        existing_nullable=False,
    )

    # Reverse role slug renames
    op.execute("UPDATE users SET role = 'project_manager' WHERE role = 'lead'")
    op.execute("UPDATE users SET role = 'viewer' WHERE role = 'read_only'")
