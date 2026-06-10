"""Add soft delete (deleted_at) to core entities and tighten FK ondelete

Revision ID: a1b2c3d4e5f6
Revises: b5c6d7e8f9a0
Create Date: 2026-04-13 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOFT_DELETE_TABLES = [
    "projects",
    "test_suites",
    "test_cases",
    "test_runs",
    "test_results",
    "milestones",
    "users",
]


def upgrade() -> None:
    for table in SOFT_DELETE_TABLES:
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            op.f(f"ix_{table}_deleted_at"), table, ["deleted_at"], unique=False
        )

    # Tighten FK ondelete to prevent hard cascade bypassing soft-delete.
    # Postgres auto-named the original FKs as "<table>_<col>_fkey".

    op.drop_constraint("test_suites_project_id_fkey", "test_suites", type_="foreignkey")
    op.create_foreign_key(
        "test_suites_project_id_fkey",
        "test_suites",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "test_suites_parent_suite_id_fkey", "test_suites", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_suites_parent_suite_id_fkey",
        "test_suites",
        "test_suites",
        ["parent_suite_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("test_cases_suite_id_fkey", "test_cases", type_="foreignkey")
    op.create_foreign_key(
        "test_cases_suite_id_fkey",
        "test_cases",
        "test_suites",
        ["suite_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint("test_runs_project_id_fkey", "test_runs", type_="foreignkey")
    op.create_foreign_key(
        "test_runs_project_id_fkey",
        "test_runs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "test_results_test_run_id_fkey", "test_results", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_results_test_run_id_fkey",
        "test_results",
        "test_runs",
        ["test_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "test_results_test_case_id_fkey", "test_results", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_results_test_case_id_fkey",
        "test_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Restore CASCADE FKs
    op.drop_constraint(
        "test_results_test_case_id_fkey", "test_results", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_results_test_case_id_fkey",
        "test_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "test_results_test_run_id_fkey", "test_results", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_results_test_run_id_fkey",
        "test_results",
        "test_runs",
        ["test_run_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("test_runs_project_id_fkey", "test_runs", type_="foreignkey")
    op.create_foreign_key(
        "test_runs_project_id_fkey",
        "test_runs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("test_cases_suite_id_fkey", "test_cases", type_="foreignkey")
    op.create_foreign_key(
        "test_cases_suite_id_fkey",
        "test_cases",
        "test_suites",
        ["suite_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "test_suites_parent_suite_id_fkey", "test_suites", type_="foreignkey"
    )
    op.create_foreign_key(
        "test_suites_parent_suite_id_fkey",
        "test_suites",
        "test_suites",
        ["parent_suite_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("test_suites_project_id_fkey", "test_suites", type_="foreignkey")
    op.create_foreign_key(
        "test_suites_project_id_fkey",
        "test_suites",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for table in SOFT_DELETE_TABLES:
        op.drop_index(op.f(f"ix_{table}_deleted_at"), table_name=table)
        op.drop_column(table, "deleted_at")
