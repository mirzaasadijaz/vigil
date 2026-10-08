"""false-detection layer: alert severity + stored configuration

Adds two nullable columns. Nothing is dropped and nothing is backfilled, so this is safe
to apply to a live database and safe to roll back.

  alert_logs.severity            classification written by the false-detection layer.
                                 NULL for every row written before the layer existed,
                                 and for every row written while it is disabled - read
                                 paths treat NULL as "unclassified", never as a level.
                                 Indexed because a security product has to answer
                                 "today's critical alerts" without a full scan.

  system_settings.false_detection  JSON blob of the configuration, stored the same way
                                 as `locations` and `departments` already are. No new
                                 table, so the "modules do not add tables" rule holds.

NOTE: this repository's migration tree has TWO heads (9488212d6fbb and d4e5f6a0b1c2).
This revision is written against d4e5f6a0b1c2.

DO NOT apply this with `alembic upgrade heads`. Tested against a reproduction of a real
deployment, that fails with `column "module" of relation "alert_logs" already exists`:
`heads` also replays the OTHER branch, whose columns `create_tables()` has already
created via Base.metadata.create_all. Upgrade THIS branch by name instead:

    alembic upgrade a1b2c3d4e5f7

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a0b1c2
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "d4e5f6a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_logs", sa.Column("severity", sa.String(16), nullable=True))
    op.create_index("ix_alert_logs_severity", "alert_logs", ["severity"])
    op.add_column("system_settings",
                  sa.Column("false_detection", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_settings", "false_detection")
    op.drop_index("ix_alert_logs_severity", table_name="alert_logs")
    op.drop_column("alert_logs", "severity")
