"""The photo gallery (/gallery): the public page's API and the organisers' console.

See services/gallery.py. Nothing here touches events, votes, the voters' sign-in or sessions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import AdminContext, check_origin, client_ip, current_admin
from ..errors import AppError
from ..models import EmailJob, GalleryMedia, GalleryPerson, Participant, Programme
from ..services import gallery, media
from ..services.invitations import message_id_for
from ..services.programmes import active_programme, asset_urls
from ..state import audit, emit
from .admin_media import UploadIn, read_chunk
from .common import NO_STORE, Model, SingleLine, no_store, set_session_cookie

router = APIRouter(prefix="/api/v1/gallery")
admin = APIRouter(prefix="/api/v1/admin/gallery", dependencies=[Depends(current_admin)])

# Addresses set aside for examples and tests, which no real mail provider should be given.
_RESERVED_DOMAINS = {"example.com", "example.org", "example.net", "localhost"}
_RESERVED_ENDINGS = (".example", ".invalid", ".localhost", ".test")


def _remember(response: Response, token: str | None) -> None:
    if token:
        settings = get_settings()
        expires = datetime.now(UTC) + timedelta(days=settings.device_cookie_days)
        set_session_cookie(response, settings.gallery_cookie_name, token, expires)


async def _programme_view(db: AsyncSession, programme: Programme) -> dict[str, Any]:
    urls = await asset_urls(db, [programme.logo_asset_id])
    return {
        "name": programme.name,
        "logo_url": urls.get(programme.logo_asset_id) if programme.logo_asset_id else None,
    }


class LinkIn(Model):
    token: str = Field(default="", max_length=200)


async def _me(db: AsyncSession, request: Request, response: Response, token: str | None) -> dict[str, Any]:
    no_store(response)
    settings = get_settings()
    async with db.begin():
        programme = await active_programme(db)
        identity = await gallery.identify(db, request, programme, token)
        answer = {
            "programme": await _programme_view(db, programme),
            "person": gallery.me_view(identity.person),
            "suggested_name": identity.suggested_name,
            "known": identity.from_email,
            "max_image_bytes": settings.gallery_max_image_bytes,
            "max_video_bytes": settings.gallery_max_video_bytes,
        }
    _remember(response, identity.device_token)
    return answer


@router.get("/me")
async def get_me(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Who this browser is, and the programme's name and logo for the page's header."""
    return await _me(db, request, response, None)


