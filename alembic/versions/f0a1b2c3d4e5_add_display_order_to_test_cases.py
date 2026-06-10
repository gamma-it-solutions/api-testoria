"""add display_order to test_cases

Revision ID: f0a1b2c3d4e5
Revises: c7f1a2b3d4e8
Create Date: 2026-05-11 18:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "c7f1a2b3d4e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column("display_order", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "display_order")
