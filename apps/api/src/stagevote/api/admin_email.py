"""Admin email: provider diagnostics, invitation wording, previews, job status and controlled retries."""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BeforeValidator, Field, StringConstraints
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import conflict, not_found, validation
from ..mailer.providers import make_provider
from ..mailer.templates import (
    DEFAULT_INVITATION,
    PLACEHOLDERS,
    PREVIEW_LINK_TOKEN,
    Wording,
    render_invitation,
    unknown_placeholders,
)
from ..models import EmailAttempt, EmailJob, EmailTemplate
from ..services.email_templates import invitation_template, programme_context, wording_of
from ..services.invitations import queue_test_email, sign_in_link
from ..services.participants import normalize_email
from ..services.programmes import active_programme
from ..state import audit, emit
from .common import Model, SingleLine, no_store

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


def _provider_summary() -> dict[str, Any]:
    s = get_settings()
    summary: dict[str, Any] = {
        "provider": s.email_provider,
        "mail_from": s.mail_from,
        "environment": s.app_env,
    }
    if s.email_provider in ("smtp", "mailpit"):
        summary |= {
            "smtp_host": s.smtp_host,
            "smtp_port": s.smtp_port,
            "smtp_tls_mode": s.smtp_tls_mode,
            "smtp_username_set": bool(s.smtp_username),
            "smtp_password_set": bool(s.smtp_password and s.smtp_password.get_secret_value()),
        }
    if s.email_provider == "gmail_workspace":
        summary |= {
            "google_service_account_file": s.google_service_account_file,
            "google_workspace_sender": s.google_workspace_sender,
        }
    if s.email_provider == "mailpit" and not s.is_production:
        summary["test_inbox_url"] = s.mailpit_ui_url
    return summary


