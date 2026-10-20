"""Admin event, ballot and nominee editor.

Freeze rules
------------
* Before an event first opens, everything can change.
* When voting first opens, ballots and nominee identity freeze (`frozen_at`). After that:
  - event name, description, rules and logo stay editable (presentation; audited);
  - ballot title and nominee name accept only small corrections (similarity >= 0.8, a
    reason is required, before/after is audited);
  - nominee affiliation, description and photo stay editable (audited);
  - ballots and nominees cannot be added or removed: a new meaning needs a new event.
* Votes reference nominees with ON DELETE RESTRICT, so nothing with votes can be deleted.
"""

from __future__ import annotations

import difflib
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import AwareDatetime, Field, StringConstraints
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import conflict, not_found, validation
from ..models import Asset, Ballot, Event, Nominee, Programme
from ..services.live import begin_transition, regenerate_schedule
from ..services.programmes import active_programme, server_now
from ..state import Actor, audit, bump_state
from .common import Model, SingleLine, no_store

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.casefold().strip(), b.casefold().strip()).ratio()


def _similar(a: str, b: str) -> bool:
    return _ratio(a, b) >= 0.8


def _is_correction(original: str, new: str, siblings: list[str]) -> bool:
    """A post-freeze rename must stay close to the original and must not move towards a
    sibling's name ("Team A" -> "Team B" would swap the meaning of recorded votes)."""
    if not _similar(original, new):
        return False
    closeness = _ratio(original, new)
    return all(
        new.casefold().strip() != other.casefold().strip() and _ratio(new, other) < closeness
        for other in siblings
    )


