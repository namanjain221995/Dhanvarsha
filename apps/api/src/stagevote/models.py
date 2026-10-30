"""SQLAlchemy models.

Invariants that protect votes are enforced by PostgreSQL itself (unique constraints,
composite foreign keys, partial unique indexes, an exclusion constraint and an
immutability trigger defined in the initial migration). Application checks exist to give
good error messages; the database is the last line of defence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ACCENTS = ("violet", "plum", "indigo", "teal", "rose", "amber", "gold")
EVENT_STATUSES = ("upcoming", "open", "closed")
# team and individual ballots are votes for a nominee. mcq is a multiple-choice question
# whose answer options are nominees; text is a question answered by typing (no nominees).
BALLOT_KINDS = ("team", "individual", "mcq", "text")
# The longest typed answer, in characters (ck_votes_choice enforces the same limit).
ANSWER_MAX_LENGTH = 500
EMAIL_JOB_KINDS = ("invitation", "login_reminder", "password_reset", "test")
EMAIL_JOB_STATUSES = ("queued", "processing", "accepted", "failed", "unknown", "superseded", "cancelled")
SCHEDULE_JOB_STATUSES = ("pending", "done", "skipped", "missed", "stale", "failed")
# opening_media is the image or video an opening-page card shows full screen.
ASSET_KINDS = ("programme_logo", "banner", "event_logo", "nominee_photo", "opening_media")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (CheckConstraint(_in("kind", ASSET_KINDS), name="ck_assets_kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # The uploaded file's name, shown to the organiser (opening_media only).
    original_name: Mapped[str | None] = mapped_column(String(255))
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class Programme(Base):
    __tablename__ = "programmes"
    __table_args__ = (
        CheckConstraint(_in("accent", ACCENTS), name="ck_programmes_accent"),
        CheckConstraint(_in("run_mode", ("manual", "scheduled")), name="ck_programmes_run_mode"),
        CheckConstraint(
            "login_opens_at IS NULL OR login_closes_at IS NULL OR login_closes_at > login_opens_at",
            name="ck_programmes_login_window",
        ),
        CheckConstraint("jsonb_typeof(opening) = 'object'", name="ck_programmes_opening_object"),
        Index("uq_programmes_single_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    help_contact: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    logo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT")
    )
    banner_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT")
    )
    accent: Mapped[str] = mapped_column(String(20), nullable=False, default="violet")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    login_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    login_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    countdown_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    schedule_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preview_upcoming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publish_schedule: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The opening showcase page as the organiser saved it, {} until then. Every key is
    # optional: services/opening.py fills a missing one from the built-in page.
    opening: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # state_version changes whenever anything a participant can see changes; it is the
    # signal clients use to refetch their snapshot. live_version changes only on live
    # transitions and protects admin live controls against stale actions.
    state_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    live_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    settings_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("programme_id", "id", name="uq_events_programme_id_id"),
        CheckConstraint(_in("status", EVENT_STATUSES), name="ck_events_status"),
        CheckConstraint(
            "planned_start_at IS NULL OR planned_end_at IS NULL OR planned_end_at > planned_start_at",
            name="ck_events_planned_order",
        ),
        CheckConstraint(
            "(planned_start_at IS NULL) = (planned_end_at IS NULL)", name="ck_events_planned_pair"
        ),
        CheckConstraint(
            "status <> 'open' OR (opened_at IS NOT NULL AND voting_deadline_at IS NOT NULL)",
            name="ck_events_open_has_window",
        ),
        CheckConstraint("status = 'upcoming' OR opened_at IS NOT NULL", name="ck_events_started_was_opened"),
        CheckConstraint("status <> 'closed' OR closed_at IS NOT NULL", name="ck_events_closed_has_time"),
        CheckConstraint(
            "voting_deadline_at IS NULL OR opened_at IS NULL OR voting_deadline_at > opened_at",
            name="ck_events_deadline_after_open",
        ),
        CheckConstraint("status <> 'open' OR archived_at IS NULL", name="ck_events_open_not_archived"),
        CheckConstraint("char_length(organiser_note) <= 2000", name="ck_events_organiser_note_length"),
        # Invariant 5: at most one OPEN event per programme.
        Index(
            "uq_events_one_open_per_programme",
            "programme_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_events_programme_position", "programme_id", "position"),
        # Exclusion constraint for overlapping planned windows lives in the migration
        # (it needs btree_gist).
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    programme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programmes.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rules: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Only organisers see this (when to open the round, special instructions): it is in
    # admin payloads and never in participant or public ones.
    organiser_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    logo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="upcoming")
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voting_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(40))
    opened_by: Mapped[str | None] = mapped_column(String(16))
    opened_schedule_revision: Mapped[int | None] = mapped_column(Integer)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    results_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Ballot(Base):
    __tablename__ = "ballots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["programme_id", "event_id"],
            ["events.programme_id", "events.id"],
            name="fk_ballots_event_in_programme",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", "id", name="uq_ballots_event_id_id"),
        CheckConstraint(_in("kind", BALLOT_KINDS), name="ck_ballots_kind"),
        Index("ix_ballots_event", "event_id", "position"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    programme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # For mcq and text ballots the title is the question.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="team")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Nominee(Base):
    __tablename__ = "nominees"
    __table_args__ = (
        ForeignKeyConstraint(["ballot_id"], ["ballots.id"], name="fk_nominees_ballot", ondelete="RESTRICT"),
        UniqueConstraint("ballot_id", "id", name="uq_nominees_ballot_id_id"),
        Index("ix_nominees_ballot", "ballot_id", "position"),
        CheckConstraint("jsonb_typeof(members) = 'array'", name="ck_nominees_members_array"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ballot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    affiliation: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Team ballots: the names of the people in the team, shown to voters. The vote is for
    # the team as a whole, never for one member.
    members: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    photo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("programme_id", "email_normalized", name="uq_participants_programme_email"),
        UniqueConstraint("programme_id", "id", name="uq_participants_programme_id_id"),
        UniqueConstraint("link_token_hash", name="uq_participants_link_token_hash"),
        CheckConstraint(
            "activated_at IS NULL OR password_hash IS NOT NULL OR link_token_hash IS NOT NULL",
            name="ck_participants_activation",
        ),
        Index("ix_participants_programme_name", "programme_id", "display_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    programme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programmes.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Invariant 1: unique across the installation. Nobody types it any more: it labels the
    # participant in the audit log and the organiser console.
    user_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    # Retired with password sign-in: the Argon2id hash of the password from an invitation
    # sent before sign-in links. Unused, kept so a rollback finds the data it expects.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Retired: the earlier activation flow stored a temporary password here. Unused, kept
    # so existing databases need no destructive migration.
    temp_password_hash: Mapped[str | None] = mapped_column(String(255))
    temp_password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The personal sign-in link from the latest invitation: only the SHA-256 of its token is
    # stored. The first browser that enters with it is bound to it (the SHA-256 of that
    # browser's device cookie), and only that browser can enter with it again. A new
    # invitation replaces the token and clears the binding.
    link_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    link_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link_device_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    link_bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_invitation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # First successful sign-in (the column keeps its original name).
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class ParticipantSession(Base):
    __tablename__ = "participant_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["programme_id", "participant_id"],
            ["participants.programme_id", "participants.id"],
            name="fk_sessions_participant_in_programme",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("participant_id", "id", name="uq_sessions_participant_id_id"),
        # A participant may be signed in on several devices (migration 0005 dropped the old
        # one-session rule). This index finds their live sessions at login and on revocation.
        Index(
            "ix_sessions_active_by_participant",
            "participant_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    programme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        # Invariant 2: one vote per participant per ballot.
        UniqueConstraint("participant_id", "ballot_id", name="uq_votes_participant_ballot"),
        UniqueConstraint("participant_id", "idempotency_key", name="uq_votes_participant_idempotency"),
        # Invariants 3 and 4: the nominee belongs to the ballot, the ballot to the event,
        # the event and the participant to the same programme, the session to the voter.
        ForeignKeyConstraint(
            ["ballot_id", "nominee_id"],
            ["nominees.ballot_id", "nominees.id"],
            name="fk_votes_nominee_in_ballot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["event_id", "ballot_id"],
            ["ballots.event_id", "ballots.id"],
            name="fk_votes_ballot_in_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["programme_id", "event_id"],
            ["events.programme_id", "events.id"],
            name="fk_votes_event_in_programme",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["programme_id", "participant_id"],
            ["participants.programme_id", "participants.id"],
            name="fk_votes_participant_in_programme",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["participant_id", "session_id"],
            ["participant_sessions.participant_id", "participant_sessions.id"],
            name="fk_votes_session_of_participant",
            ondelete="RESTRICT",
        ),
        # A vote is either for a nominee or, on a text ballot, a typed answer. The composite
        # nominee foreign key is not checked when nominee_id is NULL.
        CheckConstraint(
            "(nominee_id IS NOT NULL AND answer_text IS NULL) OR "
            "(nominee_id IS NULL AND answer_text IS NOT NULL "
            f"AND char_length(answer_text) BETWEEN 1 AND {ANSWER_MAX_LENGTH})",
            name="ck_votes_choice",
        ),
        Index("ix_votes_ballot_nominee", "ballot_id", "nominee_id"),
        Index("ix_votes_event", "event_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    receipt_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    programme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ballot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    nominee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    answer_text: Mapped[str | None] = mapped_column(Text)
    participant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class RecoveryToken(Base):
    """Retired: self-service password-reset links were removed. Unused table, kept so
    existing databases need no destructive migration."""

    __tablename__ = "recovery_tokens"
    __table_args__ = (
        Index(
            "uq_recovery_one_outstanding",
            "participant_id",
            unique=True,
            postgresql_where=text("used_at IS NULL AND invalidated_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailJob(Base):
    __tablename__ = "email_jobs"
    __table_args__ = (
        CheckConstraint(_in("kind", EMAIL_JOB_KINDS), name="ck_email_jobs_kind"),
        CheckConstraint(_in("status", EMAIL_JOB_STATUSES), name="ck_email_jobs_status"),
        Index("ix_email_jobs_due", "status", "next_attempt_at"),
        Index("ix_email_jobs_participant", "participant_id", "created_at"),
        Index(
            "uq_email_jobs_one_queued_credential",
            "participant_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'queued' AND kind IN ('invitation', 'password_reset')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    programme_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programmes.id", ondelete="RESTRICT")
    )
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="RESTRICT")
    )
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(24))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Restricted payload (an invitation's sign-in link token) encrypted with
    # INVITATION_ENCRYPTION_KEY. Purged once the job is accepted, superseded or expired.
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    secret_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class EmailTemplate(Base):
    """Organiser-edited wording for one email kind. No row means the built-in wording.

    Only the wording is editable: the button with the personal sign-in link, the note
    under it and the footer are always added by the template, so an edit cannot remove them.
    """

    __tablename__ = "email_templates"
    __table_args__ = (CheckConstraint("kind IN ('invitation')", name="ck_email_templates_kind"),)

    programme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programmes.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    heading: Mapped[str] = mapped_column(String(120), nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False)
    closing: Mapped[str] = mapped_column(Text, nullable=False, default="")
    button_label: Mapped[str] = mapped_column(String(60), nullable=False)
    updated_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class EmailAttempt(Base):
    __tablename__ = "email_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IS NULL OR "
            + _in("outcome", ("accepted", "transient_error", "permanent_error", "unknown")),
            name="ck_email_attempts_outcome",
        ),
        UniqueConstraint("job_id", "attempt_no", name="uq_email_attempts_job_attempt"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))


class ScheduleJob(Base):
    __tablename__ = "schedule_jobs"
    __table_args__ = (
        CheckConstraint(_in("status", SCHEDULE_JOB_STATUSES), name="ck_schedule_jobs_status"),
        CheckConstraint(_in("action", ("open",)), name="ck_schedule_jobs_action"),
        # Duplicate jobs for the same transition and schedule revision are impossible.
        UniqueConstraint("event_id", "action", "schedule_revision", name="uq_schedule_jobs_transition"),
        Index("ix_schedule_jobs_due", "status", "run_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    programme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programmes.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_detail: Mapped[str | None] = mapped_column(String(300))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    programme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    topic: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_programme_created", "programme_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    programme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
