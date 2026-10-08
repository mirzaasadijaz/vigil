"""kohat poc: global per-capability toggles

Adds ONE nullable column. Nothing is dropped, nothing is backfilled, so this is safe to
apply to a live database and safe to roll back.

  system_settings.module_toggles   JSON object of {module_id: bool}, stored the same way
                                   as `false_detection`, `locations` and `departments`
                                   already are. No new table, so the "modules do not add
                                   tables" rule still holds.

WHY NULL IS THE CORRECT DEFAULT, AND WHY THE READ PATH MATTERS MORE THAN THIS FILE
    NULL means "nobody has configured any toggles", which
    `backend/services/module_toggles.py` resolves to **every capability enabled**. An
    absent key is treated the same way. That is deliberate and it is the safety property
    of the whole feature: adding a new module, or deploying this migration, can never
    silently switch something off.

    So this migration is genuinely inert on its own — behaviour changes only once an
    operator sets a toggle to false in the admin panel.

The Kohat POC capabilities (tripwire, crowd, no_stopping, tamper) need no schema of their
own: they write to `alert_logs` like every other module, tagged by `module` and
`event_type`, per the unified event store rule.

NOTE: this repository's migration tree has TWO heads (9488212d6fbb and d4e5f6a0b1c2).
This revision continues the d4e5f6a0b1c2 branch, on top of the false-detection layer.

DO NOT use `alembic upgrade heads` — it also replays the other branch, which collides
with columns `create_tables()` has already created. Upgrade this branch by name:

    alembic upgrade a1b2c3d4e5f7

Revision ID: a1b2c3d4e5f7
Revises: f1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings",
                  sa.Column("module_toggles", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_settings", "module_toggles")