async def _urls(db: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    rows = (await db.execute(select(Asset.id, Asset.storage_key).where(Asset.id.in_(wanted)))).all()
    return {r.id: f"/media/{r.storage_key}" for r in rows}


async def _rounds(db: AsyncSession, programme: Programme) -> dict[uuid.UUID, int]:
    """Round numbers as participants see them: position order among non-archived events."""
    ids = (
        (
            await db.execute(
                select(Event.id)
                .where(Event.programme_id == programme.id, Event.archived_at.is_(None))
                .order_by(Event.position, Event.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {event_id: index for index, event_id in enumerate(ids, start=1)}


async def _event_payloads(
    db: AsyncSession, programme: Programme, events: list[Event]
) -> list[dict[str, Any]]:
    rounds = await _rounds(db, programme)
    ids = [e.id for e in events]
    ballots = (
        (
            await db.execute(
                select(Ballot).where(Ballot.event_id.in_(ids)).order_by(Ballot.position, Ballot.created_at)
            )
        )
        .scalars()
        .all()
        if ids
        else []
    )
    bids = [b.id for b in ballots]
    nominees = (
        (
            await db.execute(
                select(Nominee)
                .where(Nominee.ballot_id.in_(bids))
                .order_by(Nominee.position, Nominee.created_at)
            )
        )
        .scalars()
        .all()
        if bids
        else []
    )
    counts: dict[Any, Any] = (
        dict(
            (
                await db.execute(
                    text(
                        "SELECT ballot_id, count(*) FROM votes WHERE ballot_id = ANY(:ids) GROUP BY ballot_id"
                    ),
                    {"ids": bids},
                )
            ).all()
        )
        if bids
        else {}
    )
    urls = await _urls(db, {e.logo_asset_id for e in events} | {n.photo_asset_id for n in nominees})
    nominees_by: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for n in nominees:
        nominees_by.setdefault(n.ballot_id, []).append(
            {
                "id": str(n.id),
                "name": n.name,
                "affiliation": n.affiliation,
                "description": n.description,
                "members": list(n.members),
                "position": n.position,
                "photo_asset_id": str(n.photo_asset_id) if n.photo_asset_id else None,
                "photo_url": urls.get(n.photo_asset_id) if n.photo_asset_id else None,
            }
        )
    ballots_by: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for b in ballots:
        ballots_by.setdefault(b.event_id, []).append(
            {
                "id": str(b.id),
                "title": b.title,
                "kind": b.kind,
                "description": b.description,
                "position": b.position,
                "frozen": b.frozen_at is not None,
                "vote_count": int(counts.get(b.id, 0)),
                "nominees": nominees_by.get(b.id, []),
            }
        )
    out = []
    for e in events:
        out.append(
            {
                "id": str(e.id),
                "round": rounds.get(e.id),
                "position": e.position,
                "name": e.name,
                "description": e.description,
                "rules": e.rules,
                "logo_asset_id": str(e.logo_asset_id) if e.logo_asset_id else None,
                "logo_url": urls.get(e.logo_asset_id) if e.logo_asset_id else None,
                "status": e.status,
                "planned_start_at": e.planned_start_at,
                "planned_end_at": e.planned_end_at,
                "opened_at": e.opened_at,
                "voting_deadline_at": e.voting_deadline_at,
                "closed_at": e.closed_at,
                "close_reason": e.close_reason,
                "opened_by": e.opened_by,
                "frozen": e.frozen_at is not None,
                "results_published": e.results_published_at is not None,
                "archived": e.archived_at is not None,
                "version": e.version,
                "ballots": ballots_by.get(e.id, []),
                "vote_count": sum(b["vote_count"] for b in ballots_by.get(e.id, [])),
            }
        )
    return out


async def _list(db: AsyncSession, programme: Programme, include_archived: bool) -> list[Event]:
    stmt = select(Event).where(Event.programme_id == programme.id)
    if not include_archived:
        stmt = stmt.where(Event.archived_at.is_(None))
    return list((await db.execute(stmt.order_by(Event.position, Event.created_at))).scalars().all())


@router.get("/events")
async def list_events(
    response: Response, include_archived: bool = False, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        events = await _list(db, programme, include_archived)
        payload = await _event_payloads(db, programme, events)
        now = await server_now(db)
    return {
        "events": payload,
        "server_time": now,
        "timezone": programme.timezone,
        "run_mode": programme.run_mode,
    }


async def _get_event(db: AsyncSession, programme: Programme, event_id: uuid.UUID, lock: bool = True) -> Event:
    stmt = select(Event).where(Event.id == event_id, Event.programme_id == programme.id)
    if lock:
        stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
    event = (await db.execute(stmt)).scalar_one_or_none()
    if event is None:
        raise not_found("Event")
    return event


@router.get("/events/{event_id}")
async def get_event(
    event_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        event = await _get_event(db, programme, event_id, lock=False)
        [found] = await _event_payloads(db, programme, [event])
        now = await server_now(db)
    return {"event": found, "server_time": now, "timezone": programme.timezone}


async def _validate_window(
    db: AsyncSession,
    programme: Programme,
    event_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
) -> None:
    if (start is None) != (end is None):
        raise validation("Set both the planned start and end, or neither.")
    if start is None or end is None:
        return
    if end <= start:
        raise validation("The planned end must be after the planned start.")
    if programme.login_opens_at and start < programme.login_opens_at:
        raise validation("The planned start is before programme access opens.")
    if programme.login_closes_at and end > programme.login_closes_at:
        raise validation("The planned end is after programme access closes.")
    clash = (
        (
            await db.execute(
                select(Event.name).where(
                    Event.programme_id == programme.id,
                    Event.archived_at.is_(None),
                    Event.planned_start_at.is_not(None),
                    Event.id != (event_id or uuid.UUID(int=0)),
                    Event.planned_start_at < end,
                    Event.planned_end_at > start,
                )
            )
        )
        .scalars()
        .first()
    )
    if clash:
        raise validation(f"This window overlaps “{clash}”. Scheduled live windows cannot overlap.")


class EventCreate(Model):
    name: SingleLine = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    rules: str = Field(default="", max_length=4000)
    ballot_kind: Literal["team", "individual"] = "team"
    ballot_title: SingleLine = Field(default="Your vote", min_length=1, max_length=80)
    planned_start_at: AwareDatetime | None = None
    planned_end_at: AwareDatetime | None = None
    logo_asset_id: uuid.UUID | None = None


async def _after_schedule_edit(db: AsyncSession, programme: Programme, now: datetime) -> None:
    if programme.run_mode == "scheduled":
        await regenerate_schedule(db, programme, now)


@router.post("/events", status_code=201)
async def create_event(
    body: EventCreate,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        programme, now = await begin_transition(db, current.id, None)
        await _validate_window(db, programme, None, body.planned_start_at, body.planned_end_at)
        position = (
            await db.execute(
                select(func.coalesce(func.max(Event.position), 0)).where(Event.programme_id == programme.id)
            )
        ).scalar_one() + 1
        event = Event(
            programme_id=programme.id,
            position=position,
            name=body.name,
            description=body.description,
            rules=body.rules,
            logo_asset_id=body.logo_asset_id,
            planned_start_at=body.planned_start_at,
            planned_end_at=body.planned_end_at,
        )
        db.add(event)
        await db.flush()
        db.add(
            Ballot(
                programme_id=programme.id,
                event_id=event.id,
                position=1,
                title=body.ballot_title,
                kind=body.ballot_kind,
            )
        )
        await db.flush()
        if body.planned_start_at:
            await _after_schedule_edit(db, programme, now)
        await bump_state(db, programme.id, reason="event_created")
        await audit(
            db,
            ctx.actor,
            "event.created",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
            details={"name": event.name},
        )
        payload = await _event_payloads(db, programme, [event])
    return {"event": payload[0]}


class EventPatch(Model):
    version: int
    name: SingleLine | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    rules: str | None = Field(default=None, max_length=4000)
    logo_asset_id: uuid.UUID | None = None
    clear_logo: bool = False
    planned_start_at: AwareDatetime | None = None
    planned_end_at: AwareDatetime | None = None
    clear_schedule: bool = False


@router.patch("/events/{event_id}")
async def patch_event(
    event_id: uuid.UUID,
    body: EventPatch,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    fields = body.model_dump(exclude_unset=True)
    async with db.begin():
        current = await active_programme(db)
        programme, now = await begin_transition(db, current.id, None)
        event = await _get_event(db, programme, event_id)
        if event.version != body.version:
            raise conflict(
                "STALE_EVENT", "This event changed since you opened it. Reload to see the latest version."
            )
        changes: dict[str, Any] = {}
        for key in ("name", "description", "rules"):
            if key in fields and fields[key] is not None and getattr(event, key) != fields[key]:
                changes[key] = {"from": getattr(event, key), "to": fields[key]}
                setattr(event, key, fields[key])
        if body.clear_logo or (body.logo_asset_id and body.logo_asset_id != event.logo_asset_id):
            new_logo = None if body.clear_logo else body.logo_asset_id
            changes["logo_asset_id"] = {"from": str(event.logo_asset_id), "to": str(new_logo)}
            event.logo_asset_id = new_logo
        schedule_touched = body.clear_schedule or "planned_start_at" in fields or "planned_end_at" in fields
        if schedule_touched:
            if event.status != "upcoming":
                raise conflict("EVENT_STARTED", "The schedule of an event that has opened cannot change.")
            start = None if body.clear_schedule else fields.get("planned_start_at", event.planned_start_at)
            end = None if body.clear_schedule else fields.get("planned_end_at", event.planned_end_at)
            await _validate_window(db, programme, event.id, start, end)
            if (start, end) != (event.planned_start_at, event.planned_end_at):
                changes["schedule"] = {
                    "from": [str(event.planned_start_at), str(event.planned_end_at)],
                    "to": [str(start), str(end)],
                }
                event.planned_start_at, event.planned_end_at = start, end
        if not changes:
            payload = await _event_payloads(db, programme, [event])
            return {"event": payload[0]}
        event.version += 1
        try:
            await db.flush()
        except IntegrityError as exc:
            raise validation("This window overlaps another scheduled event.") from exc
        if "schedule" in changes:
            await _after_schedule_edit(db, programme, now)
        await bump_state(db, programme.id, live="schedule" in changes, reason="event_updated")
        action = "event.presentation_corrected" if event.frozen_at else "event.updated"
        await audit(
            db,
            ctx.actor,
            action,
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
            details=changes,
        )
        payload = await _event_payloads(db, programme, [event])
    return {"event": payload[0]}


class ReorderIn(Model):
    ordered_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.post("/events/reorder")
async def reorder_events(
    body: ReorderIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        programme, _ = await begin_transition(db, current.id, None)
        events = (
            (
                await db.execute(
                    select(Event).where(Event.programme_id == programme.id).with_for_update(key_share=True)
                )
            )
            .scalars()
            .all()
        )
        by_id = {e.id: e for e in events}
        if set(body.ordered_ids) - set(by_id):
            raise validation("Unknown event in the new order.")
        ordered = [by_id[i] for i in body.ordered_ids] + [
            e for e in events if e.id not in set(body.ordered_ids)
        ]
        for position, event in enumerate(ordered, start=1):
            event.position = position
        await bump_state(db, programme.id, reason="reorder")
        await audit(
            db,
            ctx.actor,
            "event.reordered",
            programme_id=programme.id,
            details={"order": [str(e.id) for e in ordered]},
        )
    return {"ok": True}


@router.post("/events/{event_id}/archive")
async def archive_event(
    event_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        programme, now = await begin_transition(db, current.id, None)
        event = await _get_event(db, programme, event_id)
        if event.status == "open":
            raise conflict("EVENT_OPEN", "Close voting before archiving this event.")
        event.archived_at = now
        event.version += 1
        if event.planned_start_at:
            await _after_schedule_edit(db, programme, now)
        await bump_state(db, programme.id, live=True, reason="archive")
        await audit(
            db,
            ctx.actor,
            "event.archived",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
        )
    return {"ok": True}


@router.post("/events/{event_id}/unarchive")
async def unarchive_event(
    event_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        programme, now = await begin_transition(db, current.id, None)
        event = await _get_event(db, programme, event_id)
        event.archived_at = None
        event.version += 1
        try:
            await db.flush()
            # The overlap constraint is deferred to COMMIT; check it here so a clash is
            # reported as a validation error instead of failing at commit.
            await db.execute(text("SET CONSTRAINTS ex_events_planned_window_overlap IMMEDIATE"))
        except IntegrityError as exc:
            raise validation(
                "Its planned window now overlaps another event. Change one schedule first."
            ) from exc
        if event.planned_start_at:
            await _after_schedule_edit(db, programme, now)
        await bump_state(db, programme.id, live=True, reason="unarchive")
        await audit(
            db,
            ctx.actor,
            "event.unarchived",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
        )
    return {"ok": True}


# ---- ballots ------------------------------------------------------------------


async def _get_ballot(db: AsyncSession, programme: Programme, ballot_id: uuid.UUID) -> tuple[Ballot, Event]:
    ballot = (
        await db.execute(
            select(Ballot)
            .where(Ballot.id == ballot_id, Ballot.programme_id == programme.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if ballot is None:
        raise not_found("Ballot")
    event = await _get_event(db, programme, ballot.event_id)
    return ballot, event


class BallotCreate(Model):
    title: SingleLine = Field(min_length=1, max_length=80)
    kind: Literal["team", "individual"] = "team"
    description: str = Field(default="", max_length=2000)


@router.post("/events/{event_id}/ballots", status_code=201)
async def create_ballot(
    event_id: uuid.UUID,
    body: BallotCreate,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        programme, _ = await begin_transition(db, current.id, None)
        event = await _get_event(db, programme, event_id)
        if event.frozen_at is not None or event.status != "upcoming":
            raise conflict(
                "EVENT_FROZEN", "Ballots cannot be added after voting has opened. Create a new event instead."
            )
        position = (
            await db.execute(
                select(func.coalesce(func.max(Ballot.position), 0)).where(Ballot.event_id == event.id)
            )
        ).scalar_one() + 1
        ballot = Ballot(
            programme_id=programme.id,
            event_id=event.id,
            position=position,
            title=body.title,
            kind=body.kind,
            description=body.description,
        )
        db.add(ballot)
        event.version += 1
        await db.flush()
        await bump_state(db, programme.id, reason="ballot_created")
        await audit(
            db,
            ctx.actor,
            "ballot.created",
            programme_id=programme.id,
            target_type="ballot",
            target_id=ballot.id,
            details={"event_id": str(event.id), "title": body.title, "kind": body.kind},
        )
    return {"id": str(ballot.id)}


class BallotPatch(Model):
    title: SingleLine | None = Field(default=None, min_length=1, max_length=80)
    kind: Literal["team", "individual"] | None = None
    description: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=300)


@router.patch("/ballots/{ballot_id}")
async def patch_ballot(
    ballot_id: uuid.UUID,
    body: BallotPatch,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        ballot, event = await _get_ballot(db, programme, ballot_id)
        changes: dict[str, Any] = {}
        frozen = ballot.frozen_at is not None
        if body.kind is not None and body.kind != ballot.kind:
            if frozen:
                raise conflict("BALLOT_FROZEN", "The ballot type cannot change after voting opened.")
            changes["kind"] = {"from": ballot.kind, "to": body.kind}
            ballot.kind = body.kind
        if body.title is not None and body.title != ballot.title:
            if frozen:
                siblings = list(
                    (
                        await db.execute(
                            select(Ballot.title).where(
                                Ballot.event_id == ballot.event_id, Ballot.id != ballot.id
                            )
                        )
                    ).scalars()
                )
                if not _is_correction(ballot.title, body.title, siblings):
                    raise conflict(
                        "BALLOT_FROZEN", "After voting opens only small corrections to the title are allowed."
                    )
                if not body.reason:
                    raise validation("Give a reason for correcting a ballot that has opened.")
            changes["title"] = {"from": ballot.title, "to": body.title}
            ballot.title = body.title
        if body.description is not None and body.description != ballot.description:
            changes["description"] = {"from": ballot.description, "to": body.description}
            ballot.description = body.description
        if changes:
            event.version += 1
            await bump_state(db, programme.id, reason="ballot_updated")
            await audit(
                db,
                ctx.actor,
                "ballot.corrected" if frozen else "ballot.updated",
                programme_id=programme.id,
                target_type="ballot",
                target_id=ballot.id,
                details={**changes, "reason": body.reason},
            )
    return {"ok": True}


@router.delete("/ballots/{ballot_id}")
async def delete_ballot(
    ballot_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        ballot, event = await _get_ballot(db, programme, ballot_id)
        if ballot.frozen_at is not None or event.status != "upcoming":
            raise conflict("BALLOT_FROZEN", "Ballots cannot be removed after voting has opened.")
        remaining = (await db.execute(select(func.count()).where(Ballot.event_id == event.id))).scalar_one()
        if remaining <= 1:
            raise conflict("LAST_BALLOT", "An event needs at least one ballot.")
        try:
            await db.execute(delete(Nominee).where(Nominee.ballot_id == ballot.id))
            await db.execute(delete(Ballot).where(Ballot.id == ballot.id))
        except IntegrityError as exc:
            raise conflict("HAS_VOTES", "This ballot has votes and cannot be removed.") from exc
        event.version += 1
        await bump_state(db, programme.id, reason="ballot_deleted")
        await audit(
            db,
            ctx.actor,
            "ballot.deleted",
            programme_id=programme.id,
            target_type="ballot",
            target_id=ballot_id,
            details={"title": ballot.title},
        )
    return {"ok": True}


# ---- nominees -----------------------------------------------------------------


MemberName = Annotated[SingleLine, StringConstraints(max_length=120)]
MAX_MEMBERS = 30


def _members(names: list[str]) -> list[str]:
    """Team member names in the order given: blanks dropped, repeats (any case) kept once."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = " ".join(raw.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


class NomineeCreate(Model):
    name: SingleLine = Field(min_length=1, max_length=120)
    affiliation: SingleLine = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    # Team ballots: who is in the team. Shown to voters; the vote is for the whole team.
    members: list[MemberName] = Field(default_factory=list, max_length=MAX_MEMBERS)
    photo_asset_id: uuid.UUID | None = None


@router.post("/ballots/{ballot_id}/nominees", status_code=201)
async def create_nominee(
    ballot_id: uuid.UUID,
    body: NomineeCreate,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        ballot, event = await _get_ballot(db, programme, ballot_id)
        if ballot.frozen_at is not None or event.status != "upcoming":
            raise conflict("BALLOT_FROZEN", "Nominees cannot be added after voting has opened.")
        position = (
            await db.execute(
                select(func.coalesce(func.max(Nominee.position), 0)).where(Nominee.ballot_id == ballot.id)
            )
        ).scalar_one() + 1
        nominee = Nominee(
            ballot_id=ballot.id,
            position=position,
            name=body.name,
            affiliation=body.affiliation,
            description=body.description,
            members=_members(body.members),
            photo_asset_id=body.photo_asset_id,
        )
        db.add(nominee)
        event.version += 1
        await db.flush()
        await bump_state(db, programme.id, reason="nominee_created")
        await audit(
            db,
            ctx.actor,
            "nominee.created",
            programme_id=programme.id,
            target_type="nominee",
            target_id=nominee.id,
            details={"ballot_id": str(ballot.id), "name": body.name},
        )
    return {"id": str(nominee.id)}


class NomineePatch(Model):
    name: SingleLine | None = Field(default=None, min_length=1, max_length=120)
    affiliation: SingleLine | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    members: list[MemberName] | None = Field(default=None, max_length=MAX_MEMBERS)
    photo_asset_id: uuid.UUID | None = None
    clear_photo: bool = False
    reason: str | None = Field(default=None, max_length=300)


async def _get_nominee(
    db: AsyncSession, programme: Programme, nominee_id: uuid.UUID
) -> tuple[Nominee, Ballot, Event]:
    nominee = (
        await db.execute(
            select(Nominee)
            .where(Nominee.id == nominee_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if nominee is None:
        raise not_found("Nominee")
    ballot, event = await _get_ballot(db, programme, nominee.ballot_id)
    return nominee, ballot, event


@router.patch("/nominees/{nominee_id}")
async def patch_nominee(
    nominee_id: uuid.UUID,
    body: NomineePatch,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        nominee, ballot, event = await _get_nominee(db, programme, nominee_id)
        frozen = ballot.frozen_at is not None
        changes: dict[str, Any] = {}
        if body.name is not None and body.name != nominee.name:
            if frozen:
                siblings = list(
                    (
                        await db.execute(
                            select(Nominee.name).where(
                                Nominee.ballot_id == ballot.id, Nominee.id != nominee.id
                            )
                        )
                    ).scalars()
                )
                if not _is_correction(nominee.name, body.name, siblings):
                    raise conflict(
                        "NOMINEE_FROZEN",
                        "After voting opens a nominee can only get small spelling corrections. "
                        "A different nominee needs a new event.",
                    )
                if not body.reason:
                    raise validation("Give a reason for correcting a nominee after voting opened.")
            changes["name"] = {"from": nominee.name, "to": body.name}
            nominee.name = body.name
        for key in ("affiliation", "description"):
            value = getattr(body, key)
            if value is not None and value != getattr(nominee, key):
                changes[key] = {"from": getattr(nominee, key), "to": value}
                setattr(nominee, key, value)
        # Like the affiliation and description, the member list is presentation only and
        # may be corrected after voting opens (every change is audited).
        if body.members is not None and _members(body.members) != list(nominee.members):
            members = _members(body.members)
            changes["members"] = {"from": list(nominee.members), "to": members}
            nominee.members = members
        if body.clear_photo or (body.photo_asset_id and body.photo_asset_id != nominee.photo_asset_id):
            new_photo = None if body.clear_photo else body.photo_asset_id
            changes["photo_asset_id"] = {"from": str(nominee.photo_asset_id), "to": str(new_photo)}
            nominee.photo_asset_id = new_photo
        if changes:
            event.version += 1
            await bump_state(db, programme.id, reason="nominee_updated")
            await audit(
                db,
                ctx.actor,
                "nominee.corrected" if frozen else "nominee.updated",
                programme_id=programme.id,
                target_type="nominee",
                target_id=nominee.id,
                details={**changes, "reason": body.reason},
            )
    return {"ok": True}


@router.delete("/nominees/{nominee_id}")
async def delete_nominee(
    nominee_id: uuid.UUID,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        nominee, ballot, event = await _get_nominee(db, programme, nominee_id)
        if ballot.frozen_at is not None or event.status != "upcoming":
            raise conflict("NOMINEE_FROZEN", "Nominees cannot be removed after voting has opened.")
        try:
            await db.execute(delete(Nominee).where(Nominee.id == nominee.id))
        except IntegrityError as exc:
            raise conflict("HAS_VOTES", "This nominee has votes and cannot be removed.") from exc
        event.version += 1
        await bump_state(db, programme.id, reason="nominee_deleted")
        await audit(
            db,
            ctx.actor,
            "nominee.deleted",
            programme_id=programme.id,
            target_type="nominee",
            target_id=nominee_id,
            details={"name": nominee.name},
        )
    return {"ok": True}


class NomineeOrder(Model):
    ordered_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.post("/ballots/{ballot_id}/nominees/reorder")
async def reorder_nominees(
    ballot_id: uuid.UUID,
    body: NomineeOrder,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        current = await active_programme(db)
        # Same lock order as live transitions (live lock, programme, event, rows).
        programme, _ = await begin_transition(db, current.id, None)
        ballot, event = await _get_ballot(db, programme, ballot_id)
        if ballot.frozen_at is not None or event.status != "upcoming":
            raise conflict(
                "BALLOT_FROZEN", "The order of nominees is fixed once voting has opened for this ballot."
            )
        nominees = (
            (
                await db.execute(
                    select(Nominee).where(Nominee.ballot_id == ballot.id).with_for_update(key_share=True)
                )
            )
            .scalars()
            .all()
        )
        by_id = {n.id: n for n in nominees}
        if set(body.ordered_ids) - set(by_id):
            raise validation("Unknown nominee in the new order.")
        ordered = [by_id[i] for i in body.ordered_ids] + [
            n for n in nominees if n.id not in set(body.ordered_ids)
        ]
        for position, nominee in enumerate(ordered, start=1):
            nominee.position = position
        event.version += 1
        await bump_state(db, programme.id, reason="nominee_reorder")
        await audit(
            db,
            ctx.actor,
            "nominee.reordered",
            programme_id=programme.id,
            target_type="ballot",
            target_id=ballot.id,
        )
    return {"ok": True}


@router.get("/events/{event_id}/preview")
async def preview_event(
    event_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """The participant view of an event as it would look while live (no receipts)."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        event = await _get_event(db, programme, event_id, lock=False)
        payload = (await _event_payloads(db, programme, [event]))[0]
        events = await _list(db, programme, include_archived=False)
        now = await server_now(db)
    round_no = next((i for i, e in enumerate(events, start=1) if e.id == event.id), None)
    return {
        "server_time": now,
        "timezone": programme.timezone,
        "event_count": len(events),
        "event": {
            "id": payload["id"],
            "round": round_no,
            "status": "live",
            "locked": False,
            "name": payload["name"],
            "description": payload["description"],
            "rules": payload["rules"],
            "logo_url": payload["logo_url"],
            "opened_at": now,
            "voting_deadline_at": payload["planned_end_at"],
            "closed_at": None,
            "results_published": False,
        },
        "ballots": [
            {
                "id": b["id"],
                "title": b["title"],
                "kind": b["kind"],
                "description": b["description"],
                "nominees": [
                    {
                        "id": n["id"],
                        "name": n["name"],
                        "affiliation": n["affiliation"],
                        "description": n["description"],
                        "members": n["members"],
                        "photo_url": n["photo_url"],
                    }
                    for n in b["nominees"]
                ],
                "receipt": None,
            }
            for b in payload["ballots"]
        ],
        "results": None,
        "preview": True,
    }


_ = Actor  # re-exported for type checkers of callers
