"""merge heads

Revision ID: 51fccc1eeae9
Revises: a1b2c3d4e5f6, 9488212d6fbb
Create Date: 2026-08-04 12:42:03.248454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51fccc1eeae9'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', '9488212d6fbb')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
