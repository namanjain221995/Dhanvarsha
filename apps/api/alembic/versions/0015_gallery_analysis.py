"""what the gallery's analysis saw

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03 04:00:00.000000

gallery_faces.analysis holds where a face is in its picture, how sure the detector was, and whom
it was compared with. gallery_media.analysis holds how the file was read, the video frames kept for
the organisers, and the vision model's description once an organiser has asked for it.

Additive only: the previous release never reads the columns. The gallery worker fills them in for
files read before this revision. The frame files stay on the media volume after a downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("gallery_media", "gallery_faces"):
        op.add_column(
            table,
            sa.Column("analysis", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        )


def downgrade() -> None:
    for table in ("gallery_media", "gallery_faces"):
        op.drop_column(table, "analysis")
