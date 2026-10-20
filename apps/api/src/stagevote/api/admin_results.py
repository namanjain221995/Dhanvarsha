"""Aggregate results, CSV export and results publication."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import conflict, not_found
from ..models import Event
from ..services.live import begin_transition
from ..services.programmes import active_programme, server_now
from ..services.results import ballot_results
from ..state import audit, bump_state
from .common import no_store

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


def _label(event: Event, now: Any) -> str:
    if event.status == "closed" or (event.voting_deadline_at and event.voting_deadline_at <= now):
        return "final"
    if event.status == "open":
        return "provisional"
    return "not_started"


async def _event_results(
    db: AsyncSession, programme_id: uuid.UUID, events: list[Event]
) -> list[dict[str, Any]]:
    now = await server_now(db)
    ids = [e.id for e in events]
    ballot_rows = (
        (
            await db.execute(
                text(
                    "SELECT id, event_id FROM ballots WHERE event_id = ANY(:ids) ORDER BY position, created_at"
                ),
                {"ids": ids},
            )
        ).all()
        if ids
        else []
    )
    results = await ballot_results(db, [r.id for r in ballot_rows], programme_id=programme_id)
    by_ballot = {r["ballot_id"]: r for r in results}
    out = []
    for index, event in enumerate(events, start=1):
        out.append(
            {
                "event_id": str(event.id),
                "round": index,
                "name": event.name,
                "status": event.status,
                "label": _label(event, now),
                "results_published": event.results_published_at is not None,
                "closed_at": event.closed_at,
                "ballots": [
                    by_ballot[str(r.id)]
                    for r in ballot_rows
                    if r.event_id == event.id and str(r.id) in by_ballot
                ],
            }
        )
    return out


async def _events(db: AsyncSession, programme_id: uuid.UUID, event_id: uuid.UUID | None) -> list[Event]:
    stmt = select(Event).where(Event.programme_id == programme_id, Event.archived_at.is_(None))
    events = list((await db.execute(stmt.order_by(Event.position, Event.created_at))).scalars().all())
    if event_id is not None:
        events = [e for e in events if e.id == event_id]
        if not events:
            raise not_found("Event")
    return events


@router.get("/results")
async def results(
    response: Response, event_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        events = await _events(db, programme.id, None)
        rounds = {e.id: i for i, e in enumerate(events, start=1)}
        selected = [e for e in events if event_id is None or e.id == event_id]
        if event_id is not None and not selected:
            raise not_found("Event")
        data = await _event_results(db, programme.id, selected)
        now = await server_now(db)
    for item in data:
        item["round"] = rounds[uuid.UUID(item["event_id"])]
    return {"server_time": now, "timezone": programme.timezone, "events": data}


@router.get("/results/export.csv")
async def export_csv(
    event_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: AdminContext = Depends(current_admin),
) -> Response:
    """Aggregate counts only (no per-person selections)."""
    async with db.begin():
        programme = await active_programme(db)
        events = await _events(db, programme.id, None)
        rounds = {e.id: i for i, e in enumerate(events, start=1)}
        selected = [e for e in events if event_id is None or e.id == event_id]
        data = await _event_results(db, programme.id, selected)
        await audit(
            db,
            ctx.actor,
            "results.exported",
            programme_id=programme.id,
            details={"event_id": str(event_id) if event_id else None},
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "round",
            "event",
            "event_status",
            "result_label",
            "ballot",
            "nominee",
            "affiliation",
            "votes",
            "percent",
            "ballot_total_votes",
            "eligible_voters",
            "leading",
            "tie",
        ]
    )
    for item in data:
        for ballot in item["ballots"]:
            for nominee in ballot["nominees"]:
                writer.writerow(
                    [
                        rounds[uuid.UUID(item["event_id"])],
                        _safe(item["name"]),
                        item["status"],
                        item["label"],
                        _safe(ballot["title"]),
                        _safe(nominee["name"]),
                        _safe(nominee["affiliation"]),
                        nominee["votes"],
                        nominee["percent"],
                        ballot["total_votes"],
                        ballot["eligible_voters"],
                        "yes" if nominee["leading"] else "no",
                        "yes" if ballot["tie"] else "no",
                    ]
                )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="stagevote-results.csv"',
            "Cache-Control": "no-store, private",
        },
    )


def _safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported text cells."""
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


async def _set_published(db: AsyncSession, event_id: uuid.UUID, publish: bool, ctx: AdminContext) -> None:
    async with db.begin():
        current = await active_programme(db)
        programme, now = await begin_transition(db, current.id, None)
        event = (
            await db.execute(
                select(Event)
                .where(Event.id == event_id, Event.programme_id == programme.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if event is None:
            raise not_found("Event")
        if publish and event.status != "closed":
            raise conflict(
                "EVENT_NOT_CLOSED", "Results can be published once voting for the event has closed."
            )
        event.results_published_at = now if publish else None
        event.version += 1
        await bump_state(db, programme.id, reason="results")
        await audit(
            db,
            ctx.actor,
            "results.published" if publish else "results.unpublished",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
        )


@router.post("/events/{event_id}/results/publish")
async def publish(
    event_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    await _set_published(db, event_id, True, ctx)
    return {"ok": True}


@router.post("/events/{event_id}/results/unpublish")
async def unpublish(
    event_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    await _set_published(db, event_id, False, ctx)
    return {"ok": True}
