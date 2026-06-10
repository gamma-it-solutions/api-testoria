"""merge soft_delete and step_results heads

Revision ID: b4ea679b9619
Revises: a1b2c3d4e5f6, b368c6900009
Create Date: 2026-04-18 07:40:23.512005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4ea679b9619'
down_revision: Union[str, None] = ('a1b2c3d4e5f6', 'b368c6900009')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
