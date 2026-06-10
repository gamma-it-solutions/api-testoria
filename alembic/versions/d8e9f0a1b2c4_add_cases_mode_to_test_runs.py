"""add cases_mode to test_runs

Revision ID: d8e9f0a1b2c4
Revises: c5d7e9f1a2b3
Create Date: 2026-04-20 11:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c4"
down_revision: Union[str, None] = "c5d7e9f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "cases_mode",
            sa.String(length=20),
            nullable=False,
            server_default="auto",
        ),
    )
    op.execute(
        "UPDATE test_runs SET cases_mode = 'explicit' "
        "WHERE id IN (SELECT DISTINCT test_run_id FROM test_run_test_cases)"
    )
    op.create_check_constraint(
        "ck_test_runs_cases_mode",
        "test_runs",
        "cases_mode IN ('auto', 'explicit')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_test_runs_cases_mode", "test_runs", type_="check")
    op.drop_column("test_runs", "cases_mode")
