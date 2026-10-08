"""Fix smart_ prefix in camera module_config

Some cameras have modules stored as "smart_occupancy" / "smart_attendance"
instead of "occupancy" / "attendance". This migration corrects those values.

Revision ID: d4e5f6a0b1c2
Revises: c7e8f9a0b1c2
Create Date: 2026-07-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for old, new in [("smart_occupancy", "occupancy"), ("smart_attendance", "attendance")]:
        op.execute(
            sa.text(
                "UPDATE camera_devices "
                "SET module_config = REPLACE(module_config, :old, :new) "
                "WHERE module_config LIKE :pattern"
            ).bindparams(old=old, new=new, pattern=f"%{old}%")
        )


def downgrade() -> None:
    # Can't reliably reverse (no way to know which were originally "smart_")
    pass
