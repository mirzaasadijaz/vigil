"""add extra_photos to employees

Revision ID: a1b2c3d4e5f6
Revises: d4e5f6a0b1c2
Create Date: 2026-08-04 12:28:50.732364

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The live DB is bootstrapped via create_tables() (Base.metadata.create_all),
    # so the column may already exist — skip in that case to stay compatible either way.
    bind = op.get_bind()
    columns = [c["name"] for c in sa.inspect(bind).get_columns("employees")]
    if "extra_photos" in columns:
        return
    op.add_column("employees", sa.Column("extra_photos", sa.ARRAY(sa.String(500)), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("employees", "extra_photos")
