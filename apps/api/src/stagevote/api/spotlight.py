"""The spotlight screen (/spotlight): the public view and the organiser's editor and buttons.

See services/spotlight.py. Nothing here touches events, votes, the programme settings or the
opening page: the spotlight lives in its own column and has its own version.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import Field, StringConstraints
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import AppError
from ..models import Programme
from ..services import opening, spotlight
from ..services.programmes import active_programme, asset_urls
from ..state import audit
from .common import Model, SingleLine, no_store

router = APIRouter(prefix="/api/v1")
admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])

EntryId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=spotlight.ENTRY_ID_MAX, pattern=spotlight.ENTRY_ID_PATTERN),
]


# ---- public -------------------------------------------------------------------


@router.get("/public/spotlight")
async def get_public_spotlight(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The screen's view. Names and pictures only of people already revealed."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        state = spotlight.resolve(programme)
        files = await spotlight.images(db, state)
        # The same emblem as the opening page, so the two screens match.
        logo = opening.resolve_page(programme)["logo_asset_id"]
        urls = await asset_urls(db, [logo])
    return spotlight.public_view(state, files, urls.get(logo) if logo else None)


# ---- organiser ----------------------------------------------------------------


class EntryIn(Model):
    id: EntryId
    name: SingleLine = Field(min_length=1, max_length=spotlight.NAME_MAX)
    detail: SingleLine = Field(default="", max_length=spotlight.DETAIL_MAX)
    # A picture uploaded through /admin/media/uploads.
    image_asset_id: uuid.UUID | None = None
    # Accepted and ignored, so the editor can send back a person as it loaded them.
    image: dict[str, Any] | None = Field(default=None, exclude=True)


class SpotlightIn(Model):
    """The whole spotlight: saving replaces the title, subtitle, hold time and people."""

    version: int
    title: SingleLine = Field(min_length=1, max_length=spotlight.TITLE_MAX)
    subtitle: SingleLine = Field(default="", max_length=spotlight.SUBTITLE_MAX)
    hold_seconds: int = Field(ge=0, le=spotlight.HOLD_MAX)
    entries: list[EntryIn] = Field(max_length=spotlight.MAX_ENTRIES)


class ShowIn(Model):
    entry_id: EntryId


async def _locked_programme(db: AsyncSession) -> Programme:
    """The active programme, locked for this transaction. Like the opening page, the spotlight
    changes nothing a vote reads, so it never takes the live lock or waits for votes."""
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
    return programme


async def _editor(db: AsyncSession, state: dict[str, Any]) -> dict[str, Any]:
    return {"spotlight": spotlight.editor_view(state, await spotlight.images(db, state))}


@admin.get("/spotlight")
async def get_spotlight(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        return await _editor(db, spotlight.resolve(programme))


@admin.put("/spotlight")
async def save_spotlight(
    body: SpotlightIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    content = body.model_dump(exclude={"version"})
    spotlight.check_entry_ids(content["entries"])
    async with db.begin():
        programme = await _locked_programme(db)
        state = spotlight.resolve(programme)
        if state["version"] != body.version:
            raise AppError(
                "STALE_SPOTLIGHT",
                "Someone else changed the spotlight. Reload to see the latest version.",
                409,
            )
        await spotlight.check_images(db, content["entries"])
        state = spotlight.saved(state, content)
        programme.spotlight = spotlight.stored(state)
        await audit(
            db,
            ctx.actor,
            "spotlight.updated",
            programme_id=programme.id,
            details={"people": len(state["entries"])},
        )
        return await _editor(db, state)


@admin.post("/spotlight/show")
async def show_entry(
    body: ShowIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Bring one person's card to the middle of every screen showing the spotlight."""
    no_store(response)
    async with db.begin():
        programme = await _locked_programme(db)
        state = spotlight.resolve(programme)
        entry = next((e for e in state["entries"] if e["id"] == body.entry_id), None)
        if entry is None:
            raise AppError(
                "NOT_FOUND", "That person is not in the saved spotlight. Save first, then show them.", 404
            )
        state = spotlight.showing(state, entry["id"])
        programme.spotlight = spotlight.stored(state)
        await audit(
            db,
            ctx.actor,
            "spotlight.shown",
            programme_id=programme.id,
            details={"name": entry["name"], "seq": state["seq"]},
        )
        return await _editor(db, state)


@admin.post("/spotlight/back")
async def send_back(
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The card in the middle goes back to its place now (otherwise after the hold time)."""
    no_store(response)
    async with db.begin():
        programme = await _locked_programme(db)
        before = spotlight.resolve(programme)
        state = spotlight.sent_back(before)
        if state is not before:
            programme.spotlight = spotlight.stored(state)
            await audit(db, ctx.actor, "spotlight.sent_back", programme_id=programme.id)
        return await _editor(db, state)


@admin.post("/spotlight/hide-all")
async def hide_all(
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Every card face down again on every screen. The people stay as they are."""
    no_store(response)
    async with db.begin():
        programme = await _locked_programme(db)
        state = spotlight.hidden_again(spotlight.resolve(programme))
        programme.spotlight = spotlight.stored(state)
        await audit(
            db,
            ctx.actor,
            "spotlight.cards_hidden",
            programme_id=programme.id,
            details={"epoch": state["epoch"]},
        )
        return await _editor(db, state)
