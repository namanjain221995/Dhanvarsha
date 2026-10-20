"""Admin participant management: import, search, invitations and account actions.

Default admin views show completion status (how many ballots someone voted on), never
which nominee a person chose.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import AppError, conflict, not_found, validation
from ..models import EmailJob, Participant
from ..services.auth import lock_participant
from ..services.invitations import (
    issue_invitations,
    lock_participants,
    revoke_participant_sessions,
    supersede_jobs,
)
from ..services.participants import commit_import, preview_import
from ..services.programmes import active_programme, server_now
from ..state import audit, emit
from .common import Model, SingleLine, no_store

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])

INVITE_BATCH = 100


class ImportIn(Model):
    text: str = Field(min_length=1, max_length=1_000_000)
    send_invitations: bool = False


def _summary(rows: list[Any]) -> dict[str, int]:
    out = {"new": 0, "invalid": 0, "duplicate_in_input": 0, "already_imported": 0}
    for row in rows:
        out[row.status] = out.get(row.status, 0) + 1
    return out


async def _upload_text(file: UploadFile) -> str:
    data = await file.read(2_000_001)
    if len(data) > 2_000_000:
        raise AppError("FILE_TOO_LARGE", "CSV files must be 2 MB or smaller.", 413)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise validation("Save the CSV as UTF-8 and try again.") from exc


@router.post("/participants/import/preview")
async def import_preview(
    body: ImportIn, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        rows = await preview_import(db, programme.id, body.text)
    return {"summary": _summary(rows), "rows": [r.public() for r in rows]}


@router.post("/participants/import/preview-file")
async def import_preview_file(
    response: Response, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    content = await _upload_text(file)
    async with db.begin():
        programme = await active_programme(db)
        rows = await preview_import(db, programme.id, content)
    return {"summary": _summary(rows), "rows": [r.public() for r in rows], "text": content}


@router.post("/participants/import/commit")
async def import_commit(
    body: ImportIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        rows, created = await commit_import(db, programme.id, body.text)
        await audit(
            db,
            ctx.actor,
            "participants.imported",
            programme_id=programme.id,
            details={"created": len(created), "summary": _summary(rows)},
        )
    invited = 0
    if body.send_invitations and created:
        invited = await _invite(db, programme.id, created, ctx)
    return {
        "summary": _summary(rows),
        "created": len(created),
        "invited": invited,
        "rows": [r.public() for r in rows],
    }


async def _invite(db: AsyncSession, programme_id: uuid.UUID, ids: list[uuid.UUID], ctx: AdminContext) -> int:
    total = 0
    for start in range(0, len(ids), INVITE_BATCH):
        chunk = ids[start : start + INVITE_BATCH]
        async with db.begin():
            people = [
                p
                for p in await lock_participants(db, programme_id, chunk)
                if p.activated_at is None and p.disabled_at is None
            ]
            now = await server_now(db)
            jobs = await issue_invitations(db, people, ctx.actor, now)
            total += len(jobs)
    return total


_STATUS_FILTERS = {
    "all": "TRUE",
    "not_invited": "p.last_invited_at IS NULL AND p.activated_at IS NULL AND p.disabled_at IS NULL",
    "invited": "p.last_invited_at IS NOT NULL AND p.activated_at IS NULL AND p.disabled_at IS NULL",
    "activated": "p.activated_at IS NOT NULL AND p.disabled_at IS NULL",
    "disabled": "p.disabled_at IS NOT NULL",
    "signed_in": "s.id IS NOT NULL",
    "invite_failed": "j.status IN ('failed', 'unknown')",
}


@router.get("/participants")
async def list_participants(
    response: Response,
    q: str = "",
    status: Literal[
        "all", "not_invited", "invited", "activated", "disabled", "signed_in", "invite_failed"
    ] = "all",
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    async with db.begin():
        programme = await active_programme(db)
        base = f"""
            FROM participants p
            LEFT JOIN LATERAL (
                SELECT id, expires_at, created_at FROM participant_sessions
                WHERE participant_id = p.id AND revoked_at IS NULL AND expires_at > now()
                LIMIT 1
            ) s ON TRUE
            LEFT JOIN LATERAL (
                SELECT status, kind, updated_at FROM email_jobs
                WHERE participant_id = p.id AND kind = 'invitation'
                ORDER BY created_at DESC LIMIT 1
            ) j ON TRUE
            WHERE p.programme_id = :prog
              AND ({_STATUS_FILTERS[status]})
              AND (:q = '' OR p.email_normalized LIKE :like OR lower(p.display_name) LIKE :like
                   OR p.user_code ILIKE :like)
        """
        params = {"prog": programme.id, "q": q.strip(), "like": f"%{q.strip().lower()}%"}
        total = (await db.execute(text("SELECT count(*) " + base), params)).scalar_one()
        rows = (
            await db.execute(
                text(
                    """
                    SELECT p.id, p.email, p.display_name, p.user_code, p.activated_at, p.disabled_at,
                           p.last_invited_at, p.created_at, p.synthetic,
                           s.id AS session_id, s.created_at AS session_started_at,
                           j.status AS email_status, j.kind AS email_kind, j.updated_at AS email_updated_at,
                           (SELECT count(*) FROM votes v JOIN events ev ON ev.id = v.event_id
                            WHERE v.participant_id = p.id AND ev.archived_at IS NULL) AS votes
                    """
                    + base
                    + " ORDER BY p.created_at, p.email_normalized LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).all()
        opened_ballots = (
            await db.execute(
                text(
                    "SELECT count(*) FROM ballots b JOIN events e ON e.id = b.event_id "
                    "WHERE e.programme_id = :prog AND e.status <> 'upcoming' AND e.archived_at IS NULL"
                ),
                {"prog": programme.id},
            )
        ).scalar_one()
        totals = (
            await db.execute(
                text(
                    """
                    SELECT count(*) AS all,
                           count(*) FILTER (WHERE p.last_invited_at IS NULL AND p.activated_at IS NULL
                                            AND p.disabled_at IS NULL) AS not_invited,
                           count(*) FILTER (WHERE p.last_invited_at IS NOT NULL AND p.activated_at IS NULL
                                            AND p.disabled_at IS NULL) AS invited,
                           count(*) FILTER (WHERE p.activated_at IS NOT NULL AND p.disabled_at IS NULL) AS activated,
                           count(*) FILTER (WHERE p.disabled_at IS NOT NULL) AS disabled,
                           count(*) FILTER (WHERE EXISTS (
                               SELECT 1 FROM participant_sessions s WHERE s.participant_id = p.id
                               AND s.revoked_at IS NULL AND s.expires_at > now())) AS signed_in,
                           count(*) FILTER (WHERE (
                               SELECT j.status FROM email_jobs j WHERE j.participant_id = p.id
                               AND j.kind = 'invitation'
                               ORDER BY j.created_at DESC LIMIT 1) IN ('failed', 'unknown')) AS invite_failed
                    FROM participants p WHERE p.programme_id = :prog
                    """
                ),
                {"prog": programme.id},
            )
        ).one()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "opened_ballots": int(opened_ballots),
        "counts": dict(totals._mapping),
        "items": [
            {
                "id": str(r.id),
                "email": r.email,
                "display_name": r.display_name,
                "user_code": r.user_code,
                "activated": r.activated_at is not None,
                "activated_at": r.activated_at,
                "disabled": r.disabled_at is not None,
                "invited_at": r.last_invited_at,
                "signed_in": r.session_id is not None,
                "session_started_at": r.session_started_at,
                "last_email": {
                    "status": r.email_status,
                    "kind": r.email_kind,
                    "updated_at": r.email_updated_at,
                }
                if r.email_status
                else None,
                "ballots_voted": int(r.votes),
                "synthetic": r.synthetic,
            }
            for r in rows
        ],
    }


async def _participant(db: AsyncSession, participant_id: uuid.UUID) -> Participant:
    programme = await active_programme(db)
    participant = (
        await db.execute(
            select(Participant)
            .where(Participant.id == participant_id, Participant.programme_id == programme.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if participant is None:
        raise not_found("Participant")
    return participant


@router.get("/participants/{participant_id}")
async def participant_detail(
    participant_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        p = (
            await db.execute(
                select(Participant).where(
                    Participant.id == participant_id, Participant.programme_id == programme.id
                )
            )
        ).scalar_one_or_none()
        if p is None:
            raise not_found("Participant")
        jobs = (
            (
                await db.execute(
                    select(EmailJob)
                    .where(EmailJob.participant_id == p.id)
                    .order_by(EmailJob.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        sessions = (
            await db.execute(
                text(
                    "SELECT id, created_at, last_seen_at, expires_at, revoked_at, revoke_reason, user_agent "
                    "FROM participant_sessions WHERE participant_id = :p ORDER BY created_at DESC LIMIT 10"
                ),
                {"p": p.id},
            )
        ).all()
        votes = (
            await db.execute(
                text(
                    "SELECT e.name AS event, b.title AS ballot, v.created_at, v.receipt_code FROM votes v "
                    "JOIN ballots b ON b.id = v.ballot_id JOIN events e ON e.id = v.event_id "
                    "WHERE v.participant_id = :p ORDER BY v.created_at"
                ),
                {"p": p.id},
            )
        ).all()
    return {
        "participant": {
            "id": str(p.id),
            "email": p.email,
            "display_name": p.display_name,
            "user_code": p.user_code,
            "activated_at": p.activated_at,
            "disabled_at": p.disabled_at,
            "invited_at": p.last_invited_at,
            "created_at": p.created_at,
        },
        "email_jobs": [
            {
                "id": str(j.id),
                "kind": j.kind,
                "status": j.status,
                "attempts": j.attempts,
                "created_at": j.created_at,
                "accepted_at": j.accepted_at,
                "last_error": j.last_error,
            }
            for j in jobs
        ],
        "sessions": [
            {
                "id": str(s.id),
                "created_at": s.created_at,
                "last_seen_at": s.last_seen_at,
                "expires_at": s.expires_at,
                "revoked_at": s.revoked_at,
                "revoke_reason": s.revoke_reason,
                "user_agent": s.user_agent,
            }
            for s in sessions
        ],
        # Completion only: which ballots were voted on, never which nominee was chosen.
        "ballots_voted": [
            {"event": v.event, "ballot": v.ballot, "at": v.created_at, "receipt_code": v.receipt_code}
            for v in votes
        ],
    }


class ParticipantPatch(Model):
    display_name: SingleLine = Field(max_length=120)


@router.patch("/participants/{participant_id}")
async def patch_participant(
    participant_id: uuid.UUID,
    body: ParticipantPatch,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        p = await _participant(db, participant_id)
        before = p.display_name
        p.display_name = body.display_name
        await audit(
            db,
            ctx.actor,
            "participant.renamed",
            programme_id=p.programme_id,
            target_type="participant",
            target_id=p.id,
            details={"from": before, "to": body.display_name},
        )
    return {"ok": True}


@router.post("/participants/{participant_id}/invite")
async def invite_one(
    participant_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Send the invitation (email address + new password). Re-sending replaces the
    password, so the previous one stops working, and signs out any open session."""
    no_store(response)
    async with db.begin():
        p = await _participant(db, participant_id)
        if p.disabled_at is not None:
            raise conflict("ACCOUNT_DISABLED", "Enable this account before inviting it.")
        resend = p.last_invited_at is not None
        now = await server_now(db)
        await issue_invitations(db, [p], ctx.actor, now)
    return {"ok": True, "resent": resend}


