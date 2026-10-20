"""team members on nominees

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nominees",
        sa.Column("members", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.create_check_constraint("ck_nominees_members_array", "nominees", "jsonb_typeof(members) = 'array'")


def downgrade() -> None:
    op.drop_constraint("ck_nominees_members_array", "nominees", type_="check")
    op.drop_column("nominees", "members")
