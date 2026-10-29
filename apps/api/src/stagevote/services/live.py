"""Live control: event transitions, the schedule, manual override and deadline sweeps.

Every transition runs in one transaction that holds the programme live lock in
EXCLUSIVE mode (see state.py for the lock order) and re-reads state after acquiring it.
Admin actions carry the `live_version` the admin saw; a mismatch means another admin (or
the scheduler) changed the live state first, and the action is rejected as stale.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import AppError, conflict, not_found
from ..models import Ballot, Event, Nominee, Programme, ScheduleJob
from ..state import Actor, audit, bump_state, db_now, lock_live_exclusive
from .programmes import login_phase

MIN_VOTING_SECONDS = 15


async def _lock_programme(db: AsyncSession, programme_id: uuid.UUID) -> Programme:
    return (
        await db.execute(
            select(Programme)
            .where(Programme.id == programme_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def begin_transition(
    db: AsyncSession, programme_id: uuid.UUID, expected_live_version: int | None
) -> tuple[Programme, datetime]:
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    await lock_live_exclusive(db, programme_id)
    programme = await _lock_programme(db, programme_id)
    if expected_live_version is not None and programme.live_version != expected_live_version:
        raise conflict(
            "STALE_STATE",
            "The live state changed since you loaded this page. Review the latest state and try again.",
            {"live_version": programme.live_version},
        )
    return programme, await db_now(db)


async def _lock_event(db: AsyncSession, programme_id: uuid.UUID, event_id: uuid.UUID) -> Event:
    event = (
        await db.execute(
            select(Event)
            .where(Event.id == event_id, Event.programme_id == programme_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if event is None:
        raise not_found("Event")
    return event


async def _open_events(db: AsyncSession, programme_id: uuid.UUID) -> list[Event]:
    return list(
        (
            await db.execute(
                select(Event)
                .where(Event.programme_id == programme_id, Event.status == "open")
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _ballot_problems(db: AsyncSession, event_id: uuid.UUID) -> str | None:
    """Why the event cannot open yet, or None. A round with no ballots may open (a show
    with no voting), and a typed-answer question needs nothing before it opens."""
    rows = (
        await db.execute(
            select(Ballot.id, Ballot.title, Ballot.kind, func.count(Nominee.id).label("n"))
            .outerjoin(Nominee, Nominee.ballot_id == Ballot.id)
            .where(Ballot.event_id == event_id)
            .group_by(Ballot.id, Ballot.title, Ballot.kind, Ballot.position, Ballot.created_at)
            .order_by(Ballot.position, Ballot.created_at)
        )
    ).all()
    problems = []
    empty = [r.title for r in rows if r.kind in ("team", "individual") and r.n == 0]
    if empty:
        problems.append(f"Add nominees to: {', '.join(empty)}.")
    short = [r.title for r in rows if r.kind == "mcq" and r.n < 2]
    if short:
        problems.append(f"Add at least two answers to: {', '.join(short)}.")
    return " ".join(problems) or None


def _suspend_schedule(programme: Programme, now: datetime) -> bool:
    if programme.run_mode == "scheduled" and not programme.schedule_suspended:
        programme.schedule_suspended = True
        programme.schedule_suspended_at = now
        return True
    return False


def _resolve_deadline(programme: Programme, now: datetime, requested: datetime | None) -> datetime:
    deadline = requested or programme.login_closes_at
    if deadline is None:
        raise AppError(
            "DEADLINE_REQUIRED",
            "Choose a voting deadline, or set the programme access closing time first.",
            422,
        )
    if deadline <= now + timedelta(seconds=MIN_VOTING_SECONDS):
        raise AppError("DEADLINE_TOO_SOON", "The voting deadline must be at least 15 seconds from now.", 422)
    if programme.login_closes_at is not None and deadline > programme.login_closes_at:
        raise AppError(
            "DEADLINE_AFTER_ACCESS",
            "The voting deadline cannot be later than the programme access closing time.",
            422,
        )
    return deadline


async def _freeze(db: AsyncSession, event: Event, now: datetime) -> None:
    event.frozen_at = event.frozen_at or now
    await db.execute(
        update(Ballot).where(Ballot.event_id == event.id, Ballot.frozen_at.is_(None)).values(frozen_at=now)
    )


def _close(event: Event, now: datetime, reason: str) -> None:
    deadline = event.voting_deadline_at
    event.status = "closed"
    event.closed_at = min(now, deadline) if deadline else now
    event.close_reason = "deadline" if deadline and deadline <= now else reason
    event.version += 1


async def _open(
    db: AsyncSession, programme: Programme, event: Event, now: datetime, deadline: datetime, opened_by: str
) -> None:
    if event.archived_at is not None:
        raise conflict("EVENT_ARCHIVED", "Archived events cannot be opened.")
    if event.status == "closed":
        raise conflict("EVENT_CLOSED", "This event has closed. Closed events never reopen.")
    if event.status == "open":
        raise conflict("EVENT_ALREADY_OPEN", "This event is already open.")
    problem = await _ballot_problems(db, event.id)
    if problem:
        raise conflict("EVENT_NOT_READY", problem)
    if login_phase(programme, now) != "open":
        raise conflict(
            "PROGRAMME_ACCESS_CLOSED",
            "Voting can only open while programme access is open. Adjust the access window first.",
        )
    event.status = "open"
    event.opened_at = now
    event.voting_deadline_at = deadline
    event.opened_by = opened_by
    event.opened_schedule_revision = programme.schedule_revision if opened_by == "schedule" else None
    event.version += 1
    await _freeze(db, event, now)


# ---- manual admin actions -----------------------------------------------------


async def open_event(
    db: AsyncSession,
    programme_id: uuid.UUID,
    event_id: uuid.UUID,
    deadline_at: datetime | None,
    expected_live_version: int,
    actor: Actor,
) -> None:
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        if programme.ended_at is not None:
            raise conflict("PROGRAMME_ENDED", "The programme has ended. Resume it before opening events.")
        if await _open_events(db, programme_id):
            raise conflict("ANOTHER_EVENT_OPEN", "Another event is open. Use “Start next event” to switch.")
        event = await _lock_event(db, programme_id, event_id)
        deadline = _resolve_deadline(programme, now, deadline_at)
        await _open(db, programme, event, now, deadline, "manual")
        suspended = _suspend_schedule(programme, now)
        await bump_state(db, programme_id, live=True, reason="open")
        await audit(
            db,
            actor,
            "event.opened",
            programme_id=programme_id,
            target_type="event",
            target_id=event.id,
            details={"deadline": deadline.isoformat(), "by": "manual", "schedule_suspended": suspended},
        )


async def close_event(
    db: AsyncSession, programme_id: uuid.UUID, event_id: uuid.UUID, expected_live_version: int, actor: Actor
) -> None:
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        event = await _lock_event(db, programme_id, event_id)
        if event.status != "open":
            raise conflict("EVENT_NOT_OPEN", "This event is not open.")
        _close(event, now, "manual")
        suspended = _suspend_schedule(programme, now)
        await bump_state(db, programme_id, live=True, reason="close")
        await audit(
            db,
            actor,
            "event.closed",
            programme_id=programme_id,
            target_type="event",
            target_id=event.id,
            details={"reason": event.close_reason, "schedule_suspended": suspended},
        )


async def start_next(
    db: AsyncSession,
    programme_id: uuid.UUID,
    next_event_id: uuid.UUID,
    deadline_at: datetime | None,
    expected_live_version: int,
    actor: Actor,
) -> None:
    """Atomically close the open event (if any) and open the selected next event."""
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        if programme.ended_at is not None:
            raise conflict("PROGRAMME_ENDED", "The programme has ended. Resume it before opening events.")
        closed: list[str] = []
        for current in await _open_events(db, programme_id):
            if current.id == next_event_id:
                raise conflict("EVENT_ALREADY_OPEN", "This event is already open.")
            _close(current, now, "superseded")
            closed.append(str(current.id))
        await db.flush()  # the partial unique index sees the close before the open
        event = await _lock_event(db, programme_id, next_event_id)
        deadline = _resolve_deadline(programme, now, deadline_at)
        await _open(db, programme, event, now, deadline, "manual")
        suspended = _suspend_schedule(programme, now)
        await bump_state(db, programme_id, live=True, reason="start_next")
        await audit(
            db,
            actor,
            "event.started_next",
            programme_id=programme_id,
            target_type="event",
            target_id=event.id,
            details={"closed": closed, "deadline": deadline.isoformat(), "schedule_suspended": suspended},
        )


async def change_deadline(
    db: AsyncSession,
    programme_id: uuid.UUID,
    event_id: uuid.UUID,
    deadline_at: datetime,
    expected_live_version: int,
    actor: Actor,
) -> None:
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        event = await _lock_event(db, programme_id, event_id)
        if event.status != "open" or (event.voting_deadline_at and event.voting_deadline_at <= now):
            raise conflict(
                "EVENT_NOT_OPEN", "Only an event that is still accepting votes can get a new deadline."
            )
        previous = event.voting_deadline_at
        event.voting_deadline_at = _resolve_deadline(programme, now, deadline_at)
        event.version += 1
        suspended = _suspend_schedule(programme, now)
        await bump_state(db, programme_id, live=True, reason="deadline")
        await audit(
            db,
            actor,
            "event.deadline_changed",
            programme_id=programme_id,
            target_type="event",
            target_id=event.id,
            details={
                "from": previous.isoformat() if previous else None,
                "to": event.voting_deadline_at.isoformat(),
                "schedule_suspended": suspended,
            },
        )


async def end_programme(
    db: AsyncSession, programme_id: uuid.UUID, expected_live_version: int, actor: Actor
) -> None:
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        closed = []
        for current in await _open_events(db, programme_id):
            _close(current, now, "programme_ended")
            closed.append(str(current.id))
        programme.ended_at = now
        _suspend_schedule(programme, now)
        await bump_state(db, programme_id, live=True, reason="end")
        await audit(db, actor, "programme.ended", programme_id=programme_id, details={"closed": closed})


async def reopen_programme(
    db: AsyncSession, programme_id: uuid.UUID, expected_live_version: int, actor: Actor
) -> None:
    async with db.begin():
        programme, _ = await begin_transition(db, programme_id, expected_live_version)
        if programme.ended_at is None:
            raise conflict("PROGRAMME_NOT_ENDED", "The programme has not ended.")
        programme.ended_at = None
        await bump_state(db, programme_id, live=True, reason="reopen")
        await audit(db, actor, "programme.reopened", programme_id=programme_id)


async def restart_programme(
    db: AsyncSession, programme_id: uuid.UUID, expected_live_version: int, actor: Actor
) -> dict[str, int]:
    """Start the programme again from round 1.

    Votes are never deleted or changed (the database refuses it). Instead every current event
    is closed if open and archived, together with its ballots and votes, and an identical fresh
    copy (name, description, rules, logo, ballots, nominees, team members) is created in the
    same order with status `upcoming`. Participants can vote again on the copies; the archived
    rounds keep their votes and audit trail but are hidden from participants and results.
    Planned windows are copied only when they are still in the future.
    """
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        events = list(
            (
                await db.execute(
                    select(Event)
                    .where(Event.programme_id == programme_id, Event.archived_at.is_(None))
                    .order_by(Event.position, Event.created_at)
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        if not events:
            raise conflict("NOTHING_TO_RESTART", "There are no events to restart.")
        if programme.ended_at is None and all(e.status == "upcoming" for e in events):
            raise conflict(
                "ALREADY_AT_START", "No event has been opened yet: the programme is already at round 1."
            )
        event_ids = [e.id for e in events]
        ballots = list(
            (
                await db.execute(
                    select(Ballot)
                    .where(Ballot.event_id.in_(event_ids))
                    .order_by(Ballot.position, Ballot.created_at)
                )
            ).scalars()
        )
        nominees = list(
            (
                await db.execute(
                    select(Nominee)
                    .where(Nominee.ballot_id.in_([b.id for b in ballots]))
                    .order_by(Nominee.position, Nominee.created_at)
                )
            ).scalars()
        )
        ballots_by_event: dict[uuid.UUID, list[Ballot]] = {}
        for ballot in ballots:
            ballots_by_event.setdefault(ballot.event_id, []).append(ballot)
        nominees_by_ballot: dict[uuid.UUID, list[Nominee]] = {}
        for nominee in nominees:
            nominees_by_ballot.setdefault(nominee.ballot_id, []).append(nominee)

        closed = 0
        copies: list[tuple[Event, Event]] = []
        for event in events:
            if event.status == "open":
                _close(event, now, "restarted")
                closed += 1
            event.archived_at = now
            event.version += 1
            future = event.planned_start_at is not None and event.planned_start_at > now
            copy = Event(
                id=uuid.uuid4(),
                programme_id=programme_id,
                position=event.position,
                name=event.name,
                description=event.description,
                rules=event.rules,
                logo_asset_id=event.logo_asset_id,
                status="upcoming",
                planned_start_at=event.planned_start_at if future else None,
                planned_end_at=event.planned_end_at if future else None,
            )
            db.add(copy)
            copies.append((event, copy))
        # The models have no ORM relationships, so insert parents before children explicitly.
        await db.flush()
        ballot_copies: list[tuple[Ballot, Ballot]] = []
        for event, copy in copies:
            for ballot in ballots_by_event.get(event.id, []):
                ballot_copy = Ballot(
                    id=uuid.uuid4(),
                    programme_id=programme_id,
                    event_id=copy.id,
                    position=ballot.position,
                    title=ballot.title,
                    kind=ballot.kind,
                    description=ballot.description,
                )
                db.add(ballot_copy)
                ballot_copies.append((ballot, ballot_copy))
        await db.flush()
        for ballot, ballot_copy in ballot_copies:
            for nominee in nominees_by_ballot.get(ballot.id, []):
                db.add(
                    Nominee(
                        ballot_id=ballot_copy.id,
                        position=nominee.position,
                        name=nominee.name,
                        affiliation=nominee.affiliation,
                        description=nominee.description,
                        members=list(nominee.members),
                        photo_asset_id=nominee.photo_asset_id,
                    )
                )
        programme.ended_at = None
        await db.flush()
        scheduled = await regenerate_schedule(db, programme, now)
        await bump_state(db, programme_id, live=True, reason="restart")
        summary = {"events": len(events), "closed": closed, "scheduled": scheduled}
        await audit(db, actor, "programme.restarted", programme_id=programme_id, details=summary)
    return summary


# ---- schedule -----------------------------------------------------------------


async def regenerate_schedule(db: AsyncSession, programme: Programme, now: datetime) -> int:
    """New schedule revision: pending jobs of older revisions become stale.

    Caller holds the exclusive live lock and the programme row lock.
    """
    programme.schedule_revision += 1
    await db.execute(
        update(ScheduleJob)
        .where(ScheduleJob.programme_id == programme.id, ScheduleJob.status == "pending")
        .values(status="stale", result_detail="Replaced by a newer schedule revision", updated_at=now)
    )
    if programme.run_mode != "scheduled" or programme.schedule_suspended:
        return 0
    events = (
        await db.execute(
            select(Event).where(
                Event.programme_id == programme.id,
                Event.status == "upcoming",
                Event.archived_at.is_(None),
                Event.planned_start_at.is_not(None),
                Event.planned_end_at > now,
            )
        )
    ).scalars()
    count = 0
    for event in events:
        assert event.planned_start_at is not None and event.planned_end_at is not None
        db.add(
            ScheduleJob(
                programme_id=programme.id,
                event_id=event.id,
                action="open",
                run_at=event.planned_start_at,
                window_end_at=event.planned_end_at,
                schedule_revision=programme.schedule_revision,
            )
        )
        count += 1
    await db.flush()
    return count


async def resume_schedule(
    db: AsyncSession, programme_id: uuid.UUID, expected_live_version: int, actor: Actor
) -> int:
    async with db.begin():
        programme, now = await begin_transition(db, programme_id, expected_live_version)
        if programme.run_mode != "scheduled":
            raise conflict("NOT_SCHEDULED", "Switch the programme to scheduled mode first.")
        programme.schedule_suspended = False
        programme.schedule_suspended_at = None
        jobs = await regenerate_schedule(db, programme, now)
        await bump_state(db, programme_id, live=True, reason="resume")
        await audit(
            db,
            actor,
            "schedule.resumed",
            programme_id=programme_id,
            details={"revision": programme.schedule_revision, "jobs": jobs},
        )
        return jobs


async def schedule_preview(db: AsyncSession, programme: Programme, now: datetime) -> list[dict[str, Any]]:
    """What resuming the schedule would do, for the admin confirmation dialog."""
    events = (
        await db.execute(
            select(Event)
            .where(Event.programme_id == programme.id, Event.archived_at.is_(None))
            .order_by(Event.position, Event.created_at)
        )
    ).scalars()
    plan: list[dict[str, Any]] = []
    open_now = None
    immediate = None
    for event in events:
        if event.status == "open":
            open_now = event
            continue
        if event.status != "upcoming" or event.planned_start_at is None or event.planned_end_at is None:
            continue
        if event.planned_end_at <= now:
            outcome = "window_passed"
        elif event.planned_start_at <= now:
            outcome = "opens_now"
            immediate = immediate or event
        else:
            outcome = "opens_later"
        plan.append(
            {
                "event_id": str(event.id),
                "name": event.name,
                "outcome": outcome,
                "planned_start_at": event.planned_start_at,
                "planned_end_at": event.planned_end_at,
            }
        )
    if open_now is not None:
        plan.insert(
            0,
            {
                "event_id": str(open_now.id),
                "name": open_now.name,
                "outcome": "closes_when_next_opens" if immediate else "stays_open_until_deadline",
                "planned_start_at": open_now.planned_start_at,
                "planned_end_at": open_now.voting_deadline_at,
            },
        )
    return plan


async def run_due_schedule_jobs(db: AsyncSession) -> int:
    """Execute due scheduled opens. Idempotent and safe with several workers."""
    due = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT programme_id FROM schedule_jobs WHERE status = 'pending' AND run_at <= now()"
                )
            )
        )
        .scalars()
        .all()
    )
    await db.commit()
    executed = 0
    for programme_id in due:
        async with db.begin():
            programme, now = await begin_transition(db, programme_id, None)
            jobs = (
                (
                    await db.execute(
                        select(ScheduleJob)
                        .where(
                            ScheduleJob.programme_id == programme_id,
                            ScheduleJob.status == "pending",
                            ScheduleJob.run_at <= now,
                        )
                        .order_by(ScheduleJob.run_at)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                await _execute_open_job(db, programme, job, now)
                executed += 1
    return executed


async def _execute_open_job(db: AsyncSession, programme: Programme, job: ScheduleJob, now: datetime) -> None:
    system = Actor.system()
    job.attempts += 1
    job.executed_at = now

    def finish(status: str, detail: str) -> None:
        job.status = status
        job.result_detail = detail[:300]

    if job.schedule_revision != programme.schedule_revision:
        finish("stale", "Schedule changed after this job was created")
        return
    if programme.run_mode != "scheduled":
        finish("skipped", "Programme is in manual mode")
        return
    if programme.schedule_suspended:
        finish("skipped", "Manual override active")
        return
    if programme.ended_at is not None:
        finish("skipped", "Programme has ended")
        return
    event = await _lock_event(db, programme.id, job.event_id)
    if event.archived_at is not None or event.status != "upcoming":
        # Never reopen a closed event and never touch one that is already open.
        finish("skipped", f"Event is already {event.status if event.archived_at is None else 'archived'}")
        return
    if event.planned_start_at != job.run_at or event.planned_end_at != job.window_end_at:
        finish("stale", "Event schedule no longer matches this job")
        return
    if now >= job.window_end_at:
        finish(
            "missed", "The scheduled window ended before the transition could run; the event was not opened"
        )
        await audit(
            db,
            system,
            "schedule.missed",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
            details={"planned_start": job.run_at.isoformat(), "planned_end": job.window_end_at.isoformat()},
        )
        await bump_state(db, programme.id, live=True, reason="schedule_missed")
        return
    if login_phase(programme, now) != "open":
        finish("missed", "Programme access was not open at the scheduled time")
        await audit(
            db,
            system,
            "schedule.missed",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
            details={"reason": "access_window"},
        )
        await bump_state(db, programme.id, live=True, reason="schedule_missed")
        return
    problem = await _ballot_problems(db, event.id)
    if problem:
        finish("failed", problem)
        await audit(
            db,
            system,
            "schedule.failed",
            programme_id=programme.id,
            target_type="event",
            target_id=event.id,
            details={"reason": problem},
        )
        await bump_state(db, programme.id, live=True, reason="schedule_failed")
        return
    for current in await _open_events(db, programme.id):
        _close(current, now, "superseded_by_schedule")
    await db.flush()
    deadline = job.window_end_at
    if programme.login_closes_at is not None and deadline > programme.login_closes_at:
        deadline = programme.login_closes_at
    await _open(db, programme, event, now, deadline, "schedule")
    finish("done", "Opened on schedule")
    await bump_state(db, programme.id, live=True, reason="schedule_open")
    await audit(
        db,
        system,
        "event.opened",
        programme_id=programme.id,
        target_type="event",
        target_id=event.id,
        details={"deadline": deadline.isoformat(), "by": "schedule", "revision": job.schedule_revision},
    )


async def sweep_deadlines(db: AsyncSession) -> int:
    """Close open events whose effective deadline has passed.

    Votes are already refused at the deadline by the vote transaction; this only makes
    the stored status catch up.
    """
    due = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT programme_id FROM events WHERE status = 'open' AND voting_deadline_at <= now()"
                )
            )
        )
        .scalars()
        .all()
    )
    await db.commit()
    closed = 0
    for programme_id in due:
        async with db.begin():
            _, now = await begin_transition(db, programme_id, None)
            events = [
                e
                for e in await _open_events(db, programme_id)
                if e.voting_deadline_at and e.voting_deadline_at <= now
            ]
            for event in events:
                _close(event, now, "deadline")
                closed += 1
                await audit(
                    db,
                    Actor.system(),
                    "event.closed",
                    programme_id=programme_id,
                    target_type="event",
                    target_id=event.id,
                    details={"reason": "deadline"},
                )
            if events:
                await bump_state(db, programme_id, live=True, reason="deadline")
    return closed
