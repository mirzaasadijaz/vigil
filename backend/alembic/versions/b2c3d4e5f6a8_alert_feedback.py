"""operator feedback on alerts (thumbs up / thumbs down)

Adds four nullable columns to `alert_logs`. Nothing is dropped, nothing is backfilled,
so this is safe on a live database and safe to roll back.

  alert_logs.feedback       'up' | 'down' | NULL   NULL = nobody has judged this alert
  alert_logs.feedback_by    user id who judged it
  alert_logs.feedback_at    when
  alert_logs.feedback_note  optional free text: WHY it was wrong

WHY COLUMNS AND NOT A TABLE
    The repository rule is that modules emit events into the unified `alert_logs` store
    rather than adding tables. Feedback is not an event - it is an annotation ON an
    event, exactly one per alert, so it belongs on the row it judges. A join table would
    buy nothing and cost every read path a join.

WHY `feedback` IS INDEXED
    The whole point of collecting this is the export: "give me every alert an operator
    marked wrong, with its diagnostic payload". That is a filtered scan over a table
    which grows without bound, and it will be run repeatedly while tuning. Without the
    index it degrades as the deployment ages, which is precisely when the data becomes
    useful.

WHY NULL IS THE RIGHT DEFAULT
    NULL means "not judged", which is different from "judged as fine". Every read path
    must keep those apart: an untouched alert is not evidence of anything, and counting
    it as a thumbs-up would quietly inflate the accuracy figure this feature exists to
    measure.

NOTE: this repository's migration tree has TWO heads (9488212d6fbb and a1b2c3d4e5f7).
This revision continues the a1b2c3d4e5f7 branch.

DO NOT use `alembic upgrade heads` - it also replays the other branch, which collides
with columns `create_tables()` has already created. Upgrade this branch by name:

    alembic upgrade b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a8"       
down_revision = "b2c3d4e5f6a7" 
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    """`create_tables()` runs Base.metadata.create_all on startup IN ADDITION to
    Alembic, so a fresh database can already have these columns before this migration
    runs. Guarding makes the migration idempotent instead of exploding on a database
    that is ahead of `alembic_version` - a documented gotcha in this repo."""
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column("alert_logs", "feedback"):
        op.add_column("alert_logs", sa.Column("feedback", sa.String(8), nullable=True))
        op.create_index("ix_alert_logs_feedback", "alert_logs", ["feedback"])
    if not _has_column("alert_logs", "feedback_by"):
        op.add_column("alert_logs", sa.Column("feedback_by", sa.Integer(), nullable=True))
    if not _has_column("alert_logs", "feedback_at"):
        op.add_column("alert_logs",
                      sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("alert_logs", "feedback_note"):
        op.add_column("alert_logs", sa.Column("feedback_note", sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("feedback_note", "feedback_at", "feedback_by"):
        if _has_column("alert_logs", col):
            op.drop_column("alert_logs", col)
    if _has_column("alert_logs", "feedback"):
        op.drop_index("ix_alert_logs_feedback", table_name="alert_logs")
        op.drop_column("alert_logs", "feedback")
