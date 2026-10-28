"""Durable email delivery: leasing, sending, retries, lease recovery and secret purging."""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from jinja2 import TemplateError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..logging import get_logger
from ..models import EmailAttempt, EmailJob, Participant, Programme
from ..security import decrypt_payload
from ..services.email_templates import invitation_template, programme_context, wording_of
from ..services.invitations import LINK_TOKEN_KEY, sign_in_link
from ..services.participants import name_from_email
from ..state import emit
from .providers import InlineImage, OutgoingEmail, Provider, SendResult
from .templates import (
    BRAND_MARK_CID,
    LOGO_CID,
    PREVIEW_LINK_TOKEN,
    STAGE_BANNER_CID,
    brand_mark_png,
    email_logo_png,
    render_invitation,
    stage_banner_gif,
)

log = get_logger(__name__)
LEASE_SECONDS = 120


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: about 30s, 1m, 2m, 4m... capped at 30 minutes."""
    base = min(30 * (2 ** max(attempt - 1, 0)), 1800)
    return base * random.uniform(0.5, 1.0)


async def lease_jobs(db: AsyncSession, worker_id: str, limit: int) -> list[uuid.UUID]:
    async with db.begin():
        rows = await db.execute(
            text(
                f"""
                UPDATE email_jobs SET status = 'processing', lease_owner = :w,
                       lease_expires_at = now() + interval '{LEASE_SECONDS} seconds',
                       attempts = attempts + 1, updated_at = now()
                WHERE id IN (
                    SELECT id FROM email_jobs
                    WHERE status = 'queued' AND next_attempt_at <= now()
                    ORDER BY next_attempt_at, created_at
                    LIMIT :n
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id
                """
            ),
            {"w": worker_id, "n": limit},
        )
        return list(rows.scalars())


async def _finish(
    db: AsyncSession,
    job: EmailJob,
    status: str,
    *,
    code: str | None = None,
    error: str | None = None,
    purge: bool = True,
) -> None:
    job.status = status
    job.lease_owner = None
    job.lease_expires_at = None
    job.finished_at = datetime.now(job.created_at.tzinfo)
    if code:
        job.last_error_code = code
    if error:
        job.last_error = error[:500]
    if purge and job.secret_ciphertext is not None:
        job.secret_ciphertext = None
        job.secret_purged_at = datetime.now(job.created_at.tzinfo)


async def _prepare(
    db: AsyncSession, job: EmailJob, settings: Settings, provider: Provider
) -> OutgoingEmail | None:
    """Re-check that the job is still wanted and render it. None means: do not send."""
    now: datetime = (await db.execute(text("SELECT now()"))).scalar_one()
    participant: Participant | None = None
    if job.participant_id is not None:
        participant = (
            await db.execute(
                select(Participant)
                .where(Participant.id == job.participant_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if participant.disabled_at is not None:
            await _finish(
                db, job, "cancelled", code="account_disabled", error="Participant disabled before sending"
            )
            return None
    if job.kind == "invitation":
        assert participant is not None
        if participant.current_invitation_id != job.invitation_id:
            await _finish(
                db, job, "superseded", code="superseded", error="A newer invitation replaced this one"
            )
            return None
    elif job.kind != "test":
        # Kinds from the retired activation/reset flow are never sent.
        await _finish(
            db, job, "cancelled", code="retired_kind", error=f"{job.kind} emails are no longer sent"
        )
        return None
    secret: dict[str, Any] = {}
    if job.kind == "invitation":
        if job.secret_ciphertext is None or (job.secret_expires_at and job.secret_expires_at <= now):
            await _finish(
                db,
                job,
                "failed",
                code="credential_expired",
                error="The sign-in link copy for this email has expired; send the invitation again",
            )
            return None
        secret = decrypt_payload(job.secret_ciphertext)
        if LINK_TOKEN_KEY not in secret:
            # Includes invitations queued before sign-in links, which carry a password.
            await _finish(
                db, job, "failed", code="retired_payload", error="Created by an older version; send it again"
            )
            return None

    programme = await db.get(Programme, job.programme_id) if job.programme_id else None
    context, logo_key = await programme_context(db, programme, settings)
    wording = wording_of(await invitation_template(db, job.programme_id))
    inline: list[InlineImage] = []
    if logo_key:
        logo = await asyncio.to_thread(email_logo_png, settings.media_root, logo_key)
        if logo:
            inline.append(InlineImage(cid=LOGO_CID, data=logo, subtype="png"))
            context["logo_src"] = f"cid:{LOGO_CID}"
    if context["theme"] == "stage":
        banner = await asyncio.to_thread(stage_banner_gif)
        inline.append(InlineImage(cid=STAGE_BANNER_CID, data=banner, subtype="gif"))
        context["banner_src"] = f"cid:{STAGE_BANNER_CID}"
        mark = await asyncio.to_thread(brand_mark_png)
        inline.append(InlineImage(cid=BRAND_MARK_CID, data=mark, subtype="png"))
        context["mark_src"] = f"cid:{BRAND_MARK_CID}"
    if job.kind == "invitation":
        assert participant is not None
        context |= {
            "display_name": participant.display_name or name_from_email(participant.email),
            "link": sign_in_link(settings.app_base_url, secret[LINK_TOKEN_KEY]),
        }
    else:
        # A preview: the real design and wording with an example link that signs nobody in.
        context |= {"display_name": "", "link": sign_in_link(settings.app_base_url, PREVIEW_LINK_TOKEN)}
    subject, body_text, body_html = render_invitation(context, wording, preview=job.kind == "test")
    return OutgoingEmail(
        to=job.recipient,
        subject=subject,
        text=body_text,
        html=body_html,
        message_id=job.message_id,
        headers={"X-StageVote-Job": str(job.id)},
        inline_images=inline,
    )


async def process_job(
    maker: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    worker_id: str,
    provider: Provider,
    settings: Settings,
) -> SendResult | None:
    async with maker() as db:
        async with db.begin():
            job = (
                await db.execute(
                    select(EmailJob)
                    .where(
                        EmailJob.id == job_id,
                        EmailJob.status == "processing",
                        EmailJob.lease_owner == worker_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return None
            try:
                message = await _prepare(db, job, settings, provider)
            except ValueError as exc:  # undecryptable payload: wrong key
                await _finish(db, job, "failed", code="payload_unreadable", error=str(exc), purge=False)
                return None
            except TemplateError as exc:  # the template and its context disagree: a bug, not a retry
                log.exception("email template failed", extra={"job_id": str(job_id)})
                await _finish(
                    db,
                    job,
                    "failed",
                    code="template_error",
                    error=f"The email could not be built from its template ({type(exc).__name__}); "
                    "report this to the developers",
                    purge=False,
                )
                return None
            if message is None:
                await emit(db, job.programme_id, "email", {"j": str(job.id)})
                return None
            attempt = EmailAttempt(job_id=job.id, attempt_no=job.attempts, worker_id=worker_id)
            db.add(attempt)
            await db.flush()
            attempt_id = attempt.id
            job.provider = provider.name
        # The attempt row is committed before the provider is called: if this worker dies
        # mid-send, lease recovery sees a started attempt and marks the job `unknown`.

        try:
            result = await provider.send(message)
        except Exception as exc:
            log.exception("provider raised", extra={"job_id": str(job_id)})
            result = SendResult("unknown", error_code="provider_exception", error_detail=type(exc).__name__)

        async with db.begin():
            await db.execute(
                text(
                    "UPDATE email_attempts SET finished_at = now(), outcome = :o, error_code = :c, error_detail = :d, "
                    "provider_message_id = :m WHERE id = :id"
                ),
                {
                    "o": result.outcome,
                    "c": result.error_code,
                    "d": result.error_detail,
                    "m": result.provider_message_id,
                    "id": attempt_id,
                },
            )
            job = (
                await db.execute(
                    select(EmailJob)
                    .where(EmailJob.id == job_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            still_ours = job.status == "processing" and job.lease_owner == worker_id
            if result.outcome == "accepted":
                job.provider_message_id = result.provider_message_id
                job.accepted_at = datetime.now(job.created_at.tzinfo)
                if still_ours:
                    await _finish(db, job, "accepted")
            elif not still_ours:
                pass  # superseded or cancelled while sending; keep that status
            elif result.outcome == "transient_error" and job.attempts < job.max_attempts:
                job.status = "queued"
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = datetime.now(job.created_at.tzinfo) + timedelta(
                    seconds=backoff_seconds(job.attempts)
                )
                job.last_error_code = result.error_code
                job.last_error = result.error_detail
            elif result.outcome == "unknown":
                # Keep the credential so an admin can deliberately retry the same message.
                await _finish(
                    db, job, "unknown", code=result.error_code, error=result.error_detail, purge=False
                )
            else:
                await _finish(
                    db, job, "failed", code=result.error_code, error=result.error_detail, purge=False
                )
            await emit(db, job.programme_id, "email", {"j": str(job.id)})
    return result


async def recover_expired_leases(db: AsyncSession) -> int:
    """Jobs whose worker vanished: never-started sends are requeued, started ones become unknown."""
    async with db.begin():
        rows = (
            await db.execute(
                text(
                    """
                    SELECT j.id, j.programme_id,
                           EXISTS (SELECT 1 FROM email_attempts a WHERE a.job_id = j.id
                                   AND a.attempt_no = j.attempts AND a.finished_at IS NULL) AS started
                    FROM email_jobs j
                    WHERE j.status = 'processing' AND j.lease_expires_at < now()
                    FOR UPDATE OF j SKIP LOCKED
                    """
                )
            )
        ).all()
        for row in rows:
            if row.started:
                await db.execute(
                    text(
                        "UPDATE email_jobs SET status = 'unknown', lease_owner = NULL, lease_expires_at = NULL, "
                        "finished_at = now(), last_error_code = 'worker_interrupted', last_error = :e WHERE id = :id"
                    ),
                    {
                        "id": row.id,
                        "e": "The worker stopped during delivery; the provider may or may not have accepted it",
                    },
                )
                await db.execute(
                    text(
                        "UPDATE email_attempts SET finished_at = now(), outcome = 'unknown', "
                        "error_code = 'worker_interrupted' WHERE job_id = :id AND finished_at IS NULL"
                    ),
                    {"id": row.id},
                )
            else:
                await db.execute(
                    text(
                        "UPDATE email_jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
                        "attempts = GREATEST(attempts - 1, 0), next_attempt_at = now() WHERE id = :id"
                    ),
                    {"id": row.id},
                )
            await emit(db, row.programme_id, "email", {"j": str(row.id)})
        return len(rows)


async def release_unstarted(db: AsyncSession, worker_id: str, job_ids: list[uuid.UUID]) -> None:
    """On shutdown: hand leased-but-unstarted jobs back to the queue."""
    if not job_ids:
        return
    async with db.begin():
        await db.execute(
            text(
                "UPDATE email_jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
                "attempts = GREATEST(attempts - 1, 0) WHERE id = ANY(:ids) AND lease_owner = :w AND status = 'processing' "
                "AND NOT EXISTS (SELECT 1 FROM email_attempts a WHERE a.job_id = email_jobs.id "
                "AND a.attempt_no = email_jobs.attempts)"
            ),
            {"ids": job_ids, "w": worker_id},
        )


async def purge_secrets(db: AsyncSession) -> int:
    async with db.begin():
        expired_queued = await db.execute(
            text(
                "UPDATE email_jobs SET status = 'failed', last_error_code = 'credential_expired', "
                "last_error = 'The sign-in link copy expired before delivery; send the invitation again', "
                "finished_at = now() "
                "WHERE status = 'queued' AND secret_expires_at IS NOT NULL AND secret_expires_at <= now()"
            )
        )
        purged = await db.execute(
            text(
                "UPDATE email_jobs SET secret_ciphertext = NULL, secret_purged_at = now() "
                "WHERE secret_ciphertext IS NOT NULL AND (secret_expires_at <= now() "
                "OR status IN ('accepted', 'superseded', 'cancelled'))"
            )
        )
        return int(getattr(expired_queued, "rowcount", 0) or 0) + int(getattr(purged, "rowcount", 0) or 0)
