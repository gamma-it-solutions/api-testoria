"""add display_order to test_suites

Revision ID: e9f0a1b2c3d5
Revises: d8e9f0a1b2c4
Create Date: 2026-04-20 12:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d5"
down_revision: Union[str, None] = "d8e9f0a1b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_suites",
        sa.Column("display_order", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_suites", "display_order")
