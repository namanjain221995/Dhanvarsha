"""question ballots: multiple-choice and typed answers

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30 09:00:00.000000

Two new ballot kinds: "mcq" (a multiple-choice question whose answer options are nominees)
and "text" (a question answered by typing). Ballot titles may now be up to 200 characters,
since a question is longer than a vote title.

A typed answer is a row in `votes` with `answer_text` and no nominee, so every vote
invariant applies to it unchanged: one per participant per ballot, idempotency keys, the
immutability trigger, receipts and the session, event and programme foreign keys. The
composite foreign key (ballot_id, nominee_id) is not checked when nominee_id is NULL.
ck_votes_choice makes every row exactly one of the two: a nominee, or 1-500 characters
of text.

Additive only: the previous release reads and writes this schema as before. Table-level
grants already cover the new column, and the runtime role still has only SELECT and
INSERT on votes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = "kind IN ('team', 'individual')"
_NEW_KINDS = "kind IN ('team', 'individual', 'mcq', 'text')"
_CHOICE = (
    "(nominee_id IS NOT NULL AND answer_text IS NULL) OR "
    "(nominee_id IS NULL AND answer_text IS NOT NULL AND char_length(answer_text) BETWEEN 1 AND 500)"
)
# Anything the previous schema cannot hold: question ballots, typed answers (immutable,
# so they could never be removed) and titles longer than the old limit.
_BLOCKERS = (
    "SELECT EXISTS (SELECT 1 FROM ballots WHERE kind IN ('mcq', 'text') OR char_length(title) > 80) "
    "OR EXISTS (SELECT 1 FROM votes WHERE answer_text IS NOT NULL)"
)
_REFUSAL = (
    "refusing to downgrade: the database holds question ballots, typed answers or ballot titles "
    "longer than 80 characters, which the previous schema cannot hold. The previous release works "
    "with this schema as it is: roll back the application without downgrading the database."
)


def upgrade() -> None:
    op.drop_constraint("ck_ballots_kind", "ballots", type_="check")
    op.create_check_constraint("ck_ballots_kind", "ballots", _NEW_KINDS)
    op.alter_column(
        "ballots",
        "title",
        type_=sa.String(length=200),
        existing_type=sa.String(length=80),
        existing_nullable=False,
    )
    # None of these changes an existing row, so the immutability trigger is not involved.
    op.alter_column("votes", "nominee_id", nullable=True, existing_type=sa.UUID())
    op.add_column("votes", sa.Column("answer_text", sa.Text(), nullable=True))
    op.create_check_constraint("ck_votes_choice", "votes", _CHOICE)


def downgrade() -> None:
    # Never destroy recorded answers, or questions people may still be answering.
    if context.is_offline_mode():
        # A SQL script cannot look at the data first: the server refuses instead.
        op.execute(f"DO $$ BEGIN IF ({_BLOCKERS}) THEN RAISE EXCEPTION '{_REFUSAL}'; END IF; END $$")
    elif op.get_bind().execute(sa.text(_BLOCKERS)).scalar():
        raise RuntimeError(_REFUSAL)
    op.drop_constraint("ck_votes_choice", "votes", type_="check")
    op.drop_column("votes", "answer_text")
    op.alter_column("votes", "nominee_id", nullable=False, existing_type=sa.UUID())
    op.alter_column(
        "ballots",
        "title",
        type_=sa.String(length=80),
        existing_type=sa.String(length=200),
        existing_nullable=False,
    )
    op.drop_constraint("ck_ballots_kind", "ballots", type_="check")
    op.create_check_constraint("ck_ballots_kind", "ballots", _OLD_KINDS)
