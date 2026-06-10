"""rename test_runs.status in_progress to active

Revision ID: a4f9c1d27e53
Revises: e9f0a1b2c3d5
Create Date: 2026-04-22 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f9c1d27e53"
down_revision: Union[str, None] = "e9f0a1b2c3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE test_runs SET status = 'active' WHERE status = 'in_progress'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE test_runs SET status = 'in_progress' WHERE status = 'active'"
        )
    )
