"""rename result_attachments.file_path to object_key; add storage_backend

Revision ID: c7f1a2b3d4e8
Revises: a4f9c1d27e53
Create Date: 2026-04-22 17:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f1a2b3d4e8"
down_revision: Union[str, None] = "a4f9c1d27e53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "result_attachments",
        "file_path",
        new_column_name="object_key",
        existing_type=sa.String(length=1024),
        existing_nullable=False,
    )
    op.add_column(
        "result_attachments",
        sa.Column(
            "storage_backend",
            sa.String(length=16),
            nullable=False,
            server_default="local",
        ),
    )
    # Future rows default to 's3'; existing rows stay 'local' until migrated.
    op.alter_column(
        "result_attachments",
        "storage_backend",
        server_default="s3",
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("result_attachments", "storage_backend")
    op.alter_column(
        "result_attachments",
        "object_key",
        new_column_name="file_path",
        existing_type=sa.String(length=1024),
        existing_nullable=False,
    )
