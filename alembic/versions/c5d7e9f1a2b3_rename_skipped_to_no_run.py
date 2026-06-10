"""rename skipped status to no_run

Revision ID: c5d7e9f1a2b3
Revises: b4ea679b9619
Create Date: 2026-04-20 10:00:00.000000

"""
import json
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5d7e9f1a2b3"
down_revision: Union[str, None] = "b4ea679b9619"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rewrite_step_results(conn: sa.engine.Connection, old: str, new: str) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT id, step_results FROM test_results "
            "WHERE step_results IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        data = row.step_results
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, list):
            continue
        changed = False
        for item in data:
            if isinstance(item, dict) and item.get("status") == old:
                item["status"] = new
                changed = True
        if changed:
            conn.execute(
                sa.text("UPDATE test_results SET step_results = :payload WHERE id = :id"),
                {"payload": json.dumps(data), "id": row.id},
            )


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE test_results SET status = 'no_run' WHERE status = 'skipped'")
    )
    conn.execute(
        sa.text(
            "UPDATE result_history SET status = 'no_run' WHERE status = 'skipped'"
        )
    )
    _rewrite_step_results(conn, "skipped", "no_run")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE test_results SET status = 'skipped' WHERE status = 'no_run'")
    )
    conn.execute(
        sa.text(
            "UPDATE result_history SET status = 'skipped' WHERE status = 'no_run'"
        )
    )
    _rewrite_step_results(conn, "no_run", "skipped")
