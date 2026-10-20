"""Participant snapshots and event details.

The programme structure (events, ballots, nominees, asset URLs) is cached per process
and keyed by the programme's `state_version`, which every participant-visible change
increments. Time-dependent status (live vs closed at the deadline) is computed per
request from server time, and receipts are always read from the database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import ParticipantContext
from ..errors import AppError, not_found
from ..models import Asset, Ballot, Event, Nominee, Programme
from .programmes import login_phase, public_programme, server_now
from .results import ballot_results
from .voting import list_receipts


@dataclass
class NomineeView:
    id: uuid.UUID
    name: str
    affiliation: str
    description: str
    photo_url: str | None
    members: tuple[str, ...] = ()  # team ballots: who is in the team


@dataclass
class BallotView:
    id: uuid.UUID
    title: str
    kind: str
    description: str
    nominees: list[NomineeView] = field(default_factory=list)


@dataclass
class EventView:
    id: uuid.UUID
    position: int
    name: str
    description: str
    rules: str
    logo_url: str | None
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    opened_at: datetime | None
    voting_deadline_at: datetime | None
    closed_at: datetime | None
    results_published: bool
    ballots: list[BallotView] = field(default_factory=list)


@dataclass
class Structure:
    state_version: int
    urls: dict[uuid.UUID, str]
    events: list[EventView]


_cache: dict[uuid.UUID, Structure] = {}


async def load_structure(db: AsyncSession, programme: Programme) -> Structure:
    cached = _cache.get(programme.id)
    if cached is not None and cached.state_version == programme.state_version:
        return cached
    events = (
        (
            await db.execute(
                select(Event)
                .where(Event.programme_id == programme.id, Event.archived_at.is_(None))
                .order_by(Event.position, Event.created_at)
            )
        )
        .scalars()
        .all()
    )
    event_ids = [e.id for e in events]
    ballots = (
        (
            await db.execute(
                select(Ballot)
                .where(Ballot.event_id.in_(event_ids))
                .order_by(Ballot.position, Ballot.created_at)
            )
        )
        .scalars()
        .all()
        if event_ids
        else []
    )
    ballot_ids = [b.id for b in ballots]
    nominees = (
        (
            await db.execute(
                select(Nominee)
                .where(Nominee.ballot_id.in_(ballot_ids))
                .order_by(Nominee.position, Nominee.created_at)
            )
        )
        .scalars()
        .all()
        if ballot_ids
        else []
    )
    asset_ids = {programme.logo_asset_id, programme.banner_asset_id}
    asset_ids |= {e.logo_asset_id for e in events} | {n.photo_asset_id for n in nominees}
    asset_ids.discard(None)
    urls: dict[uuid.UUID, str] = {}
    if asset_ids:
        rows = (await db.execute(select(Asset.id, Asset.storage_key).where(Asset.id.in_(asset_ids)))).all()
        urls = {r.id: f"/media/{r.storage_key}" for r in rows}

    nominees_by_ballot: dict[uuid.UUID, list[NomineeView]] = {}
    for n in nominees:
        nominees_by_ballot.setdefault(n.ballot_id, []).append(
            NomineeView(
                n.id,
                n.name,
                n.affiliation,
                n.description,
                urls.get(n.photo_asset_id) if n.photo_asset_id else None,
                tuple(n.members),
            )
        )
    ballots_by_event: dict[uuid.UUID, list[BallotView]] = {}
    for b in ballots:
        ballots_by_event.setdefault(b.event_id, []).append(
            BallotView(b.id, b.title, b.kind, b.description, nominees_by_ballot.get(b.id, []))
        )
    views = [
        EventView(
            id=e.id,
            position=e.position,
            name=e.name,
            description=e.description,
            rules=e.rules,
            logo_url=urls.get(e.logo_asset_id) if e.logo_asset_id else None,
            status=e.status,
            planned_start_at=e.planned_start_at,
            planned_end_at=e.planned_end_at,
            opened_at=e.opened_at,
            voting_deadline_at=e.voting_deadline_at,
            closed_at=e.closed_at,
            results_published=e.results_published_at is not None and e.status == "closed",
            ballots=ballots_by_event.get(e.id, []),
        )
        for e in events
    ]
    structure = Structure(programme.state_version, urls, views)
    _cache[programme.id] = structure
    return structure


def participant_status(event: EventView, now: datetime) -> str:
    if event.status == "open" and event.voting_deadline_at is not None and now < event.voting_deadline_at:
        return "live"
    if event.status == "upcoming":
        return "upcoming"
    return "closed"


def _closed_at(event: EventView) -> datetime | None:
    if event.status == "closed":
        return event.closed_at
    if event.status == "open":
        return event.voting_deadline_at
    return None


async def _programme(db: AsyncSession, programme_id: uuid.UUID) -> Programme:
    return (
        await db.execute(
            select(Programme).where(Programme.id == programme_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def participant_snapshot(db: AsyncSession, ctx: ParticipantContext) -> dict[str, Any]:
    async with db.begin():
        programme = await _programme(db, ctx.programme_id)
        now = await server_now(db)
        structure = await load_structure(db, programme)
        receipts = await list_receipts(db, ctx.participant_id)

    voted_ballots = {r.ballot_id for r in receipts}
    total = len(structure.events)
    events_out: list[dict[str, Any]] = []
    live_event: dict[str, Any] | None = None
    next_event: dict[str, Any] | None = None
    for index, event in enumerate(structure.events, start=1):
        status = participant_status(event, now)
        released = status != "upcoming"
        show_identity = released or programme.preview_upcoming
        item: dict[str, Any] = {
            "id": str(event.id),
            "round": index,
            "status": status,
            "locked": not released,
            "name": event.name if show_identity else None,
            "logo_url": event.logo_url if show_identity else None,
            "planned_start_at": event.planned_start_at
            if (programme.publish_schedule and status == "upcoming")
            else None,
            "opened_at": event.opened_at if released else None,
            "voting_deadline_at": event.voting_deadline_at if status == "live" else None,
            "closed_at": _closed_at(event) if status == "closed" else None,
            "ballots": [
                {"id": str(b.id), "title": b.title, "voted": b.id in voted_ballots} for b in event.ballots
            ]
            if released
            else [],
            "ballot_count": len(event.ballots) if released else None,
            "results_published": event.results_published,
        }
        events_out.append(item)
        if status == "live" and live_event is None:
            live_event = item
        if status == "upcoming" and next_event is None:
            next_event = item

    phase_login = login_phase(programme, now)
    all_closed = total > 0 and all(e["status"] == "closed" for e in events_out)
    if phase_login == "closed":
        phase = "access_closed"
    elif programme.ended_at is not None or all_closed:
        phase = "completed"
    elif live_event is not None:
        phase = "live"
    else:
        phase = "waiting"

    return {
        "server_time": now,
        "state_version": programme.state_version,
        "phase": phase,
        "programme": public_programme(programme, structure.urls, now),
        "participant": {"user_code": ctx.user_code, "display_name": ctx.display_name},
        "session_expires_at": ctx.expires_at,
        "csrf_token": ctx.csrf_token,
        "event_count": total,
        "live_event_id": live_event["id"] if live_event else None,
        "next_event_id": next_event["id"] if next_event else None,
        "events": events_out,
        "receipts": [r.public() for r in receipts],
    }


async def event_detail(db: AsyncSession, ctx: ParticipantContext, event_id: uuid.UUID) -> dict[str, Any]:
    async with db.begin():
        programme = await _programme(db, ctx.programme_id)
        now = await server_now(db)
        structure = await load_structure(db, programme)
        event = next((e for e in structure.events if e.id == event_id), None)
        if event is None:
            raise not_found("Event")
        status = participant_status(event, now)
        round_no = structure.events.index(event) + 1
        receipts = {r.ballot_id: r for r in await list_receipts(db, ctx.participant_id)}
        results = None
        if event.results_published and status == "closed":
            results = await ballot_results(db, [b.id for b in event.ballots], programme_id=programme.id)

    base: dict[str, Any] = {
        "server_time": now,
        "state_version": programme.state_version,
        "timezone": programme.timezone,
        "event_count": len(structure.events),
    }
    if status == "upcoming":
        if not programme.preview_upcoming:
            raise AppError("EVENT_NOT_RELEASED", "This round has not opened yet.", 403)
        return {
            **base,
            "event": {
                "id": str(event.id),
                "round": round_no,
                "status": status,
                "locked": True,
                "name": event.name,
                "logo_url": event.logo_url,
                "planned_start_at": event.planned_start_at if programme.publish_schedule else None,
            },
            "ballots": [],
            "results": None,
        }
    return {
        **base,
        "event": {
            "id": str(event.id),
            "round": round_no,
            "status": status,
            "locked": False,
            "name": event.name,
            "description": event.description,
            "rules": event.rules,
            "logo_url": event.logo_url,
            "opened_at": event.opened_at,
            "voting_deadline_at": event.voting_deadline_at if status == "live" else None,
            "closed_at": _closed_at(event) if status == "closed" else None,
            "results_published": event.results_published,
        },
        "ballots": [
            {
                "id": str(b.id),
                "title": b.title,
                "kind": b.kind,
                "description": b.description,
                "nominees": [
                    {
                        "id": str(n.id),
                        "name": n.name,
                        "affiliation": n.affiliation,
                        "description": n.description,
                        "members": list(n.members),
                        "photo_url": n.photo_url,
                    }
                    for n in b.nominees
                ],
                "receipt": receipts[b.id].public() if b.id in receipts else None,
            }
            for b in event.ballots
        ],
        "results": results,
    }


def invalidate_cache() -> None:
    _cache.clear()
