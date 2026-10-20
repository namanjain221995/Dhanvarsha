"""Invitation emails: the organiser's invitation creates the participant's sign-in.

Each invitation generates a random password, stores only its Argon2id hash as the
participant's password, and encrypts the plaintext into the email job so a delivery retry
sends the same password. The participant signs in with their email address and that
password; there is no activation step, sign-up or self-service reset. The worker purges
the ciphertext once the job is accepted, superseded or past `INVITATION_RETRY_HOURS`.

Sending a new invitation replaces the password (the previous one stops working) and
signs the participant out of any open browser session.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import conflict
from ..models import EmailJob, Participant, ParticipantSession
from ..security import encrypt_payload, hash_password, new_invitation_password
from ..state import Actor, audit, emit


def _message_id(job_id: uuid.UUID) -> str:
    return f"<{job_id}@{get_settings().mail_from_domain}>"


async def supersede_jobs(db: AsyncSession, participant_id: uuid.UUID, kinds: tuple[str, ...]) -> int:
    """Stop pending emails that would carry an outdated password."""
    result = await db.execute(
        update(EmailJob)
        .where(
            EmailJob.participant_id == participant_id,
            EmailJob.kind.in_(kinds),
            EmailJob.status.in_(("queued", "failed", "unknown")),
        )
        .values(
            status="superseded",
            secret_ciphertext=None,
            secret_purged_at=text("now()"),
            finished_at=text("now()"),
            lease_owner=None,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_participant_sessions(
    db: AsyncSession, participant: Participant, reason: str, now: datetime
) -> int:
    """Revoke every live session of a participant (caller holds the participant lock)."""
    result = await db.execute(
        update(ParticipantSession)
        .where(ParticipantSession.participant_id == participant.id, ParticipantSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
        .returning(ParticipantSession.id)
    )
    ids = list(result.scalars())
    if ids:
        # Name the revoked sessions so a later, valid session is never closed by this signal.
        await emit(
            db,
            participant.programme_id,
            "session",
            {"pid": str(participant.id), "sids": [str(i) for i in ids], "r": reason},
        )
    return len(ids)


async def issue_invitations(
    db: AsyncSession,
    participants: list[Participant],
    actor: Actor,
    now: datetime,
) -> list[EmailJob]:
    """Give each participant a new password and queue the invitation that carries it.

    Callers must hold FOR UPDATE locks on the participant rows. Disabled accounts are
    refused. For someone invited before, the previous password stops working, pending
    invitation emails with the old password are superseded, and open sessions end.
    """
    settings = get_settings()
    for participant in participants:
        if participant.disabled_at is not None:
            raise conflict("ACCOUNT_DISABLED", "Enable this account before inviting it.")
    passwords = [new_invitation_password() for _ in participants]
    hashes = await asyncio.gather(*(hash_password(p) for p in passwords))
    retry_until = now + timedelta(hours=settings.invitation_retry_hours)
    jobs: list[EmailJob] = []
    signed_out = 0
    for participant, password, hashed in zip(participants, passwords, hashes, strict=True):
        await supersede_jobs(db, participant.id, ("invitation",))
        if participant.password_hash is not None:
            # The password is being replaced: any session opened with the old one ends.
            signed_out += await revoke_participant_sessions(db, participant, "credentials_reissued", now)
        invitation_id = uuid.uuid4()
        participant.password_hash = hashed
        participant.credential_version += 1
        participant.current_invitation_id = invitation_id
        participant.last_invited_at = now
        job_id = uuid.uuid4()
        job = EmailJob(
            id=job_id,
            programme_id=participant.programme_id,
            participant_id=participant.id,
            invitation_id=invitation_id,
            kind="invitation",
            recipient=participant.email,
            status="queued",
            max_attempts=settings.email_max_attempts,
            message_id=_message_id(job_id),
            secret_ciphertext=encrypt_payload({"password": password}),
            secret_expires_at=retry_until,
            created_by_admin_id=actor.id if actor.type == "admin" else None,
        )
        db.add(job)
        jobs.append(job)
    await db.flush()
    if participants:
        await audit(
            db,
            actor,
            "invitation.issued",
            programme_id=participants[0].programme_id,
            target_type="participant",
            target_id=participants[0].id if len(participants) == 1 else None,
            details={
                "count": len(participants),
                "participant_ids": [str(p.id) for p in participants[:50]],
                "sessions_signed_out": signed_out,
            },
        )
        await emit(db, participants[0].programme_id, "email", {"n": len(jobs)})
    return jobs


async def queue_test_email(
    db: AsyncSession, recipient: str, actor: Actor, programme_id: uuid.UUID | None
) -> EmailJob:
    job_id = uuid.uuid4()
    job = EmailJob(
        id=job_id,
        programme_id=programme_id,
        kind="test",
        recipient=recipient,
        status="queued",
        max_attempts=1,
        message_id=_message_id(job_id),
        created_by_admin_id=actor.id,
    )
    db.add(job)
    await db.flush()
    await audit(db, actor, "email.test_queued", programme_id=programme_id, details={"recipient": recipient})
    await emit(db, programme_id, "email", {"n": 1})
    return job


async def lock_participants(
    db: AsyncSession, programme_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Participant]:
    rows = await db.execute(
        select(Participant)
        .where(Participant.programme_id == programme_id, Participant.id.in_(ids))
        .order_by(Participant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())
