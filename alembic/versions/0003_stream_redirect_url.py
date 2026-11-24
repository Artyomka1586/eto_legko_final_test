"""streams.redirect_url: store redirect target of no-offer streams for display

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("streams", sa.Column("redirect_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("streams", "redirect_url")
