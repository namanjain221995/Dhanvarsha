"""editable invitation email wording

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("programme_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("heading", sa.String(length=120), nullable=False),
        sa.Column("intro", sa.Text(), nullable=False),
        sa.Column("closing", sa.Text(), nullable=False),
        sa.Column("button_label", sa.String(length=60), nullable=False),
        sa.Column("updated_by_admin_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('invitation')", name="ck_email_templates_kind"),
        sa.ForeignKeyConstraint(["programme_id"], ["programmes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("programme_id", "kind"),
    )
    role = op.get_context().opts.get("app_role", "stagevote_app")
    if not role.replace("_", "").isalnum():
        raise ValueError(f"unsafe role name: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON email_templates TO {role};
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("email_templates")
