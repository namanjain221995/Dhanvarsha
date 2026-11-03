"""who sent a gallery file from the organisers' console

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-04 08:00:00.000000

gallery_media.uploader_admin_id is the organiser who sent a file from the console, where a guest's file
has uploader_person_id instead. It goes back to NULL if that organiser's account is removed, and the file
stays.

Additive: the previous release ignores it. The downgrade forgets who sent those files, and keeps the files.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gallery_media", sa.Column("uploader_admin_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "gallery_media_uploader_admin_id_fkey",
        "gallery_media",
        "admin_users",
        ["uploader_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_column("gallery_media", "uploader_admin_id")
