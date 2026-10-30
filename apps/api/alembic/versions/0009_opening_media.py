"""opening media: the image or video a card shows full screen

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-30 18:00:00.000000

A new asset kind, opening_media: the image or video an opening-page card shows full screen
when its button is pressed. Images are re-encoded to WebP as before; videos are stored as
uploaded, with width and height 0. assets.original_name keeps the uploaded file's name for
the organiser's editor.

Additive: the previous release never writes the new kind or reads the new column, and
table-level grants already cover it. The initial schema's kind check was named by
PostgreSQL (assets_kind_check); it is replaced by ck_assets_kind.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = "kind IN ('programme_logo', 'banner', 'event_logo', 'nominee_photo')"
_NEW_KINDS = "kind IN ('programme_logo', 'banner', 'event_logo', 'nominee_photo', 'opening_media')"
_BLOCKERS = "SELECT EXISTS (SELECT 1 FROM assets WHERE kind = 'opening_media')"
_REFUSAL = (
    "refusing to downgrade: the database holds opening-page images or videos (assets of kind "
    "opening_media), which the previous schema cannot hold. The previous release works with this "
    "schema as it is: roll back the application without downgrading the database."
)


def upgrade() -> None:
    op.drop_constraint("assets_kind_check", "assets", type_="check")
    op.create_check_constraint("ck_assets_kind", "assets", _NEW_KINDS)
    op.add_column("assets", sa.Column("original_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Never drop uploaded media a saved page may still show.
    if context.is_offline_mode():
        # A SQL script cannot look at the data first: the server refuses instead.
        op.execute(f"DO $$ BEGIN IF ({_BLOCKERS}) THEN RAISE EXCEPTION '{_REFUSAL}'; END IF; END $$")
    elif op.get_bind().execute(sa.text(_BLOCKERS)).scalar():
        raise RuntimeError(_REFUSAL)
    op.drop_column("assets", "original_name")
    op.drop_constraint("ck_assets_kind", "assets", type_="check")
    op.create_check_constraint("assets_kind_check", "assets", _OLD_KINDS)
