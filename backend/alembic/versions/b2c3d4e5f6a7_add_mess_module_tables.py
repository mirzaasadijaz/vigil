"""add mess module tables

Revision ID: b2c3d4e5f6a7
Revises: 51fccc1eeae9
Create Date: 2026-08-04 14:24:53.243169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '51fccc1eeae9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("menu_items"):
        op.create_table('menu_items',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=True),
            sa.Column('price', sa.Float(), nullable=True),
            sa.Column('icon', sa.String(length=50), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_menu_items_id'), 'menu_items', ['id'], unique=False)
    if not insp.has_table("mess_sessions"):
        op.create_table('mess_sessions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('employee_id', sa.Integer(), nullable=False),
            sa.Column('camera_id', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('total', sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(['camera_id'], ['camera_devices.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_mess_sessions_id'), 'mess_sessions', ['id'], unique=False)
        op.create_index(op.f('ix_mess_sessions_status'), 'mess_sessions', ['status'], unique=False)
        op.create_index(op.f('ix_mess_sessions_started_at'), 'mess_sessions', ['started_at'], unique=False)
    if not insp.has_table("mess_session_items"):
        op.create_table('mess_session_items',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('menu_item_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('price', sa.Float(), nullable=True),
            sa.Column('qty', sa.Integer(), nullable=True),
            sa.Column('amount', sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(['menu_item_id'], ['menu_items.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['session_id'], ['mess_sessions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_mess_session_items_id'), 'mess_session_items', ['id'], unique=False)
        op.create_index(op.f('ix_mess_session_items_session_id'), 'mess_session_items', ['session_id'], unique=False)
    cols = [c["name"] for c in insp.get_columns("system_settings")]
    if "mess_monthly_budget" not in cols:
        op.add_column('system_settings', sa.Column('mess_monthly_budget', sa.Float(), server_default='5000.0', nullable=True))
    # Seed the fixed default menu (only when the menu table is empty)
    if not insp.has_table("menu_items") or not op.get_bind().execute(sa.text("SELECT count(*) FROM menu_items")).scalar():
        defaults = [
            ("Full Meal", "meal", 250.0, "🍛"),
            ("Half Meal", "meal", 150.0, "🍚"),
            ("Chai", "drink", 40.0, "🍵"),
            ("Coffee", "drink", 80.0, "☕"),
            ("Cold Drink", "drink", 60.0, "🥤"),
            ("Water", "drink", 20.0, "💧"),
            ("Paratha", "snack", 50.0, "🫓"),
            ("Samosa", "snack", 30.0, "🥟"),
            ("Pakora", "snack", 30.0, "🥨"),
            ("Salad", "snack", 40.0, "🥗"),
        ]
        for name, cat, price, icon in defaults:
            op.execute(sa.text(
                "INSERT INTO menu_items (name, category, price, icon, is_active) "
                "VALUES (:n, :c, :p, :i, true)"
            ).bindparams(n=name, c=cat, p=price, i=icon))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('mess_session_items')
    op.drop_table('mess_sessions')
    op.drop_table('menu_items')
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("system_settings")]
    if "mess_monthly_budget" in cols:
        op.drop_column('system_settings', 'mess_monthly_budget')
