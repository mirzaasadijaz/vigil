"""empty message

Revision ID: c455d7b1774c
Revises: a1b2c3d4e5f7, b2c3d4e5f6a8, d8e9f0a1b2c3
Create Date: 2026-10-06 17:55:23.993944

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c455d7b1774c'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f7', 'b2c3d4e5f6a8', 'd8e9f0a1b2c3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
