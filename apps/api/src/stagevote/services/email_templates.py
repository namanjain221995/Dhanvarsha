"""Organiser-edited invitation wording and the shared context every email is rendered with."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..mailer.templates import (
    ACCENT_HEX,
    DEFAULT_INVITATION,
    LOGO_HEIGHT,
    LOGO_MAX_WIDTH,
    Wording,
    format_local,
)
from ..models import EmailTemplate, Programme


async def invitation_template(db: AsyncSession, programme_id: uuid.UUID | None) -> EmailTemplate | None:
    if programme_id is None:
        return None
    return (
        await db.execute(
            select(EmailTemplate)
            .where(EmailTemplate.programme_id == programme_id, EmailTemplate.kind == "invitation")
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def wording_of(row: EmailTemplate | None) -> Wording:
    if row is None:
        return DEFAULT_INVITATION
    return Wording(
        subject=row.subject,
        heading=row.heading,
        intro=row.intro,
        closing=row.closing,
        button_label=row.button_label,
    )


async def programme_context(
    db: AsyncSession, programme: Programme | None, settings: Settings
) -> tuple[dict[str, Any], str | None]:
    """Return (context shared by every email, storage key of the programme logo)."""
    tz = programme.timezone if programme else settings.programme_timezone
    logo_key = None
    logo_size: dict[str, int] = {}
    if programme and programme.logo_asset_id:
        row = (
            await db.execute(
                text("SELECT storage_key, width, height FROM assets WHERE id = :i"),
                {"i": programme.logo_asset_id},
            )
        ).first()
        if row:
            logo_key = row.storage_key
            # Shown at its own shape, never in a frame: 56 px high, or narrower and less
            # high when a very wide logo would pass LOGO_MAX_WIDTH.
            ratio = row.width / row.height if row.height else 1.0
            width, height = round(LOGO_HEIGHT * ratio), LOGO_HEIGHT
            if width > LOGO_MAX_WIDTH:
                width, height = LOGO_MAX_WIDTH, round(LOGO_MAX_WIDTH / ratio)
            logo_size = {"logo_width": max(width, 1), "logo_height": max(height, 1)}
    context: dict[str, Any] = {
        "programme_name": programme.name if programme else settings.product_name,
        "product_name": settings.product_name,
        "help_contact": programme.help_contact if programme else "",
        "accent": ACCENT_HEX.get(programme.accent if programme else "violet", ACCENT_HEX["violet"]),
        "timezone": tz,
        "opens_at": format_local(programme.login_opens_at, tz) if programme else None,
        "link": f"{settings.app_base_url}/",
        **logo_size,
    }
    return context, logo_key
