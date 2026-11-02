"""the gallery's files on S3

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-03 06:00:00.000000

gallery_media.location says where a file's storage_key and display_key are: on the local media
volume, or S3 keys, which are longer. gallery_people.folder is a person's folder on S3, and the worker
brings it up to date whenever folder_wanted is ahead of folder_done. gallery_person_files records what
is in those folders, and has no foreign keys on purpose: its rows must outlive a removed person or file
until the worker has deleted their copies. gallery_storage_trash is S3 objects waiting to be deleted.

Additive: every existing file starts as local, and the previous release ignores the rest. The downgrade
does not touch S3, and refuses while any file is there, because the previous release could not find it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gallery_media", sa.Column("location", sa.String(length=8), server_default="local", nullable=False)
    )
    op.create_check_constraint("ck_gallery_media_location", "gallery_media", "location IN ('local', 's3')")
    op.alter_column("gallery_media", "storage_key", type_=sa.String(length=1024))
    op.alter_column("gallery_media", "display_key", type_=sa.String(length=1024))
    op.create_index(
        "ix_gallery_media_local",
        "gallery_media",
        ["created_at"],
        postgresql_where=sa.text("status = 'ready' AND location = 'local'"),
    )

    op.add_column("gallery_people", sa.Column("folder", sa.String(length=120), nullable=True))
    op.add_column(
        "gallery_people", sa.Column("folder_wanted", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "gallery_people", sa.Column("folder_done", sa.Integer(), server_default="0", nullable=False)
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_gallery_people_folder ON gallery_people (programme_id, lower(folder)) "
        "WHERE folder IS NOT NULL"
    )
    op.create_index(
        "ix_gallery_people_folder_due",
        "gallery_people",
        ["updated_at"],
        postgresql_where=sa.text("folder_wanted <> folder_done"),
    )

    op.create_table(
        "gallery_person_files",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("person_id", "media_id"),
    )
    op.create_index("ix_gallery_person_files_media", "gallery_person_files", ["media_id"])

    op.create_table(
        "gallery_storage_trash",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    role = op.get_context().opts.get("app_role", "stagevote_app")
    if not role.replace("_", "").isalnum():
        raise ValueError(f"unsafe role name: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON gallery_person_files, gallery_storage_trash TO {role};
                GRANT USAGE, SELECT ON SEQUENCE gallery_storage_trash_id_seq TO {role};
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    on_s3 = bind.execute(sa.text("SELECT count(*) FROM gallery_media WHERE location = 's3'")).scalar_one()
    if on_s3:
        raise RuntimeError(f"{on_s3} gallery files are on S3, where the previous release cannot find them")
    op.drop_table("gallery_storage_trash")
    op.drop_table("gallery_person_files")
    op.drop_index("ix_gallery_people_folder_due", table_name="gallery_people")
    op.execute("DROP INDEX uq_gallery_people_folder")
    op.drop_column("gallery_people", "folder_done")
    op.drop_column("gallery_people", "folder_wanted")
    op.drop_column("gallery_people", "folder")
    op.drop_index("ix_gallery_media_local", table_name="gallery_media")
    op.alter_column("gallery_media", "display_key", type_=sa.String(length=80))
    op.alter_column("gallery_media", "storage_key", type_=sa.String(length=80))
    op.drop_constraint("ck_gallery_media_location", "gallery_media", type_="check")
    op.drop_column("gallery_media", "location")