@router.post("/me")
async def post_me(
    body: LinkIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """The same, for someone who came from the email: the token in its link says who they are."""
    check_origin(request)
    return await _me(db, request, response, body.token)


@router.post("/enrol")
async def post_enrol(
    request: Request,
    response: Response,
    name: str = Form(max_length=400),
    consent: bool = Form(default=False),
    token: str = Form(default="", max_length=200),
    selfie: UploadFile = File(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The form: a selfie and a name. Sent again later, it replaces them."""
    check_origin(request)
    no_store(response)
    person, device = await gallery.enrol(
        db, request, name=name, consent=consent, selfie=selfie, token=token or None
    )
    _remember(response, device)
    return {"person": gallery.me_view(person)}


@router.delete("/me", status_code=204)
async def delete_me(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """Forget this person: their name, picture and every record of their face."""
    async with db.begin():
        person = await gallery.require_person(db, request)
        person_id = person.id
    await gallery.delete_person(db, person_id)
    answer = Response(status_code=204, headers=NO_STORE)
    settings = get_settings()
    answer.delete_cookie(
        settings.gallery_cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return answer


# ---- uploading ---------------------------------------------------------------------------


@router.post("/uploads", status_code=201)
async def start_upload(
    body: UploadIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        person = await gallery.require_person(db, request)
    upload_id = await gallery.start_upload(db, person, body.name, body.size, body.content_type)
    return {"upload_id": upload_id, "chunk_size": media.CHUNK_SIZE}


@router.put("/uploads/{upload_id}")
async def put_chunk(
    upload_id: str,
    request: Request,
    response: Response,
    offset: int = Query(ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        person = await gallery.require_person(db, request)
    upload = await gallery.load_upload(upload_id, person)
    received = await media.write_chunk(upload, offset, await read_chunk(request))
    return {"received": received}


@router.post("/uploads/{upload_id}/complete", status_code=201)
async def complete_upload(
    upload_id: str, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """The whole file is here. The person only ever hears "uploaded": the rest is the worker's."""
    no_store(response)
    async with db.begin():
        person = await gallery.require_person(db, request)
    upload = await gallery.load_upload(upload_id, person)
    return await gallery.finish_upload(db, upload, person, client_ip(request))


@router.delete("/uploads/{upload_id}", status_code=204)
async def abort_upload(upload_id: str, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    async with db.begin():
        person = await gallery.require_person(db, request)
    upload = await gallery.load_upload(upload_id, person)
    await media.abort_upload(upload)
    return Response(status_code=204, headers=NO_STORE)


# ---- people ------------------------------------------------------------------------------


@router.get("/people")
async def get_people(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=gallery.PAGE_MAX),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        person = await gallery.require_person(db, request)
        return await gallery.list_people(
            db, person.programme_id, person.id, " ".join(q.split()), offset, limit
        )


@router.get("/people/{person_id}")
async def get_person(
    person_id: uuid.UUID,
    request: Request,
    response: Response,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=gallery.PAGE_MAX),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        person = await gallery.require_person(db, request)
        return await gallery.person_page(db, person.programme_id, person_id, person.id, offset, limit)


# ---- organisers ---------------------------------------------------------------------------


@admin.get("")
async def admin_summary(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        answer = await gallery.summary(db, programme.id)
        answer["email"] = await _email_counts(db, programme.id)
    return answer


@admin.get("/media")
async def admin_list_media(
    response: Response,
    status: gallery.MediaFilter = Query(default="all"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=gallery.PAGE_MAX),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        return await gallery.admin_media(db, programme.id, status, offset, limit)


@admin.delete("/media/{media_id}", status_code=204)
async def admin_delete_media(
    media_id: uuid.UUID, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> Response:
    async with db.begin():
        programme_id = (await active_programme(db)).id
    if not await gallery.delete_media(db, programme_id, media_id, ctx.actor):
        raise AppError("NOT_FOUND", "This file was not found.", 404)
    return Response(status_code=204, headers=NO_STORE)


@admin.post("/media/{media_id}/retry")
async def admin_retry_media(
    media_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        item = await db.get(GalleryMedia, media_id)
        if item is None or item.programme_id != programme.id:
            raise AppError("NOT_FOUND", "This file was not found.", 404)
        if item.status != "failed" or not item.source_key:
            raise AppError("CONFLICT", "Only a failed file that is still stored can be tried again.", 409)
        item.status = "queued"
        item.attempts = 0
        item.last_error = None
        await db.execute(
            text("UPDATE gallery_media SET next_attempt_at = now() WHERE id = :m"), {"m": media_id}
        )
    return {"ok": True}


@admin.get("/people")
async def admin_people(
    response: Response,
    q: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=gallery.PAGE_MAX),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        return await gallery.list_people(db, programme.id, None, " ".join(q.split()), offset, limit)


class PersonIn(Model):
    name: SingleLine = Field(max_length=gallery.NAME_MAX)


@admin.patch("/people/{person_id}")
async def admin_rename_person(
    person_id: uuid.UUID,
    body: PersonIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        person = await db.get(GalleryPerson, person_id)
        if person is None or person.programme_id != programme.id:
            raise AppError("NOT_FOUND", "This person was not found.", 404)
        person.name = " ".join(body.name.split())
        await audit(
            db,
            ctx.actor,
            "gallery.person_renamed",
            programme_id=programme.id,
            target_type="gallery_person",
            target_id=person_id,
        )
    return {"ok": True}


class MergeIn(Model):
    into: uuid.UUID


@admin.post("/people/{person_id}/merge")
async def admin_merge_person(
    person_id: uuid.UUID,
    body: MergeIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The same person twice: everything of this one moves to `into`."""
    no_store(response)
    if person_id == body.into:
        raise AppError("VALIDATION_ERROR", "Choose someone else to merge with.", 422)
    async with db.begin():
        programme = await active_programme(db)
        await gallery.merge_people(db, person_id, body.into)
        await audit(
            db,
            ctx.actor,
            "gallery.person_merged",
            programme_id=programme.id,
            target_type="gallery_person",
            target_id=body.into,
            details={"merged": str(person_id)},
        )
    return {"ok": True}


@admin.delete("/people/{person_id}", status_code=204)
async def admin_delete_person(
    person_id: uuid.UUID, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> Response:
    async with db.begin():
        programme = await active_programme(db)
        person = await db.get(GalleryPerson, person_id)
        if person is None or person.programme_id != programme.id:
            raise AppError("NOT_FOUND", "This person was not found.", 404)
        await audit(
            db,
            ctx.actor,
            "gallery.person_deleted",
            programme_id=programme.id,
            target_type="gallery_person",
            target_id=person_id,
        )
    await gallery.delete_person(db, person_id)
    return Response(status_code=204, headers=NO_STORE)


# ---- the gallery email ----------------------------------------------------------------------

# A mail already handed over, or on its way. A failed one may be sent again.
_ALREADY = ("queued", "processing", "accepted", "unknown")


def _is_reserved(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    return domain in _RESERVED_DOMAINS or domain.endswith(_RESERVED_ENDINGS)


async def _recipients(db: AsyncSession, programme_id: uuid.UUID) -> tuple[list[Participant], int, int]:
    """(participants to email now, those with one already, those skipped for a reserved address)."""
    rows = list(
        (
            await db.execute(
                select(Participant).where(
                    Participant.programme_id == programme_id, Participant.disabled_at.is_(None)
                )
            )
        ).scalars()
    )
    done = set(
        (
            await db.execute(
                select(EmailJob.participant_id).where(
                    EmailJob.programme_id == programme_id,
                    EmailJob.kind == "gallery",
                    EmailJob.status.in_(_ALREADY),
                )
            )
        ).scalars()
    )
    # Test stacks deliver to their own inbox, where a reserved address does no harm.
    skip_reserved = get_settings().email_provider != "mailpit"
    send: list[Participant] = []
    reserved = 0
    already = 0
    for participant in rows:
        if participant.id in done:
            already += 1
        elif skip_reserved and _is_reserved(participant.email):
            reserved += 1
        else:
            send.append(participant)
    return send, already, reserved


async def _email_counts(db: AsyncSession, programme_id: uuid.UUID) -> dict[str, int]:
    send, already, reserved = await _recipients(db, programme_id)
    return {"to_send": len(send), "already": already, "reserved": reserved}


@admin.post("/email/send")
async def send_gallery_email(
    response: Response, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Email everyone who has not had it yet. Voters' sign-in links are not touched."""
    no_store(response)
    settings = get_settings()
    async with db.begin():
        programme = await active_programme(db)
        send, already, reserved = await _recipients(db, programme.id)
        for participant in send:
            job_id = uuid.uuid4()
            db.add(
                EmailJob(
                    id=job_id,
                    programme_id=programme.id,
                    participant_id=participant.id,
                    kind="gallery",
                    recipient=participant.email,
                    status="queued",
                    max_attempts=settings.email_max_attempts,
                    message_id=message_id_for(job_id),
                    created_by_admin_id=ctx.admin_id,
                )
            )
        await db.flush()
        await audit(
            db,
            ctx.actor,
            "gallery.email_sent",
            programme_id=programme.id,
            details={"queued": len(send), "already": already, "reserved": reserved},
        )
        if send:
            await emit(db, programme.id, "email", {"n": len(send)})
    return {"queued": len(send), "already": already, "reserved": reserved}
