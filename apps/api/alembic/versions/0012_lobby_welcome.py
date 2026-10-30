"""lobby welcome text

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 10:00:00.000000

What a phone's lobby shows while no round is open: programmes.welcome_title (at most 120
characters, one line) and programmes.welcome_message (at most 1000 characters, line
breaks allowed). Both start empty, and the lobby then shows its built-in "The next round
opens soon" text. Organisers set them in the console's programme settings.

Additive only: the previous release never reads the columns, and table-level grants
already cover them. The text can be typed again, so the downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "programmes",
        sa.Column("welcome_title", sa.String(120), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "programmes", sa.Column("welcome_message", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.create_check_constraint(
        "ck_programmes_welcome_message_length", "programmes", "char_length(welcome_message) <= 1000"
    )


def downgrade() -> None:
    op.drop_constraint("ck_programmes_welcome_message_length", "programmes", type_="check")
    op.drop_column("programmes", "welcome_message")
    op.drop_column("programmes", "welcome_title")
