"""participants sign in with a personal link instead of a password

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28 09:00:00.000000

Adds the personal sign-in link to participants: the SHA-256 of the token from the latest
invitation (unique), when it was issued, and the browser it is bound to (the SHA-256 of
that browser's device cookie) with the time it was bound. A participant may now be
activated with a link instead of a password.

Additive only: password_hash and the retired columns stay, so the previous release keeps
working against this schema while it is rolled out or rolled back. Table-level grants
already cover the new columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "activated_at IS NULL OR password_hash IS NOT NULL"
_NEW = "activated_at IS NULL OR password_hash IS NOT NULL OR link_token_hash IS NOT NULL"
_REFUSAL = (
    "refusing to downgrade: participants hold sign-in links, which this downgrade would destroy "
    "(and anyone who entered with a link has no password to fall back on). The previous release "
    "works with this schema as it is: roll back the application without downgrading the database."
)


def upgrade() -> None:
    op.add_column("participants", sa.Column("link_token_hash", sa.LargeBinary(length=32), nullable=True))
    op.add_column("participants", sa.Column("link_issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("participants", sa.Column("link_device_hash", sa.LargeBinary(length=32), nullable=True))
    op.add_column("participants", sa.Column("link_bound_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_participants_link_token_hash", "participants", ["link_token_hash"])
    op.drop_constraint("ck_participants_activation", "participants", type_="check")
    op.create_check_constraint("ck_participants_activation", "participants", _NEW)


def downgrade() -> None:
    # Never drop issued sign-in links by accident.
    if context.is_offline_mode():
        # A SQL script cannot look at the data first: the server refuses instead.
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM participants WHERE link_token_hash IS NOT NULL) THEN "
            f"RAISE EXCEPTION '{_REFUSAL}'; END IF; END $$"
        )
    elif (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM participants WHERE link_token_hash IS NOT NULL)"))
        .scalar()
    ):
        raise RuntimeError(_REFUSAL)
    op.drop_constraint("ck_participants_activation", "participants", type_="check")
    op.create_check_constraint("ck_participants_activation", "participants", _OLD)
    op.drop_constraint("uq_participants_link_token_hash", "participants", type_="unique")
    op.drop_column("participants", "link_bound_at")
    op.drop_column("participants", "link_device_hash")
    op.drop_column("participants", "link_issued_at")
    op.drop_column("participants", "link_token_hash")
