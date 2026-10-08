"""face_embeddings — multi-embedding face gallery (auto-enrolled vectors)

Revision ID: b1f3a7c9d2e4
Revises: 51d2c9f065e1
Create Date: 2026-06-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1f3a7c9d2e4'
down_revision: Union[str, Sequence[str], None] = '51d2c9f065e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The live DB is bootstrapped via create_tables() (Base.metadata.create_all), so the
    # table may already exist — skip in that case to stay compatible either way.
    bind = op.get_bind()
    if sa.inspect(bind).has_table('face_embeddings'):
        return
    op.create_table('face_embeddings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('vec', sa.LargeBinary(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('quality', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_face_embeddings_id'), 'face_embeddings', ['id'], unique=False)
    op.create_index(op.f('ix_face_embeddings_employee_id'), 'face_embeddings', ['employee_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_face_embeddings_employee_id'), table_name='face_embeddings')
    op.drop_index(op.f('ix_face_embeddings_id'), table_name='face_embeddings')
    op.drop_table('face_embeddings')
