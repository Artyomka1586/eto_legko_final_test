"""stream_offers.ever_published: отличает локальные непушнутые добавления от архивных офферов

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stream_offers",
        sa.Column("ever_published", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Существующие строки появились до флага: считаем их известными Keitaro.
    op.execute("UPDATE stream_offers SET ever_published = true")


def downgrade() -> None:
    op.drop_column("stream_offers", "ever_published")
