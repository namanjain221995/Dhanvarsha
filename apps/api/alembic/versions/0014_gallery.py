"""photo gallery

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03 01:00:00.000000

The event's shared photos and videos, sorted by the faces in them (services/gallery.py).

gallery_people is one person: someone who gave a selfie and a name, or a group of matching faces
nobody has named yet. gallery_devices is the browsers that remember a person. gallery_media is
one uploaded file and its place in the worker's queue. gallery_faces is one face found in a file.

Additive only: the previous release never reads these tables. The two kind checks widen to allow
the gallery email. The files stay on the media volume after a downgrade, which removes the rows
that name them, along with the face data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = "kind IN ('invitation', 'login_reminder', 'password_reset', 'test')"
NEW_JOB_KINDS = (
    "kind IN ('invitation', 'login_reminder', 'password_reset', 'test', 'gallery', 'gallery_test')"
)


def upgrade() -> None:
    op.create_table(
        "gallery_people",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("programme_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), server_default="", nullable=False),
        sa.Column("participant_id", sa.UUID(), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("aligned", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_key", sa.String(length=80), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rematch_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["programme_id"], ["programmes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gallery_people_programme", "gallery_people", ["programme_id", "name"])
    op.create_index(
        "uq_gallery_people_participant",
        "gallery_people",
        ["participant_id"],
        unique=True,
        postgresql_where=sa.text("participant_id IS NOT NULL"),
    )
    op.create_index(
        "ix_gallery_people_rematch",
        "gallery_people",
        ["updated_at"],
        postgresql_where=sa.text("rematch_pending"),
    )

    op.create_table(
        "gallery_devices",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["gallery_people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_gallery_devices_person", "gallery_devices", ["person_id"])

    op.create_table(
        "gallery_media",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("programme_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="queued", nullable=False),
        sa.Column("source_key", sa.String(length=120), nullable=True),
        sa.Column("storage_key", sa.String(length=80), nullable=True),
        sa.Column("display_key", sa.String(length=80), nullable=True),
        sa.Column("thumb_key", sa.String(length=80), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), server_default="0", nullable=False),
        sa.Column("height", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("original_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("uploader_person_id", sa.UUID(), nullable=True),
        sa.Column("uploader_ip", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("lease_owner", sa.String(length=80), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("face_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('image', 'video')", name="ck_gallery_media_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')", name="ck_gallery_media_status"
        ),
        sa.ForeignKeyConstraint(["programme_id"], ["programmes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploader_person_id"], ["gallery_people.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("programme_id", "sha256", name="uq_gallery_media_file"),
    )
    op.create_index("ix_gallery_media_due", "gallery_media", ["status", "next_attempt_at"])
    op.create_index("ix_gallery_media_uploader", "gallery_media", ["uploader_person_id"])
    op.create_index("ix_gallery_media_recent", "gallery_media", ["programme_id", "created_at"])

    op.create_table(
        "gallery_faces",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("aligned", sa.LargeBinary(), nullable=False),
        sa.Column("crop_key", sa.String(length=80), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("frame_ms", sa.Integer(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("matched_by", sa.String(length=12), server_default="new", nullable=False),
        sa.Column("is_reference", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "matched_by IN ('new', 'opencv', 'model', 'merge')", name="ck_gallery_faces_matched_by"
        ),
        sa.ForeignKeyConstraint(["media_id"], ["gallery_media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["gallery_people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gallery_faces_person", "gallery_faces", ["person_id", "media_id"])
    op.create_index("ix_gallery_faces_media", "gallery_faces", ["media_id"])
    op.create_index(
        "ix_gallery_faces_reference",
        "gallery_faces",
        ["person_id"],
        postgresql_where=sa.text("is_reference"),
    )

    op.drop_constraint("ck_email_jobs_kind", "email_jobs", type_="check")
    op.create_check_constraint("ck_email_jobs_kind", "email_jobs", NEW_JOB_KINDS)
    op.drop_constraint("ck_email_templates_kind", "email_templates", type_="check")
    op.create_check_constraint(
        "ck_email_templates_kind", "email_templates", "kind IN ('invitation', 'gallery')"
    )

    role = op.get_context().opts.get("app_role", "stagevote_app")
    if not role.replace("_", "").isalnum():
        raise ValueError(f"unsafe role name: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON gallery_people, gallery_devices, gallery_media, gallery_faces TO {role};
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE kind = 'gallery'")
    op.drop_constraint("ck_email_templates_kind", "email_templates", type_="check")
    op.create_check_constraint("ck_email_templates_kind", "email_templates", "kind IN ('invitation')")
    op.execute("DELETE FROM email_jobs WHERE kind IN ('gallery', 'gallery_test')")
    op.drop_constraint("ck_email_jobs_kind", "email_jobs", type_="check")
    op.create_check_constraint("ck_email_jobs_kind", "email_jobs", OLD_JOB_KINDS)
    op.drop_table("gallery_faces")
    op.drop_table("gallery_media")
    op.drop_table("gallery_devices")
    op.drop_table("gallery_people")
