"""organiser notes on events

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30 21:00:00.000000

events.organiser_note holds what only organisers see about a round: when to open it and
any special instructions. Up to 2000 characters, line breaks allowed; every existing row
gets the empty note.

Additive only: the previous release never reads the column, and table-level grants
already cover it. The notes are organiser text that `stagevote load-polls` or the editor
can write again, so the downgrade drops them like 0008 drops the opening page.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events", sa.Column("organiser_note", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.create_check_constraint(
        "ck_events_organiser_note_length", "events", "char_length(organiser_note) <= 2000"
    )


def downgrade() -> None:
    op.drop_constraint("ck_events_organiser_note_length", "events", type_="check")
    op.drop_column("events", "organiser_note")
