"""Drop unused global_person_appearance table and mess_monthly_budget column.

Revision ID: d8e9f0a1b2c3
Revises: f7a8b9c0d1e2
Create Date: 2026-08-07

Removes dead schema:
  - global_person_appearance  (no ORM model, never read/written, 0 rows)
  - system_settings.mess_monthly_budget  (budget limit no longer used by mess logic)
"""
from alembic import op
import sqlalchemy as sa


revision = "d8e9f0a1b2c3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("global_person_appearance"):
        op.drop_index("idx_person_id_last_seen", table_name="global_person_appearance")
        op.drop_index("idx_last_seen", table_name="global_person_appearance")
        op.drop_index("idx_person_id", table_name="global_person_appearance")
        op.drop_table("global_person_appearance")
    cols = [c["name"] for c in sa.inspect(bind).get_columns("system_settings")]
    if "mess_monthly_budget" in cols:
        op.drop_column("system_settings", "mess_monthly_budget")


def downgrade() -> None:
    op.create_table(
        "global_person_appearance",
        sa.Column("person_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("face_embedding", sa.LargeBinary(), nullable=True),
        sa.Column("body_embedding", sa.LargeBinary(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("camera_src", sa.String(255), nullable=True),
        sa.Column("sighting_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.String(1024), nullable=True),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.create_index("idx_person_id_last_seen", "global_person_appearance", ["person_id", "last_seen_at"], unique=False)
    op.create_index("idx_last_seen", "global_person_appearance", ["last_seen_at"], unique=False)
    op.create_index("idx_person_id", "global_person_appearance", ["person_id"], unique=False)
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("system_settings")]
    if "mess_monthly_budget" not in cols:
        op.add_column("system_settings", sa.Column("mess_monthly_budget", sa.Float(), server_default="5000.0", nullable=True))
