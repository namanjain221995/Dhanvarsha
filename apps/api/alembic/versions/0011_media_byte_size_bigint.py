"""64-bit asset sizes

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-30 23:30:00.000000

Opening-page videos no longer have a size limit (only the free disk space counts), and
assets.byte_size was a 32-bit integer that stops just under 2 GiB. It becomes BIGINT.

The column only gets wider, so the previous release reads it unchanged. The assets table
is small, so the rewrite is quick. The downgrade fails while an asset of 2 GiB or more is
recorded; remove those first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "assets", "byte_size", type_=sa.BigInteger(), existing_type=sa.Integer(), existing_nullable=False
    )


def downgrade() -> None:
    op.alter_column(
        "assets", "byte_size", type_=sa.Integer(), existing_type=sa.BigInteger(), existing_nullable=False
    )