@router.get("/email/status")
async def email_status(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        counts: dict[Any, Any] = dict(
            (
                await db.execute(
                    text(
                        "SELECT status, count(*) FROM email_jobs WHERE programme_id = :p OR programme_id IS NULL GROUP BY status"
                    ),
                    {"p": programme.id},
                )
            ).all()
        )
        worker = (
            await db.execute(
                text(
                    "SELECT worker_id, last_seen_at, now() - last_seen_at < interval '30 seconds' AS healthy, info "
                    "FROM worker_heartbeats ORDER BY last_seen_at DESC LIMIT 1"
                )
            )
        ).first()
        recent_config_error = (
            await db.execute(
                text(
                    "SELECT last_error_code, last_error, updated_at FROM email_jobs "
                    "WHERE status = 'failed' AND last_error_code IN ('smtp_auth_failed', 'google_auth', 'google_401', "
                    "'google_403', 'google_credentials_missing', 'email_disabled') "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
            )
        ).first()
    return {
        "config": _provider_summary(),
        "counts": {k: int(v) for k, v in counts.items()},
        "worker": {
            "id": worker.worker_id if worker else None,
            "last_seen_at": worker.last_seen_at if worker else None,
            "healthy": bool(worker and worker.healthy),
            "paused_until": (worker.info or {}).get("email_paused_until") if worker else None,
        },
        "recent_config_error": dict(recent_config_error._mapping) if recent_config_error else None,
        "notes": [
            "“Accepted” means the provider took responsibility for the message. It does not prove inbox delivery; "
            "bounces and spam filtering are not reported back to StageVote.",
        ],
    }


@router.post("/email/diagnose")
async def diagnose(
    response: Response, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Check configuration and authentication without sending a message."""
    no_store(response)
    settings = get_settings()
    problems: list[str] = []
    try:
        settings.validate_for_runtime()
    except Exception as exc:
        problems.append(str(exc))
    checks = [c.public() for c in await make_provider(settings).diagnose()]
    async with db.begin():
        programme = await active_programme(db)
        await audit(
            db,
            ctx.actor,
            "email.diagnosed",
            programme_id=programme.id,
            details={"statuses": [c["status"] for c in checks]},
        )
    return {"provider": settings.email_provider, "checks": checks, "config_problems": problems}


# ---- invitation wording ---------------------------------------------------------


def _newlines(value: Any) -> Any:
    return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value


# Paragraph text: line breaks and tabs allowed, other control characters refused.
MultiLine = Annotated[
    str, BeforeValidator(_newlines), StringConstraints(pattern=r"^[^\x00-\x08\x0b-\x1f\x7f]*$")
]


class WordingIn(Model):
    subject: SingleLine = Field(min_length=1, max_length=200)
    heading: SingleLine = Field(min_length=1, max_length=120)
    intro: MultiLine = Field(min_length=1, max_length=3000)
    closing: MultiLine = Field(default="", max_length=3000)
    button_label: SingleLine = Field(min_length=1, max_length=60)


def _checked(body: WordingIn) -> Wording:
    wording = Wording(**body.model_dump())
    unknown = unknown_placeholders(*wording.as_dict().values())
    if unknown:
        allowed = ", ".join(f"{{{name}}}" for name in PLACEHOLDERS)
        raise validation(
            f"Unknown placeholder {', '.join(unknown)}. You can use {allowed}.",
            {"unknown": unknown},
        )
    return wording


def _template_out(row: EmailTemplate | None) -> dict[str, Any]:
    return {
        "wording": wording_of(row).as_dict(),
        "defaults": DEFAULT_INVITATION.as_dict(),
        "customised": row is not None,
        "updated_at": row.updated_at if row else None,
        "placeholders": [
            {"token": f"{{{name}}}", "meaning": meaning} for name, meaning in PLACEHOLDERS.items()
        ],
    }


@router.get("/email/templates/invitation")
async def get_invitation_template(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        return _template_out(await invitation_template(db, programme.id))


@router.put("/email/templates/invitation")
async def save_invitation_template(
    body: WordingIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    wording = _checked(body)
    async with db.begin():
        programme = await active_programme(db)
        values = wording.as_dict()
        await db.execute(
            pg_insert(EmailTemplate)
            .values(programme_id=programme.id, kind="invitation", updated_by_admin_id=ctx.admin_id, **values)
            .on_conflict_do_update(
                index_elements=["programme_id", "kind"],
                set_={**values, "updated_by_admin_id": ctx.admin_id, "updated_at": func.now()},
            )
        )
        await audit(
            db, ctx.actor, "email.template_saved", programme_id=programme.id, details={"kind": "invitation"}
        )
        return _template_out(await invitation_template(db, programme.id))


@router.delete("/email/templates/invitation")
async def reset_invitation_template(
    response: Response, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Go back to the built-in wording."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        await db.execute(
            delete(EmailTemplate).where(
                EmailTemplate.programme_id == programme.id, EmailTemplate.kind == "invitation"
            )
        )
        await audit(
            db, ctx.actor, "email.template_reset", programme_id=programme.id, details={"kind": "invitation"}
        )
    return _template_out(None)


@router.post("/email/templates/invitation/preview")
async def preview_invitation(
    body: WordingIn, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Render (unsaved) wording for an example participant. Nothing is stored or sent."""
    no_store(response)
    wording = _checked(body)
    settings = get_settings()
    async with db.begin():
        programme = await active_programme(db)
        context, logo_key = await programme_context(db, programme, settings)
    if logo_key:
        context["logo_src"] = f"/media/{logo_key}"
    if context["theme"] == "stage":
        context["banner_src"] = "/api/v1/public/email-banner.gif"
    # The example link signs nobody in, like the one in preview emails.
    context |= {"display_name": "Asha Rao", "link": sign_in_link(settings.app_base_url, PREVIEW_LINK_TOKEN)}
    subject, body_text, body_html = render_invitation(context, wording)
    return {"subject": subject, "text": body_text, "html": body_html}


# ---- previews by email ------------------------------------------------------------

MAX_PREVIEW_RECIPIENTS = 10


class TestIn(Model):
    # One or more addresses separated by commas, semicolons, spaces or new lines.
    to: str = Field(min_length=3, max_length=4000)


def _recipients(raw: str) -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()
    for part in (p for p in re.split(r"[\s,;]+", raw) if p):
        try:
            address, key = normalize_email(part)
        except ValueError as exc:
            raise validation(f"“{part[:80]}” is not a valid email address. {exc}") from exc
        if key not in seen:
            seen.add(key)
            addresses.append(address)
    if not addresses:
        raise validation("Enter at least one email address.")
    if len(addresses) > MAX_PREVIEW_RECIPIENTS:
        raise validation(f"Send a preview to at most {MAX_PREVIEW_RECIPIENTS} addresses at a time.")
    return addresses


@router.post("/email/test")
async def send_test(
    body: TestIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Email a preview of the invitation (saved wording, example link) to each address.

    It goes through the same queue and provider as real invitations, one message per
    recipient, so it also proves that the provider accepts mail.
    """
    no_store(response)
    addresses = _recipients(body.to)
    async with db.begin():
        programme = await active_programme(db)
        jobs = [await queue_test_email(db, address, ctx.actor, programme.id) for address in addresses]
    return {"ok": True, "queued": len(jobs), "recipients": addresses, "job_ids": [str(j.id) for j in jobs]}


@router.get("/email/jobs")
async def list_jobs(
    response: Response,
    status: Literal[
        "all", "queued", "processing", "accepted", "failed", "unknown", "superseded", "cancelled"
    ] = "all",
    kind: Literal["all", "invitation", "test"] = "all",
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    async with db.begin():
        programme = await active_programme(db)
        stmt = select(EmailJob).where(
            (EmailJob.programme_id == programme.id) | EmailJob.programme_id.is_(None)
        )
        if status != "all":
            stmt = stmt.where(EmailJob.status == status)
        if kind != "all":
            stmt = stmt.where(EmailJob.kind == kind)
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        jobs = (
            (
                await db.execute(
                    stmt.order_by(EmailJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
                )
            )
            .scalars()
            .all()
        )
    return {
        "total": total,
        "page": page,
        "items": [
            {
                "id": str(j.id),
                "kind": j.kind,
                "recipient": j.recipient,
                "status": j.status,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "next_attempt_at": j.next_attempt_at,
                "provider": j.provider,
                "provider_message_id": j.provider_message_id,
                "accepted_at": j.accepted_at,
                "last_error_code": j.last_error_code,
                "last_error": j.last_error,
                "credential_available": j.secret_ciphertext is not None,
                "created_at": j.created_at,
                "updated_at": j.updated_at,
            }
            for j in jobs
        ],
    }


@router.get("/email/jobs/{job_id}")
async def job_detail(
    job_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        job = await db.get(EmailJob, job_id)
        if job is None or job.programme_id not in (programme.id, None):
            raise not_found("Email job")
        attempts = (
            (
                await db.execute(
                    select(EmailAttempt)
                    .where(EmailAttempt.job_id == job_id)
                    .order_by(EmailAttempt.attempt_no)
                )
            )
            .scalars()
            .all()
        )
    return {
        "id": str(job.id),
        "status": job.status,
        "attempts": [
            {
                "attempt_no": a.attempt_no,
                "started_at": a.started_at,
                "finished_at": a.finished_at,
                "outcome": a.outcome,
                "error_code": a.error_code,
                "error_detail": a.error_detail,
                "provider_message_id": a.provider_message_id,
            }
            for a in attempts
        ],
    }


class RetryIn(Model):
    confirm_possible_duplicate: bool = False


@router.post("/email/jobs/{job_id}/retry")
async def retry_job(
    job_id: uuid.UUID,
    body: RetryIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        job = (
            await db.execute(
                select(EmailJob)
                .where(EmailJob.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        programme = await active_programme(db)
        if job is None or job.programme_id not in (programme.id, None):
            raise not_found("Email job")
        if job.status not in ("failed", "unknown"):
            raise conflict("NOT_RETRYABLE", "Only failed or unknown deliveries can be retried.")
        if job.status == "unknown" and not body.confirm_possible_duplicate:
            raise conflict(
                "POSSIBLE_DUPLICATE",
                "The provider may already have accepted this message. Retrying can send a second copy. Confirm to continue.",
            )
        if job.kind == "invitation" and job.secret_ciphertext is None:
            raise conflict(
                "CREDENTIAL_GONE",
                "This email's sign-in link copy has expired or been replaced. Send a new invitation instead.",
            )
        job.status = "queued"
        job.next_attempt_at = func.now()
        job.max_attempts = job.attempts + get_settings().email_max_attempts
        job.finished_at = None
        await audit(
            db,
            ctx.actor,
            "email.retried",
            programme_id=job.programme_id,
            target_type="email_job",
            target_id=job.id,
            details={"previous_status": "unknown" if body.confirm_possible_duplicate else "failed"},
        )
        await emit(db, job.programme_id, "email", {"j": str(job.id)})
    return {"ok": True}


@router.post("/email/jobs/retry-failed")
async def retry_failed(
    response: Response, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Requeue every failed job that still has its credential (never `unknown` ones)."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        result = await db.execute(
            text(
                "UPDATE email_jobs SET status = 'queued', next_attempt_at = now(), finished_at = NULL, "
                "max_attempts = attempts + :extra WHERE programme_id = :p AND status = 'failed' "
                "AND (kind <> 'invitation' OR secret_ciphertext IS NOT NULL) RETURNING id"
            ),
            {"p": programme.id, "extra": get_settings().email_max_attempts},
        )
        ids = list(result.scalars())
        await audit(
            db, ctx.actor, "email.retried_failed", programme_id=programme.id, details={"count": len(ids)}
        )
        await emit(db, programme.id, "email", {"n": len(ids)})
    return {"ok": True, "requeued": len(ids)}
