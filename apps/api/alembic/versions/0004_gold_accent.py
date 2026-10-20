"""gold (Dhanvarsha) accent

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "accent IN ('violet', 'plum', 'indigo', 'teal', 'rose', 'amber')"
_NEW = "accent IN ('violet', 'plum', 'indigo', 'teal', 'rose', 'amber', 'gold')"


def upgrade() -> None:
    op.drop_constraint("ck_programmes_accent", "programmes", type_="check")
    op.create_check_constraint("ck_programmes_accent", "programmes", _NEW)


def downgrade() -> None:
    op.execute("UPDATE programmes SET accent = 'amber' WHERE accent = 'gold'")
    op.drop_constraint("ck_programmes_accent", "programmes", type_="check")
    op.create_check_constraint("ck_programmes_accent", "programmes", _OLD)
