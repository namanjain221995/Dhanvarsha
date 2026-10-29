"""opening showcase page

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30 14:00:00.000000

programmes.opening holds the organiser's opening page (title, year, tagline, button label,
logo and cards) as a JSON object. The empty object every existing row gets means the
built-in page, made from the programme's name and description.

Additive only: the previous release never reads the column, and table-level grants
already cover it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "programmes",
        sa.Column("opening", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_check_constraint(
        "ck_programmes_opening_object", "programmes", "jsonb_typeof(opening) = 'object'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_programmes_opening_object", "programmes", type_="check")
    op.drop_column("programmes", "opening")
