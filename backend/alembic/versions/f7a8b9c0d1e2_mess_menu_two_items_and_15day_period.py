"""Mess: 2-item menu (Full Meal 25, Chai 25) and 15-day period budget note.

Revision ID: f7a8b9c0d1e2
Revises: b2c3d4e5f6a7
Create Date: 2026-08-04

Data migration only: replace the default 10-item menu with exactly two
active items (Full Meal @ 25, Chai @ 25) and deactivate everything else.
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def _menu_exists(bind) -> bool:
    return sa.inspect(bind).has_table("menu_items")


def upgrade() -> None:
    bind = op.get_bind()
    if not _menu_exists(bind):
        return
    # Deactivate every menu item; keep only the two flat 25s active below.
    op.execute(sa.text("UPDATE menu_items SET is_active = false"))

    def upsert(name: str, category: str, icon: str, price: float) -> None:
        row = bind.execute(
            sa.text("SELECT id FROM menu_items WHERE name = :n").bindparams(n=name)
        ).first()
        if row:
            op.execute(
                sa.text(
                    "UPDATE menu_items SET category = :c, price = :p, icon = :i, is_active = true "
                    "WHERE id = :id"
                ).bindparams(c=category, p=price, i=icon, id=row[0])
            )
        else:
            op.execute(
                sa.text(
                    "INSERT INTO menu_items (name, category, price, icon, is_active) "
                    "VALUES (:n, :c, :p, :i, true)"
                ).bindparams(n=name, c=category, p=price, i=icon)
            )

    upsert("Full Meal", "meal", "🍛", 25.0)
    upsert("Chai", "drink", "🍵", 25.0)


def downgrade() -> None:
    # Restore the original default menu.
    bind = op.get_bind()
    if not _menu_exists(bind):
        return
    op.execute(sa.text("UPDATE menu_items SET is_active = false"))
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

    def restore(name: str, category: str, icon: str, price: float) -> None:
        row = bind.execute(
            sa.text("SELECT id FROM menu_items WHERE name = :n").bindparams(n=name)
        ).first()
        if row:
            op.execute(
                sa.text(
                    "UPDATE menu_items SET category = :c, price = :p, icon = :i, is_active = true "
                    "WHERE id = :id"
                ).bindparams(c=category, p=price, i=icon, id=row[0])
            )
        else:
            op.execute(
                sa.text(
                    "INSERT INTO menu_items (name, category, price, icon, is_active) "
                    "VALUES (:n, :c, :p, :i, true)"
                ).bindparams(n=name, c=category, p=price, i=icon)
            )

    for name, cat, price, icon in defaults:
        restore(name, cat, icon, price)
