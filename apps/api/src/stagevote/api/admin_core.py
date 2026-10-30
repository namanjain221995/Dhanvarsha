"""Admin authentication, programme settings, uploads, audit log and health."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import AwareDatetime, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import AdminContext, check_origin, client_ip, current_admin, load_admin_session
from ..errors import AppError, validation
from ..models import ACCENTS, AuditEntry, Event, Programme
from ..services import auth as auth_service
from ..services.assets import store_image
from ..services.live import begin_transition, regenerate_schedule
from ..services.programmes import active_programme, asset_urls, login_phase, server_now
from ..state import audit, bump_state
from .common import Model, MultiLine, SingleLine, clear_session_cookie, no_store, set_session_cookie

router = APIRouter(prefix="/api/v1")
admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


# ---- health -------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Ready when the database answers and migrations are applied. Email is not required."""
    no_store(response)
    try:
        async with db.begin():
            version = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception as exc:
        response.status_code = 503
        return {"status": "unavailable", "database": "error", "error": type(exc).__name__}
    if version is None:
        response.status_code = 503
        return {"status": "unavailable", "database": "not migrated"}
    from ..live.hub import get_hub

    hub = get_hub()
    return {
        "status": "ok",
        "database": "ok",
        "migration": version,
        "live_listener": bool(hub and hub.listener_connected),
    }


# ---- admin auth ---------------------------------------------------------------


class AdminLoginIn(Model):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


