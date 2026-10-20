"""Public programme info, participant authentication, snapshots and voting."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import ParticipantContext, check_origin, client_ip, current_participant, load_participant_session
from ..errors import AppError
from ..services import auth as auth_service
from ..services import snapshot, voting
from ..services.programmes import active_programme, asset_urls, public_programme, server_now
from .common import Model, clear_session_cookie, no_store, set_session_cookie

router = APIRouter(prefix="/api/v1")


# ---- public -------------------------------------------------------------------


@router.get("/public/programme")
async def get_public_programme(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Branding and the login window. Contains nothing private."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        now = await server_now(db)
        urls = await asset_urls(db, [programme.logo_asset_id, programme.banner_asset_id])
    data = public_programme(programme, urls, now)
    data.pop("instructions", None)
    data["product_name"] = get_settings().product_name
    return data


@router.get("/time")
async def get_time(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        return {"server_time": await server_now(db)}


@router.get("/auth/session")
async def session_status(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Whether this browser holds a valid participant session (no error when it does not)."""
    no_store(response)
    try:
        await load_participant_session(
            db, request.cookies.get(get_settings().participant_cookie_name), client_ip(request)
        )
    except AppError as exc:
        return {"signed_in": False, "reason": exc.code}
    return {"signed_in": True}


# ---- authentication -----------------------------------------------------------


class LoginIn(Model):
    user_id: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
async def login(
    body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    check_origin(request)
    no_store(response)
    settings = get_settings()
    result = await auth_service.participant_login(
        db,
        body.user_id,
        body.password,
        presented_token=request.cookies.get(settings.participant_cookie_name),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if result.token is not None:
        async with db.begin():
            session = await db.get(auth_service.ParticipantSession, result.session_id)
            assert session is not None
            set_session_cookie(response, settings.participant_cookie_name, result.token, session.expires_at)
    return {"ok": True, "reused_session": result.reused}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    check_origin(request)
    no_store(response)
    name = get_settings().participant_cookie_name
    await auth_service.participant_logout(db, request.cookies.get(name))
    clear_session_cookie(response, name)
    return {"ok": True}


# ---- signed-in participant ----------------------------------------------------


@router.get("/me/snapshot")
async def get_snapshot(
    response: Response,
    ctx: ParticipantContext = Depends(current_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    return await snapshot.participant_snapshot(db, ctx)


@router.get("/me/events/{event_id}")
async def get_event(
    event_id: uuid.UUID,
    response: Response,
    ctx: ParticipantContext = Depends(current_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    return await snapshot.event_detail(db, ctx, event_id)


class VoteIn(Model):
    nominee_id: uuid.UUID


@router.post("/me/ballots/{ballot_id}/vote")
async def submit_vote(
    ballot_id: uuid.UUID,
    body: VoteIn,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", default=""),
    ctx: ParticipantContext = Depends(current_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    receipt, created = await voting.submit_vote(db, ctx, ballot_id, body.nominee_id, idempotency_key)
    response.status_code = 201 if created else 200
    return {"receipt": receipt.public(), "replayed": not created}


@router.get("/me/ballots/{ballot_id}/receipt")
async def get_receipt(
    ballot_id: uuid.UUID,
    response: Response,
    ctx: ParticipantContext = Depends(current_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    receipt = await voting.get_receipt(db, ctx, ballot_id)
    if receipt is None:
        raise AppError("NO_VOTE", "No vote is recorded for this ballot.", 404)
    return {"receipt": receipt.public()}
