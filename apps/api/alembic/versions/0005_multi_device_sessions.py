"""participants may be signed in on several devices at once

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21 22:00:00.000000

Drops the one-live-session-per-participant rule (the partial unique index) and keeps a plain
partial index for finding a participant's live sessions. Votes stay one per ballot per
participant, enforced by the votes table, whichever device sends them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_sessions_one_active_per_participant", table_name="participant_sessions")
    op.create_index(
        "ix_sessions_active_by_participant",
        "participant_sessions",
        ["participant_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Back to one session: keep each participant's newest live session, sign the others out.
    op.execute(
        """
        UPDATE participant_sessions s SET revoked_at = now(), revoke_reason = 'admin_reset'
        WHERE s.revoked_at IS NULL AND EXISTS (
            SELECT 1 FROM participant_sessions newer
            WHERE newer.participant_id = s.participant_id AND newer.revoked_at IS NULL
              AND (newer.created_at, newer.id) > (s.created_at, s.id)
        )
        """
    )
    op.drop_index("ix_sessions_active_by_participant", table_name="participant_sessions")
    op.create_index(
        "uq_sessions_one_active_per_participant",
        "participant_sessions",
        ["participant_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
