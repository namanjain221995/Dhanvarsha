"""uploads straight from the browser to S3

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-03 08:00:00.000000

gallery_s3_uploads is one multipart upload a browser is sending straight to S3, until it completes.
gallery_media.source_on_s3 says that a queued file is waiting in the bucket, not on the media volume: its
sha256 then starts as a placeholder, because only the worker sees all of its bytes.

Additive: the previous release ignores both. The downgrade refuses while a file waits in the bucket.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gallery_media",
        sa.Column("source_on_s3", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "gallery_s3_uploads",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("s3_upload_id", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gallery_s3_uploads_created", "gallery_s3_uploads", ["created_at"])
    role = op.get_context().opts.get("app_role", "stagevote_app")
    if not role.replace("_", "").isalnum():
        raise ValueError(f"unsafe role name: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON gallery_s3_uploads TO {role};
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    waiting = bind.execute(
        sa.text("SELECT count(*) FROM gallery_media WHERE source_on_s3 AND source_key IS NOT NULL")
    ).scalar_one()
    if waiting:
        raise RuntimeError(f"{waiting} gallery files wait on S3, where the previous release cannot find them")
    op.drop_table("gallery_s3_uploads")
    op.drop_column("gallery_media", "source_on_s3")
