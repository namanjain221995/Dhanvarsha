"""Admin live control: current state, transitions, schedule override and resume."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import AwareDatetime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..live.hub import get_hub
from ..models import Event, ScheduleJob
from ..services import live
from ..services.programmes import active_programme, server_now
from ..services.results import ballot_results, eligible_voters
from .common import Model, no_store

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


@router.get("/live")
async def live_state(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        now = await server_now(db)
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
        counts: dict[Any, Any] = dict(
            (
                await db.execute(
                    text(
                        "SELECT event_id, count(DISTINCT participant_id) FROM votes WHERE programme_id = :p GROUP BY event_id"
                    ),
                    {"p": programme.id},
                )
            ).all()
        )
        current = next((e for e in events if e.status == "open"), None)
        current_results = None
        if current is not None:
            ballot_ids = list(
                (
                    await db.execute(
                        text("SELECT id FROM ballots WHERE event_id = :e ORDER BY position"),
                        {"e": current.id},
                    )
                ).scalars()
            )
            current_results = await ballot_results(db, ballot_ids, programme_id=programme.id)
        eligible = await eligible_voters(db, programme.id)
        signed_in = (
            await db.execute(
                text(
                    "SELECT count(*) FROM participant_sessions WHERE programme_id = :p AND revoked_at IS NULL "
                    "AND expires_at > now()"
                ),
                {"p": programme.id},
            )
        ).scalar_one()
        jobs = (
            (
                await db.execute(
                    select(ScheduleJob)
                    .where(ScheduleJob.programme_id == programme.id)
                    .order_by(ScheduleJob.updated_at.desc())
                    .limit(30)
                )
            )
            .scalars()
            .all()
        )
        worker = (
            await db.execute(
                text(
                    "SELECT worker_id, last_seen_at, now() - last_seen_at < interval '30 seconds' AS healthy "
                    "FROM worker_heartbeats ORDER BY last_seen_at DESC LIMIT 1"
                )
            )
        ).first()

    names = {e.id: e.name for e in events}
    upcoming = [e for e in events if e.status == "upcoming"]
    if programme.run_mode == "scheduled":
        upcoming.sort(key=lambda e: (e.planned_start_at is None, e.planned_start_at or now, e.position))
    next_event = upcoming[0] if upcoming else None
    hub = get_hub()

    def event_out(index: int, e: Event) -> dict[str, Any]:
        effective_status = e.status
        if e.status == "open" and e.voting_deadline_at and e.voting_deadline_at <= now:
            effective_status = "closing"
        return {
            "id": str(e.id),
            "round": index,
            "name": e.name,
            "status": e.status,
            "effective_status": effective_status,
            "planned_start_at": e.planned_start_at,
            "planned_end_at": e.planned_end_at,
            "opened_at": e.opened_at,
            "voting_deadline_at": e.voting_deadline_at,
            "closed_at": e.closed_at,
            "close_reason": e.close_reason,
            "opened_by": e.opened_by,
            "results_published": e.results_published_at is not None,
            "voters": int(counts.get(e.id, 0)),
        }

    rounds = {e.id: i for i, e in enumerate(events, start=1)}
    return {
        "server_time": now,
        "state_version": programme.state_version,
        "live_version": programme.live_version,
        "programme": {
            "name": programme.name,
            "timezone": programme.timezone,
            "run_mode": programme.run_mode,
            "schedule_suspended": programme.schedule_suspended,
            "schedule_suspended_at": programme.schedule_suspended_at,
            "schedule_revision": programme.schedule_revision,
            "login_opens_at": programme.login_opens_at,
            "login_closes_at": programme.login_closes_at,
            "ended_at": programme.ended_at,
        },
        "turnout": {"eligible": eligible, "signed_in": int(signed_in)},
        "current_event": event_out(rounds[current.id], current) | {"results": current_results}
        if current
        else None,
        "next_event": event_out(rounds[next_event.id], next_event) if next_event else None,
        "events": [event_out(i, e) for i, e in enumerate(events, start=1)],
        "schedule_jobs": [
            {
                "id": str(j.id),
                "event_id": str(j.event_id),
                "event_name": names.get(j.event_id, "Archived event"),
                "action": j.action,
                "run_at": j.run_at,
                "window_end_at": j.window_end_at,
                "revision": j.schedule_revision,
                "status": j.status,
                "detail": j.result_detail,
                "executed_at": j.executed_at,
            }
            for j in jobs
        ],
        "worker": {
            "id": worker.worker_id if worker else None,
            "last_seen_at": worker.last_seen_at if worker else None,
            "healthy": bool(worker and worker.healthy),
        },
        "live_channel": {
            "listener_connected": bool(hub and hub.listener_connected),
            "connections_here": hub.connection_count if hub else 0,
        },
    }


class OpenIn(Model):
    event_id: uuid.UUID
    expected_live_version: int
    deadline_at: AwareDatetime | None = None


class CloseIn(Model):
    event_id: uuid.UUID
    expected_live_version: int


class DeadlineIn(Model):
    event_id: uuid.UUID
    expected_live_version: int
    deadline_at: AwareDatetime


class VersionIn(Model):
    expected_live_version: int


@router.post("/live/open")
async def open_event(
    body: OpenIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.open_event(
        db, programme.id, body.event_id, body.deadline_at, body.expected_live_version, ctx.actor
    )
    return {"ok": True}


@router.post("/live/close")
async def close_event(
    body: CloseIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.close_event(db, programme.id, body.event_id, body.expected_live_version, ctx.actor)
    return {"ok": True}


@router.post("/live/start-next")
async def start_next(
    body: OpenIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.start_next(
        db, programme.id, body.event_id, body.deadline_at, body.expected_live_version, ctx.actor
    )
    return {"ok": True}


@router.post("/live/deadline")
async def change_deadline(
    body: DeadlineIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.change_deadline(
        db, programme.id, body.event_id, body.deadline_at, body.expected_live_version, ctx.actor
    )
    return {"ok": True}


@router.post("/live/end-programme")
async def end_programme(
    body: VersionIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.end_programme(db, programme.id, body.expected_live_version, ctx.actor)
    return {"ok": True}


@router.post("/live/reopen-programme")
async def reopen_programme(
    body: VersionIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    await live.reopen_programme(db, programme.id, body.expected_live_version, ctx.actor)
    return {"ok": True}


@router.post("/live/restart-programme")
async def restart_programme(
    body: VersionIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Start again from round 1 with fresh copies of every event (old rounds are archived)."""
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    summary = await live.restart_programme(db, programme.id, body.expected_live_version, ctx.actor)
    return {"ok": True, **summary}


@router.get("/schedule/preview")
async def schedule_preview(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
        now = await server_now(db)
        plan = await live.schedule_preview(db, programme, now)
    return {"server_time": now, "live_version": programme.live_version, "plan": plan}


@router.post("/schedule/resume")
async def resume_schedule(
    body: VersionIn,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    async with db.begin():
        programme = await active_programme(db)
    jobs = await live.resume_schedule(db, programme.id, body.expected_live_version, ctx.actor)
    return {"ok": True, "jobs": jobs}