class DisableIn(Model):
    reason: str = Field(default="", max_length=300)


@router.post("/participants/{participant_id}/disable")
async def disable_one(
    participant_id: uuid.UUID,
    body: DisableIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        p = await lock_participant(db, (await _participant(db, participant_id)).id)
        if p.disabled_at is not None:
            return {"ok": True}
        now = await server_now(db)
        p.disabled_at = now
        revoked = await revoke_participant_sessions(db, p, "disabled", now)
        await supersede_jobs(db, p.id, ("invitation",))
        await audit(
            db,
            ctx.actor,
            "participant.disabled",
            programme_id=p.programme_id,
            target_type="participant",
            target_id=p.id,
            details={"reason": body.reason, "sessions_revoked": revoked},
        )
    return {"ok": True}


@router.post("/participants/{participant_id}/enable")
async def enable_one(
    participant_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        p = await _participant(db, participant_id)
        if p.disabled_at is None:
            return {"ok": True}
        p.disabled_at = None
        await audit(
            db,
            ctx.actor,
            "participant.enabled",
            programme_id=p.programme_id,
            target_type="participant",
            target_id=p.id,
        )
    return {"ok": True}


@router.post("/participants/{participant_id}/reset-session")
async def reset_session(
    participant_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """End the participant's active browser session so they can sign in elsewhere."""
    no_store(response)
    async with db.begin():
        p = await _participant(db, participant_id)
        revoked = await revoke_participant_sessions(db, p, "admin_reset", await server_now(db))
        await audit(
            db,
            ctx.actor,
            "participant.session_reset",
            programme_id=p.programme_id,
            target_type="participant",
            target_id=p.id,
            details={"sessions_revoked": revoked},
        )
    return {"ok": True, "sessions_revoked": revoked}


@router.delete("/participants/{participant_id}")
async def delete_one(
    participant_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Remove a mistaken import. Only possible before any invitation or sign-in."""
    no_store(response)
    async with db.begin():
        p = await _participant(db, participant_id)
        dependents = (
            await db.execute(
                text(
                    "SELECT (SELECT count(*) FROM email_jobs WHERE participant_id = :p) + "
                    "(SELECT count(*) FROM participant_sessions WHERE participant_id = :p) + "
                    "(SELECT count(*) FROM recovery_tokens WHERE participant_id = :p) + "
                    "(SELECT count(*) FROM votes WHERE participant_id = :p)"
                ),
                {"p": p.id},
            )
        ).scalar_one()
        if dependents:
            raise conflict(
                "HAS_HISTORY",
                "This participant has invitations, sessions or votes. Disable the account instead.",
            )
        await db.delete(p)
        await audit(
            db,
            ctx.actor,
            "participant.deleted",
            programme_id=p.programme_id,
            target_type="participant",
            target_id=participant_id,
            details={"email": p.email},
        )
    return {"ok": True}


class BulkInviteIn(Model):
    scope: Literal["not_invited", "selected", "not_activated"] = "not_invited"
    participant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)


@router.post("/invitations/send")
async def send_invitations(
    body: BulkInviteIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Queue invitations. `not_activated` re-sends to everyone invited who has not signed in
    yet: each gets a new password and the previous one stops working."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        if body.scope == "selected":
            ids = body.participant_ids
        else:
            condition = "last_invited_at IS NULL" if body.scope == "not_invited" else "TRUE"
            ids = list(
                (
                    await db.execute(
                        text(
                            f"SELECT id FROM participants WHERE programme_id = :p AND activated_at IS NULL "
                            f"AND disabled_at IS NULL AND {condition} ORDER BY created_at"
                        ),
                        {"p": programme.id},
                    )
                ).scalars()
            )
    count = await _invite(db, programme.id, ids, ctx)
    if count:
        async with db.begin():
            await emit(db, programme.id, "email", {"n": count})
    return {"ok": True, "queued": count}


MAX_DIRECT_INVITES = 50


class SendToIn(Model):
    # Addresses separated by commas or new lines; "Name <email>" or "email, name" on its own
    # line adds a display name.
    to: str = Field(min_length=3, max_length=50_000)


@router.post("/invitations/send-to")
async def send_to(
    body: SendToIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Invite people by email address in one step. Addresses not on the list yet become
    participants, and everyone listed gets a real invitation with their own password.
    Someone invited before gets a new password (the old one stops working). All or
    nothing: one invalid address sends nothing."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        rows = await preview_import(db, programme.id, body.to)
        invalid = [r for r in rows if r.status == "invalid"]
        if invalid:
            listed = ", ".join(f"“{(r.email or r.input)[:80]}”" for r in invalid[:5])
            raise validation(
                f"Not a valid email address: {listed}. Nothing was sent.",
                {"invalid": [r.input[:200] for r in invalid]},
            )
        keys = sorted({r.email_normalized for r in rows if r.email_normalized})
        if not keys:
            raise validation("Enter at least one email address.")
        if len(keys) > MAX_DIRECT_INVITES:
            raise validation(
                f"Send to at most {MAX_DIRECT_INVITES} addresses here. "
                "For a longer list, use Add people on the Participants page."
            )
        imported, created = await commit_import(db, programme.id, body.to)
        if created:
            await audit(
                db,
                ctx.actor,
                "participants.imported",
                programme_id=programme.id,
                details={"created": len(created), "summary": _summary(imported)},
            )
        ids = list(
            (
                await db.execute(
                    select(Participant.id).where(
                        Participant.programme_id == programme.id, Participant.email_normalized.in_(keys)
                    )
                )
            ).scalars()
        )
        people = await lock_participants(db, programme.id, ids)
        disabled = sorted(p.email for p in people if p.disabled_at is not None)
        active = [p for p in people if p.disabled_at is None]
        resent = sum(1 for p in active if p.last_invited_at is not None)
        jobs = await issue_invitations(db, active, ctx.actor, await server_now(db))
        if jobs:
            await emit(db, programme.id, "email", {"n": len(jobs)})
    return {
        "ok": True,
        "created": len(created),
        "invited": len(jobs),
        "resent": resent,
        "disabled": disabled,
        "recipients": sorted(p.email for p in active),
    }
