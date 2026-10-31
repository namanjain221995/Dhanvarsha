"""spotlight screen

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-01 04:00:00.000000

programmes.spotlight holds the spotlight screen (/spotlight) as a JSON object: the people the
organiser reveals one at a time on the big screen (a name, an optional line under it and a
picture), and which of them are revealed now. The empty object every existing row gets means
an empty spotlight with the built-in title.

Additive only: the previous release never reads the column, and table-level grants already
cover it. The people can be entered again, so the downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "programmes",
        sa.Column("spotlight", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_check_constraint(
        "ck_programmes_spotlight_object", "programmes", "jsonb_typeof(spotlight) = 'object'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_programmes_spotlight_object", "programmes", type_="check")
    op.drop_column("programmes", "spotlight")
