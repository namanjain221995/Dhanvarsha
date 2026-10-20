"""Programme lookup, login-window phases and shared presentation helpers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import AppError
from ..models import Asset, Programme


async def active_programme(db: AsyncSession, *, for_update: bool = False) -> Programme:
    stmt = select(Programme).where(Programme.is_active.is_(True))
    if for_update:
        stmt = stmt.with_for_update()
    programme = (await db.execute(stmt)).scalar_one_or_none()
    if programme is None:
        raise AppError("PROGRAMME_NOT_CONFIGURED", "No programme is set up yet.", 503)
    return programme


async def programme_by_id(
    db: AsyncSession, programme_id: uuid.UUID, *, for_update: bool = False
) -> Programme:
    stmt = select(Programme).where(Programme.id == programme_id)
    if for_update:
        stmt = stmt.with_for_update()
    programme = (await db.execute(stmt)).scalar_one_or_none()
    if programme is None:
        raise AppError("PROGRAMME_NOT_FOUND", "Programme not found.", 404)
    return programme


def login_phase(programme: Programme, now: datetime) -> str:
    """before_open | open | closed — evaluated with server time only."""
    if programme.login_opens_at is None or now < programme.login_opens_at:
        return "before_open"
    if programme.login_closes_at is not None and now >= programme.login_closes_at:
        return "closed"
    return "open"


def media_url(storage_key: str | None) -> str | None:
    return f"/media/{storage_key}" if storage_key else None


async def asset_urls(db: AsyncSession, ids: list[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    rows = (await db.execute(select(Asset.id, Asset.storage_key).where(Asset.id.in_(wanted)))).all()
    return {row.id: f"/media/{row.storage_key}" for row in rows}


async def server_now(db: AsyncSession) -> datetime:
    value: datetime = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    return value


def public_programme(programme: Programme, urls: dict[uuid.UUID, str], now: datetime) -> dict[str, Any]:
    return {
        "id": str(programme.id),
        "name": programme.name,
        "description": programme.description,
        "instructions": programme.instructions,
        "help_contact": programme.help_contact,
        "logo_url": urls.get(programme.logo_asset_id) if programme.logo_asset_id else None,
        "banner_url": urls.get(programme.banner_asset_id) if programme.banner_asset_id else None,
        "accent": programme.accent,
        "timezone": programme.timezone,
        "login_opens_at": programme.login_opens_at,
        "login_closes_at": programme.login_closes_at,
        "countdown_enabled": programme.countdown_enabled,
        "login_phase": login_phase(programme, now),
        "server_time": now,
    }