@router.post("/admin/auth/login")
async def admin_login(
    body: AdminLoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    check_origin(request)
    no_store(response)
    token, admin_user = await auth_service.admin_login(
        db, body.email, body.password, client_ip(request), request.headers.get("user-agent", "")
    )
    settings = get_settings()
    async with db.begin():
        expires = await server_now(db)
    from datetime import timedelta

    set_session_cookie(
        response,
        settings.admin_cookie_name,
        token,
        expires + timedelta(hours=settings.admin_session_ttl_hours),
    )
    return {"ok": True, "email": admin_user.email}


@router.get("/admin/auth/session")
async def admin_session_status(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Whether this browser holds a valid organiser session (no error when it does not)."""
    no_store(response)
    try:
        await load_admin_session(
            db, request.cookies.get(get_settings().admin_cookie_name), client_ip(request)
        )
    except AppError:
        return {"signed_in": False}
    return {"signed_in": True}


@router.post("/admin/auth/logout")
async def admin_logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    check_origin(request)
    no_store(response)
    name = get_settings().admin_cookie_name
    await auth_service.admin_logout(db, request.cookies.get(name))
    clear_session_cookie(response, name)
    return {"ok": True}


@admin.get("/me")
async def admin_me(
    response: Response, ctx: AdminContext = Depends(current_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    settings = get_settings()
    async with db.begin():
        programme = await active_programme(db)
    return {
        "admin": {"id": str(ctx.admin_id), "email": ctx.email, "display_name": ctx.display_name},
        "csrf_token": ctx.csrf_token,
        "programme": {"id": str(programme.id), "name": programme.name, "timezone": programme.timezone},
        "environment": settings.app_env,
        "product_name": settings.product_name,
    }


# ---- programme settings ----------------------------------------------------


def programme_settings(programme: Programme, urls: dict[uuid.UUID, str], now: datetime) -> dict[str, Any]:
    return {
        "id": str(programme.id),
        "slug": programme.slug,
        "name": programme.name,
        "description": programme.description,
        "instructions": programme.instructions,
        "help_contact": programme.help_contact,
        "welcome_title": programme.welcome_title,
        "welcome_message": programme.welcome_message,
        "logo_asset_id": str(programme.logo_asset_id) if programme.logo_asset_id else None,
        "logo_url": urls.get(programme.logo_asset_id) if programme.logo_asset_id else None,
        "banner_asset_id": str(programme.banner_asset_id) if programme.banner_asset_id else None,
        "banner_url": urls.get(programme.banner_asset_id) if programme.banner_asset_id else None,
        "accent": programme.accent,
        "accents": list(ACCENTS),
        "timezone": programme.timezone,
        "login_opens_at": programme.login_opens_at,
        "login_closes_at": programme.login_closes_at,
        "login_phase": login_phase(programme, now),
        "countdown_enabled": programme.countdown_enabled,
        "run_mode": programme.run_mode,
        "schedule_suspended": programme.schedule_suspended,
        "schedule_suspended_at": programme.schedule_suspended_at,
        "schedule_revision": programme.schedule_revision,
        "preview_upcoming": programme.preview_upcoming,
        "publish_schedule": programme.publish_schedule,
        "ended_at": programme.ended_at,
        "settings_version": programme.settings_version,
        "live_version": programme.live_version,
        "server_time": now,
    }


@admin.get("/programme")
async def get_programme(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        now = await server_now(db)
        urls = await asset_urls(db, [programme.logo_asset_id, programme.banner_asset_id])
    return programme_settings(programme, urls, now)


class ProgrammePatch(Model):
    settings_version: int
    name: SingleLine | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = Field(default=None, max_length=4000)
    help_contact: SingleLine | None = Field(default=None, max_length=200)
    # The lobby's text while no round is open; "" brings back the built-in text.
    welcome_title: SingleLine | None = Field(default=None, max_length=120)
    welcome_message: MultiLine | None = Field(default=None, max_length=1000)
    logo_asset_id: uuid.UUID | None = None
    banner_asset_id: uuid.UUID | None = None
    clear_logo: bool = False
    clear_banner: bool = False
    accent: Literal["violet", "plum", "indigo", "teal", "rose", "amber", "gold"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    login_opens_at: AwareDatetime | None = None
    login_closes_at: AwareDatetime | None = None
    clear_login_window: bool = False
    countdown_enabled: bool | None = None
    run_mode: Literal["manual", "scheduled"] | None = None
    preview_upcoming: bool | None = None
    publish_schedule: bool | None = None


@admin.patch("/programme")
async def patch_programme(
    body: ProgrammePatch,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise validation("Choose a valid IANA timezone such as Asia/Kolkata.") from exc
    async with db.begin():
        current = await active_programme(db)
        # Settings changes can affect the access window used by votes and the schedule,
        # so they serialise with live transitions.
        programme, now = await begin_transition(db, current.id, None)
        if programme.settings_version != body.settings_version:
            raise AppError(
                "STALE_SETTINGS", "Someone else changed these settings. Reload and try again.", 409
            )
        changes: dict[str, Any] = {}
        fields = body.model_dump(exclude_unset=True)
        for key in (
            "name",
            "description",
            "instructions",
            "help_contact",
            "welcome_title",
            "welcome_message",
            "accent",
            "timezone",
            "countdown_enabled",
            "preview_upcoming",
            "publish_schedule",
        ):
            if key in fields and fields[key] is not None and getattr(programme, key) != fields[key]:
                changes[key] = {"from": getattr(programme, key), "to": fields[key]}
                setattr(programme, key, fields[key])
        for key, clear in (("logo_asset_id", body.clear_logo), ("banner_asset_id", body.clear_banner)):
            if clear:
                changes[key] = {"from": str(getattr(programme, key)), "to": None}
                setattr(programme, key, None)
            elif key in fields and fields[key] is not None:
                changes[key] = {"from": str(getattr(programme, key)), "to": str(fields[key])}
                setattr(programme, key, fields[key])

        opens = None if body.clear_login_window else fields.get("login_opens_at", programme.login_opens_at)
        closes = None if body.clear_login_window else fields.get("login_closes_at", programme.login_closes_at)
        if opens is not None and closes is not None and closes <= opens:
            raise validation("Access must close after it opens.")
        if (opens, closes) != (programme.login_opens_at, programme.login_closes_at):
            open_event = (
                await db.execute(
                    select(Event).where(Event.programme_id == programme.id, Event.status == "open")
                )
            ).scalar_one_or_none()
            if (
                open_event is not None
                and open_event.voting_deadline_at is not None
                and (closes is None or closes < open_event.voting_deadline_at)
            ):
                raise validation(
                    "An event is live with a deadline after this closing time. Change its deadline or close it first."
                )
            changes["login_window"] = {
                "from": [str(programme.login_opens_at), str(programme.login_closes_at)],
                "to": [str(opens), str(closes)],
            }
            programme.login_opens_at = opens
            programme.login_closes_at = closes

        schedule_changed = False
        if body.run_mode is not None and body.run_mode != programme.run_mode:
            changes["run_mode"] = {"from": programme.run_mode, "to": body.run_mode}
            programme.run_mode = body.run_mode
            programme.schedule_suspended = False
            programme.schedule_suspended_at = None
            schedule_changed = True
        if schedule_changed or "login_window" in changes:
            await regenerate_schedule(db, programme, now)
        if changes:
            programme.settings_version += 1
            await bump_state(db, programme.id, live=schedule_changed, reason="settings")
            # bump_state changed the versions with SQL: reload them so the response carries
            # the live_version admin screens must send with their next live action.
            await db.refresh(programme, ["state_version", "live_version"])
            await audit(db, ctx.actor, "programme.updated", programme_id=programme.id, details=changes)
        urls = await asset_urls(db, [programme.logo_asset_id, programme.banner_asset_id])
    return programme_settings(programme, urls, now)


# ---- uploads ------------------------------------------------------------------


@admin.post("/assets", status_code=201)
async def upload_asset(
    response: Response,
    kind: Literal["programme_logo", "banner", "event_logo", "nominee_photo"] = Form(...),
    file: UploadFile = File(...),
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    asset = await store_image(db, file, kind, ctx)
    return {
        "id": str(asset.id),
        "url": f"/media/{asset.storage_key}",
        "width": asset.width,
        "height": asset.height,
    }


# ---- audit --------------------------------------------------------------------


@admin.get("/audit")
async def list_audit(
    response: Response,
    page: int = 1,
    page_size: int = 50,
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    async with db.begin():
        programme = await active_programme(db)
        stmt = select(AuditEntry).where(
            (AuditEntry.programme_id == programme.id) | (AuditEntry.programme_id.is_(None))
        )
        count_stmt = (
            select(func.count())
            .select_from(AuditEntry)
            .where((AuditEntry.programme_id == programme.id) | (AuditEntry.programme_id.is_(None)))
        )
        if action:
            stmt = stmt.where(AuditEntry.action.like(f"{action}%"))
            count_stmt = count_stmt.where(AuditEntry.action.like(f"{action}%"))
        total = (await db.execute(count_stmt)).scalar_one()
        rows = (
            (
                await db.execute(
                    stmt.order_by(AuditEntry.id.desc()).offset((page - 1) * page_size).limit(page_size)
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
                "id": r.id,
                "at": r.created_at,
                "actor_type": r.actor_type,
                "actor": r.actor_label,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "details": r.details,
                "request_id": r.request_id,
            }
            for r in rows
        ],
    }
