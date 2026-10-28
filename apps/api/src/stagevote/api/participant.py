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
from ..mailer.templates import stage_banner_gif
from ..security import is_token_shaped, new_token
from ..services import auth as auth_service
from ..services import snapshot, voting
from ..services.programmes import active_programme, asset_urls, public_programme, server_now
from .common import Model, clear_session_cookie, no_store, set_device_cookie, set_session_cookie

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


@router.get("/public/email-banner.gif")
async def email_banner() -> Response:
    """The gold look's animated email banner, for the console's invitation preview (real
    emails carry their own copy as an inline image). Public and decorative."""
    return Response(
        content=stage_banner_gif(),
        media_type="image/gif",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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


class LinkIn(Model):
    # A token that is not shaped like a link token is answered as an invalid link, not as
    # a validation error, so the page shows one message for every bad link. Only a value
    # far too long to be one is refused outright (422).
    token: str = Field(max_length=100)


def _device_cookie(request: Request) -> str | None:
    """This browser's device cookie, if it is one this server could have issued."""
    value = request.cookies.get(get_settings().participant_device_cookie_name)
    return value if value and is_token_shaped(value) else None


@router.post("/auth/link/check")
async def check_link(
    body: LinkIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """What the sign-in page should offer for a personal link. Never signs in.

    A browser without a device cookie gets one here, before it can tap the button, so a
    sign-in whose response is lost can be retried from the same browser.
    """
    check_origin(request)
    no_store(response)
    settings = get_settings()
    device = _device_cookie(request)
    state = await auth_service.check_link(
        db,
        body.token,
        presented_session=request.cookies.get(settings.participant_cookie_name),
        presented_device=device,
        ip=client_ip(request),
    )
    if device is None:
        set_device_cookie(response, new_token())
    return {"state": state}


@router.post("/auth/link")
async def enter_with_link(
    body: LinkIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Sign in with the personal link from the invitation email ("Enter the arena")."""
    check_origin(request)
    no_store(response)
    settings = get_settings()
    result = await auth_service.enter_with_link(
        db,
        body.token,
        presented_session=request.cookies.get(settings.participant_cookie_name),
        presented_device=_device_cookie(request),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if result.device_token is not None:
        set_device_cookie(response, result.device_token)
    if result.token is not None:
        set_session_cookie(response, settings.participant_cookie_name, result.token, result.expires_at)
    return {"ok": True, "reused_session": result.reused}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    check_origin(request)
    no_store(response)
    name = get_settings().participant_cookie_name
    await auth_service.participant_logout(db, request.cookies.get(name))
    # Only the session ends. The device cookie stays, so this browser can come back with
    # the link from the invitation email.
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
