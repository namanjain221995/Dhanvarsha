"""Audience polls from a JSON file: `stagevote load-polls`.

One poll is one event (a round) with exactly one ballot: the question is the ballot title
and the answer options are its nominees, in the file's order. Questions about feelings or
skills are multiple-choice (`mcq`) ballots; votes for people are `individual`, and for
pairs or teams `team`, whose options may list their members (shown to voters as
supporting text). The default file is the Dhanvarsha audience polls shipped in
stagevote/data; its wording is the organisers' and is stored exactly as written.

Loading is one transaction under the programme live lock, like every event editor:

* Nothing changes while an event of the active programme is open.
* Each poll is matched to the non-archived event with exactly the same name, or else to
  one named "<id> — …" (the first release put the poll's id in front of every round's
  name), which takes the file's name. A missing one is created. One that has never opened gets the file's organiser note, and its
  ballot and options are replaced when they differ from the file (kind, question,
  description, option names and members; while those match, a photo, affiliation or
  description added in the editor is kept). One that has opened keeps its ballot and
  options, which froze when voting opened; only its organiser note is updated.
* The polls take the first positions in file order; every other event follows in its
  current order. With `archive_others` every other event is archived as the console's
  Archive does it, and can be restored from the console.
* Loading the same file again changes nothing.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .api.admin_events import BALLOT_TITLE_MAX, MAX_MEMBERS, ORGANISER_NOTE_MAX, MemberName, _members
from .api.common import Model, MultiLine, SingleLine
from .models import Ballot, Event, Nominee, Programme
from .services.live import begin_transition, regenerate_schedule
from .state import Actor, audit, bump_state

DEFAULT_FILE = Path(__file__).resolve().parent / "data" / "dhanvarsha_audience_polls.json"

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
KEPT = "kept (already opened)"


class PollOption(Model):
    name: SingleLine = Field(min_length=1, max_length=120)
    members: list[MemberName] = Field(default_factory=list, max_length=MAX_MEMBERS)


class PollBallot(Model):
    kind: Literal["team", "individual", "mcq"]
    title: SingleLine = Field(min_length=1, max_length=BALLOT_TITLE_MAX)
    description: str = Field(default="", max_length=2000)
    options: list[PollOption] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _check_options(self) -> PollBallot:
        if self.kind == "mcq" and len(self.options) < 2:
            raise ValueError("a multiple-choice question needs at least two options")
        if self.kind != "team" and any(o.members for o in self.options):
            raise ValueError("only the options of a team poll list members")
        repeated = [name for name, n in Counter(o.name.casefold() for o in self.options).items() if n > 1]
        if repeated:
            raise ValueError(f"the option {repeated[0]!r} is listed twice")
        return self

    def nominees(self) -> list[tuple[str, list[str]]]:
        """The options as the editor stores them: a name and, on a team poll, its members."""
        return [(o.name, _members(o.members) if self.kind == "team" else []) for o in self.options]


class Poll(Model):
    id: str = Field(default="", max_length=20)
    name: SingleLine = Field(min_length=1, max_length=120)
    organiser_note: MultiLine = Field(default="", max_length=ORGANISER_NOTE_MAX)
    ballot: PollBallot


class PollFile(Model):
    # The file may say where its wording came from ("source"); only the polls are read.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    polls: list[Poll] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_names(self) -> PollFile:
        repeated = [name for name, n in Counter(p.name for p in self.polls).items() if n > 1]
        if repeated:
            raise ValueError(f"two polls are named {repeated[0]!r}")
        return self


def read_polls(path: Path) -> list[Poll]:
    """The polls of a file, checked as the editor checks its inputs. A file that cannot be
    used stops the command with a readable message."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    try:
        return PollFile.model_validate(raw).polls
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}"
            for error in exc.errors()
        )
        raise SystemExit(f"{path} is not a usable polls file. {problems}") from exc


@dataclass
class PollOutcome:
    name: str
    outcome: str  # CREATED, UPDATED, UNCHANGED or KEPT
    changed: list[str] = field(default_factory=list)  # what changed, in words


