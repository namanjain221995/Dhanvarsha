"""The opening showcase page (/opening): the public page and the organiser's editor."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import AppError, validation
from ..models import Programme
from ..services import opening
from ..services.programmes import active_programme, asset_urls
from ..state import audit
from .common import Model, MultiLine, SingleLine, no_store

router = APIRouter(prefix="/api/v1")
admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


# ---- public -------------------------------------------------------------------


@router.get("/public/opening")
async def get_public_opening(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The showcase page as everyone sees it. Contains nothing private."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        page = opening.resolve_page(programme)
        urls = await asset_urls(db, opening.page_assets(page))
    # The built-in page follows the programme's name and description, so the programme's
    # own updated_at is the latest moment this page can have changed.
    return opening.public_view(page, urls, programme.updated_at)


# ---- organiser editor -----------------------------------------------------------

CardId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=opening.CARD_ID_MAX, pattern=opening.CARD_ID_PATTERN),
]


class CardIn(Model):
    id: CardId
    title: SingleLine = Field(min_length=1, max_length=opening.CARD_TITLE_MAX)
    icon: str = Field(max_length=40)
    image_asset_id: uuid.UUID | None = None
    link: str = Field(default="", max_length=opening.LINK_MAX)
    # Accepted and ignored, so the editor can send back a card as it loaded it.
    image_url: str | None = Field(default=None, max_length=1000, exclude=True)

    @field_validator("icon")
    @classmethod
    def _known_icon(cls, value: str) -> str:
        if value not in opening.ICON_KEYS:
            raise ValueError("Choose one of the listed icons.")
        return value

    @field_validator("link")
    @classmethod
    def _safe_link(cls, value: str) -> str:
        if not opening.link_is_safe(value):
            raise ValueError(
                "Use a web address starting with http:// or https://, or a page of this site starting with /."
            )
        return value


class OpeningIn(Model):
    """The whole page: saving replaces every value."""

    settings_version: int
    title: SingleLine = Field(min_length=1, max_length=opening.TITLE_MAX)
    year: SingleLine = Field(max_length=opening.YEAR_MAX)
    tagline: MultiLine = Field(max_length=opening.TAGLINE_MAX)
    button_label: SingleLine = Field(min_length=1, max_length=opening.BUTTON_LABEL_MAX)
    logo_asset_id: uuid.UUID | None = None
    cards: list[CardIn] = Field(max_length=opening.MAX_CARDS)
    logo_url: str | None = Field(default=None, max_length=1000, exclude=True)  # ignored, like image_url


class VersionIn(Model):
    settings_version: int


async def _editor(db: AsyncSession, programme: Programme) -> dict[str, Any]:
    page = opening.resolve_page(programme)
    urls = await asset_urls(db, opening.page_assets(page))
    return {
        "opening": opening.editor_view(page, urls),
        "defaults": opening.editor_view(opening.default_page(programme), {}),
        "icons": list(opening.ICON_KEYS),
        "settings_version": programme.settings_version,
    }


async def _programme_at(db: AsyncSession, settings_version: int) -> Programme:
    """The active programme, locked, if nobody changed its settings since `settings_version`.

    The page shares the programme's settings_version, so it serialises with the programme
    settings form on the programme row. It changes nothing a vote reads, so unlike that
    form it does not take the live lock (and never waits for votes).
    """
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    programme = (
        await db.execute(
            select(Programme)
            .where(Programme.is_active.is_(True))
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if programme is None:
        raise AppError("PROGRAMME_NOT_CONFIGURED", "No programme is set up yet.", 503)
    if programme.settings_version != settings_version:
        raise AppError(
            "STALE_SETTINGS", "Someone else changed this page. Reload to see the latest version.", 409
        )
    return programme


@admin.get("/opening")
async def get_opening(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        return await _editor(db, programme)


@admin.put("/opening")
async def save_opening(
    body: OpeningIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    page = body.model_dump(exclude={"settings_version"})
    opening.check_card_ids(page)
    async with db.begin():
        programme = await _programme_at(db, body.settings_version)
        await opening.check_assets(db, page)
        programme.opening = opening.stored_page(page)
        programme.settings_version += 1
        await audit(
            db, ctx.actor, "opening.updated", programme_id=programme.id, details={"cards": len(page["cards"])}
        )
        return await _editor(db, programme)


@admin.delete("/opening")
async def reset_opening(
    response: Response,
    body: VersionIn | None = None,
    settings_version: int | None = None,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Go back to the built-in page. The version loaded comes in the body or as
    ?settings_version=."""
    no_store(response)
    version = body.settings_version if body is not None else settings_version
    if version is None:
        message = "Send the settings_version this page was loaded with."
        raise validation(message, {"fields": [{"field": "settings_version", "message": message}]})
    async with db.begin():
        programme = await _programme_at(db, version)
        programme.opening = {}
        programme.settings_version += 1
        await audit(db, ctx.actor, "opening.reset", programme_id=programme.id)
        return await _editor(db, programme)