@dataclass
class LoadReport:
    programme: str
    source: str
    polls: list[PollOutcome] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    reordered: bool = False

    @property
    def changed(self) -> bool:
        return self.reordered or bool(self.archived) or any(p.changed for p in self.polls)

    def counts(self) -> dict[str, int]:
        found = Counter(p.outcome for p in self.polls)
        return {
            "created": found[CREATED],
            "updated": found[UPDATED],
            "unchanged": found[UNCHANGED],
            "kept": found[KEPT],
            "archived": len(self.archived),
        }

    def lines(self) -> list[str]:
        """What the command prints: one line per poll, then one per archived event."""
        width = len(KEPT)
        out = [f"Loading {len(self.polls)} polls from {self.source} into “{self.programme}”:"]
        for poll in self.polls:
            if poll.outcome == UPDATED:
                note = f" ({', '.join(poll.changed)})"
            elif poll.outcome == KEPT and poll.changed:
                note = f" ({' and '.join(poll.changed)} updated)"
            else:
                note = ""
            out.append(f"  {poll.outcome:<{width}}  {poll.name}{note}")
        out.extend(f"  {'archived':<{width}}  {name}" for name in self.archived)
        counts = self.counts()
        summary = ", ".join(f"{n} {label}" for label, n in counts.items())
        out.append(f"Done: {summary}." if self.changed else f"Nothing changed: {summary}.")
        return out


def _opened(event: Event, ballots: list[Ballot], votes: int) -> bool:
    return (
        event.status != "upcoming"
        or event.frozen_at is not None
        or any(b.frozen_at is not None for b in ballots)
        or votes > 0
    )


def _shape(kind: str, title: str, description: str, nominees: list[tuple[str, list[str]]]) -> dict[str, Any]:
    return {"kind": kind, "title": title, "description": description, "options": nominees}


def _current_shape(ballots: list[Ballot], nominees: dict[uuid.UUID, list[Nominee]]) -> list[dict[str, Any]]:
    return [
        _shape(b.kind, b.title, b.description, [(n.name, list(n.members)) for n in nominees.get(b.id, [])])
        for b in ballots
    ]


def _wanted_shape(ballot: PollBallot) -> list[dict[str, Any]]:
    return [_shape(ballot.kind, ballot.title, ballot.description, ballot.nominees())]


async def _contents(
    db: AsyncSession, events: list[Event]
) -> tuple[dict[uuid.UUID, list[Ballot]], dict[uuid.UUID, list[Nominee]], dict[uuid.UUID, int]]:
    """Ballots per event, nominees per ballot and votes per event."""
    ids = [e.id for e in events]
    if not ids:
        return {}, {}, {}
    ballots = list(
        (
            await db.execute(
                select(Ballot).where(Ballot.event_id.in_(ids)).order_by(Ballot.position, Ballot.created_at)
            )
        ).scalars()
    )
    nominees = (
        list(
            (
                await db.execute(
                    select(Nominee)
                    .where(Nominee.ballot_id.in_([b.id for b in ballots]))
                    .order_by(Nominee.position, Nominee.created_at)
                )
            ).scalars()
        )
        if ballots
        else []
    )
    votes: dict[Any, Any] = dict(
        (
            await db.execute(
                text("SELECT event_id, count(*) FROM votes WHERE event_id = ANY(:ids) GROUP BY event_id"),
                {"ids": ids},
            )
        ).all()
    )
    ballots_of: dict[uuid.UUID, list[Ballot]] = {}
    for ballot in ballots:
        ballots_of.setdefault(ballot.event_id, []).append(ballot)
    nominees_of: dict[uuid.UUID, list[Nominee]] = {}
    for nominee in nominees:
        nominees_of.setdefault(nominee.ballot_id, []).append(nominee)
    return ballots_of, nominees_of, {k: int(v) for k, v in votes.items()}


async def _add_ballot(db: AsyncSession, programme: Programme, event: Event, poll: PollBallot) -> None:
    ballot = Ballot(
        programme_id=programme.id,
        event_id=event.id,
        position=1,
        title=poll.title,
        kind=poll.kind,
        description=poll.description,
    )
    db.add(ballot)
    await db.flush()
    for position, (name, members) in enumerate(poll.nominees(), start=1):
        db.add(Nominee(ballot_id=ballot.id, position=position, name=name, members=members))
    await db.flush()


async def load_polls(
    db: AsyncSession,
    polls: list[Poll],
    *,
    archive_others: bool = False,
    source: str = "",
    actor: Actor | None = None,
) -> LoadReport:
    """Put the polls into the active programme (see the module docstring). The caller owns
    the transaction: a refusal raises SystemExit before anything is written."""
    actor = actor or Actor.system("cli")
    active = (await db.execute(select(Programme).where(Programme.is_active.is_(True)))).scalar_one_or_none()
    if active is None:
        raise SystemExit("Run `stagevote bootstrap` first.")
    programme, now = await begin_transition(db, active.id, None)
    events = list(
        (
            await db.execute(
                select(Event)
                .where(Event.programme_id == programme.id)
                .order_by(Event.position, Event.created_at)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    open_now = [e.name for e in events if e.status == "open"]
    if open_now:
        raise SystemExit(
            f"“{open_now[0]}” is open. Close it in Live control, then load the polls again. "
            "Nothing was changed."
        )
    current = [e for e in events if e.archived_at is None]
    by_name: dict[str, list[Event]] = {}
    for event in current:
        by_name.setdefault(event.name, []).append(event)
    matched: dict[str, Event] = {}
    for poll in polls:
        candidates = by_name.get(poll.name) or (
            [e for e in current if e.name.startswith(f"{poll.id} — ")] if poll.id else []
        )
        if len(candidates) > 1:
            raise SystemExit(
                f"Two events are named “{candidates[0].name}”. Rename or archive one of them, then "
                "load the polls again. Nothing was changed."
            )
        if candidates:
            matched[poll.name] = candidates[0]
    ballots_of, nominees_of, votes_of = await _contents(db, list(matched.values()))

    report = LoadReport(programme.name, source)
    placed: list[Event] = []
    for poll in polls:
        found = matched.get(poll.name)
        if found is None:
            event = Event(
                programme_id=programme.id,
                position=0,  # placed below with the others
                name=poll.name,
                description="",
                rules="",
                organiser_note=poll.organiser_note,
            )
            db.add(event)
            await db.flush()
            await _add_ballot(db, programme, event, poll.ballot)
            await audit(
                db,
                actor,
                "event.created",
                programme_id=programme.id,
                target_type="event",
                target_id=event.id,
                details={"name": event.name, "via": "load-polls"},
            )
            report.polls.append(PollOutcome(poll.name, CREATED, ["event"]))
            placed.append(event)
            continue

        event = found
        ballots = ballots_of.get(event.id, [])
        opened = _opened(event, ballots, votes_of.get(event.id, 0))
        changes: dict[str, Any] = {}
        changed: list[str] = []
        if event.name != poll.name:
            changes["name"] = {"from": event.name, "to": poll.name}
            event.name = poll.name
            changed.append("name")
        if event.organiser_note != poll.organiser_note:
            changes["organiser_note"] = {"from": event.organiser_note, "to": poll.organiser_note}
            event.organiser_note = poll.organiser_note
            changed.append("organiser note")
        # Ballots and options froze when voting opened; only a round that never opened is
        # brought in line with the file.
        if not opened:
            have, want = _current_shape(ballots, nominees_of), _wanted_shape(poll.ballot)
            if have != want:
                changes["ballots"] = {"from": have, "to": want}
                if ballots:
                    old = [b.id for b in ballots]
                    await db.execute(delete(Nominee).where(Nominee.ballot_id.in_(old)))
                    await db.execute(delete(Ballot).where(Ballot.id.in_(old)))
                await _add_ballot(db, programme, event, poll.ballot)
                changed.append("question and options")
        if changes:
            event.version += 1
            await audit(
                db,
                actor,
                "event.presentation_corrected" if event.frozen_at else "event.updated",
                programme_id=programme.id,
                target_type="event",
                target_id=event.id,
                details={**changes, "via": "load-polls"},
            )
        outcome = KEPT if opened else UPDATED if changes else UNCHANGED
        report.polls.append(PollOutcome(poll.name, outcome, changed))
        placed.append(event)

    # The polls first, in file order; every other event keeps its order after them.
    placed_ids = {e.id for e in placed}
    others = [e for e in events if e.id not in placed_ids]
    for position, event in enumerate(placed + others, start=1):
        if event.position != position:
            event.position = position
            report.reordered = True
    if report.reordered:
        await audit(
            db,
            actor,
            "event.reordered",
            programme_id=programme.id,
            details={"order": [str(e.id) for e in placed + others], "via": "load-polls"},
        )

    schedule_touched = False
    if archive_others:
        for event in current:
            if event.id in placed_ids:
                continue
            event.archived_at = now
            event.version += 1
            schedule_touched = schedule_touched or event.planned_start_at is not None
            report.archived.append(event.name)
            await audit(
                db,
                actor,
                "event.archived",
                programme_id=programme.id,
                target_type="event",
                target_id=event.id,
                details={"via": "load-polls"},
            )
    await db.flush()
    if schedule_touched and programme.run_mode == "scheduled":
        await regenerate_schedule(db, programme, now)
    if report.changed:
        # Live too: round numbers and the next round change, so open consoles reload.
        await bump_state(db, programme.id, live=True, reason="load_polls")
        await audit(
            db,
            actor,
            "polls.loaded",
            programme_id=programme.id,
            details={"source": source, **report.counts()},
        )
    return report
