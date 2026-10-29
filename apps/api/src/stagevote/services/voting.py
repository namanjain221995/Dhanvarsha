"""The vote transaction.

Acceptance point
----------------
A vote is accepted when, inside one transaction and while holding the programme live
lock in SHARED mode, the check below passes using `clock_timestamp()` evaluated *after*
every lock was acquired:

    session not revoked/expired, participant enabled and activated,
    programme access window open, event OPEN, now < effective voting deadline,
    ballot belongs to the event, nominee belongs to the ballot (or, on a text ballot,
    the typed answer is 1-500 characters).

Live transitions (close, start-next, deadline change, scheduled open, deadline sweep)
take the same lock in EXCLUSIVE mode, so a transition waits for in-flight votes to
commit and votes that arrive meanwhile wait for the transition and then see its result.
The race between a vote and a close therefore has exactly one consistent outcome.

The vote row, its receipt, the idempotency record and the outbox notification are
written in the same transaction; success is returned only after COMMIT.

Typed answers
-------------
An answer to a text ballot is a vote row with `answer_text` and no nominee, so it goes
through exactly this transaction: same locks, same checks, same idempotency and receipt.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..deps import REVOKE_MESSAGES, ParticipantContext, revoke_message
from ..errors import AppError, not_found
from ..models import ANSWER_MAX_LENGTH
from ..security import new_receipt_code, request_hash
from ..state import emit, lock_live_shared

_IDEMPOTENCY_KEY_MAX = 80
# Control characters other than tab and line breaks have no place in a typed answer (and
# PostgreSQL text cannot hold NUL at all).
_ANSWER_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Receipt:
    receipt_code: str
    ballot_id: uuid.UUID
    event_id: uuid.UUID
    ballot_kind: str
    nominee_id: uuid.UUID | None  # None for a typed answer
    nominee_name: str | None
    answer: str | None  # the typed text, for a text ballot
    ballot_title: str
    event_name: str
    accepted_at: datetime

    def public(self) -> dict[str, Any]:
        return {
            "receipt_code": self.receipt_code,
            "ballot_id": str(self.ballot_id),
            "event_id": str(self.event_id),
            "ballot_kind": self.ballot_kind,
            "nominee_id": str(self.nominee_id) if self.nominee_id else None,
            "nominee_name": self.nominee_name,
            "answer": self.answer,
            "ballot_title": self.ballot_title,
            "event_name": self.event_name,
            "accepted_at": self.accepted_at.isoformat(),
        }


_RECEIPT_SQL = """
    SELECT v.receipt_code, v.ballot_id, v.event_id, v.nominee_id, v.answer_text, v.idempotency_key,
           v.request_hash, v.created_at, n.name AS nominee_name, b.kind AS ballot_kind,
           b.title AS ballot_title, e.name AS event_name
    FROM votes v
    LEFT JOIN nominees n ON n.id = v.nominee_id
    JOIN ballots b ON b.id = v.ballot_id
    JOIN events e ON e.id = v.event_id
    WHERE v.participant_id = :pid AND {where}
"""


def _receipt(row: Any) -> Receipt:
    return Receipt(
        receipt_code=row.receipt_code,
        ballot_id=row.ballot_id,
        event_id=row.event_id,
        ballot_kind=row.ballot_kind,
        nominee_id=row.nominee_id,
        nominee_name=row.nominee_name,
        answer=row.answer_text,
        ballot_title=row.ballot_title,
        event_name=row.event_name,
        accepted_at=row.created_at,
    )


def _payload_hash(ballot_id: uuid.UUID, nominee_id: uuid.UUID | None, answer: str | None) -> str:
    """What an Idempotency-Key is bound to. A vote for a nominee hashes exactly as before
    typed answers existed, so a retry across the upgrade still matches its original."""
    if answer is None:
        return request_hash(str(ballot_id), str(nominee_id))
    if nominee_id is None:
        return request_hash(str(ballot_id), "answer", answer)
    return request_hash(str(ballot_id), str(nominee_id), "answer", answer)


def _check_choice(kind: str, nominee_id: uuid.UUID | None, answer: str | None) -> None:
    """The request must carry exactly the field its ballot kind needs."""
    if kind == "text":
        if nominee_id is not None or answer is None:
            raise AppError("WRONG_CHOICE", "This question needs a typed answer.", 422)
        if not answer or len(answer) > ANSWER_MAX_LENGTH or _ANSWER_CONTROL.search(answer):
            raise AppError("ANSWER_INVALID", "Type an answer of up to 500 characters.", 422)
    elif nominee_id is None or answer is not None:
        raise AppError("WRONG_CHOICE", "Choose one of the options.", 422)


async def _existing(db: AsyncSession, participant_id: uuid.UUID, ballot_id: uuid.UUID, key: str) -> list[Any]:
    return list(
        (
            await db.execute(
                text(_RECEIPT_SQL.format(where="(v.ballot_id = :bid OR v.idempotency_key = :key)")),
                {"pid": participant_id, "bid": ballot_id, "key": key},
            )
        ).all()
    )


def _resolve_existing(rows: list[Any], ballot_id: uuid.UUID, key: str, payload_hash: str) -> Receipt | None:
    """Apply the idempotency rules to votes that already exist for this participant."""
    for row in rows:
        if row.idempotency_key == key:
            if row.request_hash == payload_hash:
                return _receipt(row)  # identical retry: return the original receipt
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "This request key was already used for a different choice. Nothing was changed.",
                409,
                {"receipt": _receipt(row).public()},
            )
    for row in rows:
        if row.ballot_id == ballot_id:
            raise AppError(
                "ALREADY_VOTED",
                "You have already voted on this ballot. Votes are final.",
                409,
                {"receipt": _receipt(row).public()},
            )
    return None


async def submit_vote(
    db: AsyncSession,
    ctx: ParticipantContext,
    ballot_id: uuid.UUID,
    nominee_id: uuid.UUID | None,
    idempotency_key: str,
    answer: str | None = None,
) -> tuple[Receipt, bool]:
    """Return (receipt, created). created=False means an identical retry was replayed.

    A vote names a nominee of the ballot; an answer to a text ballot carries `answer`
    and no nominee. The answer is stored trimmed.
    """
    if answer is not None:
        answer = answer.strip()
    key = idempotency_key.strip()
    if not key or len(key) > _IDEMPOTENCY_KEY_MAX or not key.isascii():
        raise AppError(
            "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header (1-80 ASCII characters).", 400
        )
    payload_hash = _payload_hash(ballot_id, nominee_id, answer)
    settings = get_settings()

    async with db.begin():
        await db.execute(text(f"SET LOCAL lock_timeout = '{int(settings.vote_lock_timeout_ms)}ms'"))
        # 1. Programme live lock, shared: votes never block each other.
        await lock_live_shared(db, ctx.programme_id)
        # 2+3. Participant and session rows, shared: revocation, disabling and new
        # invitations take conflicting locks, so they are ordered against this vote.
        # Two statements keep the documented order (participant, then session).
        person = (
            await db.execute(
                text("SELECT disabled_at, activated_at FROM participants WHERE id = :pid FOR SHARE"),
                {"pid": ctx.participant_id},
            )
        ).one()
        session = (
            await db.execute(
                text(
                    "SELECT revoked_at, revoke_reason, expires_at <= clock_timestamp() AS expired "
                    "FROM participant_sessions WHERE id = :sid AND participant_id = :pid FOR SHARE"
                ),
                {"sid": ctx.session_id, "pid": ctx.participant_id},
            )
        ).one()
        if session.revoked_at is not None:
            reason = session.revoke_reason or "logout"
            raise AppError("SESSION_REVOKED", revoke_message(reason), 401, {"reason": reason})
        if session.expired:
            raise AppError("SESSION_EXPIRED", REVOKE_MESSAGES["expired"], 401)
        if person.disabled_at is not None or person.activated_at is None:
            raise AppError("ACCOUNT_DISABLED", REVOKE_MESSAGES["disabled"], 403)

        # 4. Idempotent replay is answered before the open/deadline checks, so a retry of
        # an accepted vote still gets its receipt after the round has closed.
        existing = _resolve_existing(
            await _existing(db, ctx.participant_id, ballot_id, key), ballot_id, key, payload_hash
        )
        if existing is not None:
            return existing, False

        # 5. The acceptance check, evaluated with the clock read after the locks.
        state = (
            await db.execute(
                text(
                    """
                    SELECT b.event_id, b.kind, e.status, e.voting_deadline_at, e.archived_at,
                           pr.login_opens_at, pr.login_closes_at, clock_timestamp() AS now
                    FROM ballots b
                    JOIN events e ON e.id = b.event_id AND e.programme_id = b.programme_id
                    JOIN programmes pr ON pr.id = e.programme_id
                    WHERE b.id = :bid AND b.programme_id = :prog
                    """
                ),
                {"bid": ballot_id, "prog": ctx.programme_id},
            )
        ).one_or_none()
        if state is None:
            raise not_found("Ballot")
        now = state.now
        if (
            state.login_opens_at is None
            or now < state.login_opens_at
            or (state.login_closes_at is not None and now >= state.login_closes_at)
        ):
            raise AppError("PROGRAMME_ACCESS_CLOSED", "The programme is not open for voting now.", 409)
        if state.status == "upcoming" or state.archived_at is not None:
            raise AppError("VOTING_NOT_OPEN", "Voting for this round has not opened yet.", 409)
        if state.status == "closed" or state.voting_deadline_at is None or now >= state.voting_deadline_at:
            raise AppError(
                "VOTING_CLOSED", "Voting for this round has closed. Your choice was not recorded.", 409
            )
        _check_choice(state.kind, nominee_id, answer)
        if nominee_id is not None:
            nominee_ok = (
                await db.execute(
                    text("SELECT 1 FROM nominees WHERE id = :nid AND ballot_id = :bid"),
                    {"nid": nominee_id, "bid": ballot_id},
                )
            ).first()
            if nominee_ok is None:
                raise AppError("INVALID_NOMINEE", "That choice is not part of this ballot.", 422)

        # 6. Insert. ON CONFLICT covers a concurrent duplicate from the same participant:
        # the unique index makes this statement wait for the other transaction, then
        # either insert (it rolled back) or skip (it committed).
        receipt_code = new_receipt_code()
        inserted = (
            await db.execute(
                text(
                    """
                    INSERT INTO votes (id, receipt_code, programme_id, event_id, ballot_id, nominee_id,
                                       answer_text, participant_id, session_id, idempotency_key,
                                       request_hash, created_at)
                    VALUES (:id, :code, :prog, :eid, :bid, :nid, :answer, :pid, :sid, :key, :hash, :now)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "code": receipt_code,
                    "prog": ctx.programme_id,
                    "eid": state.event_id,
                    "bid": ballot_id,
                    "nid": nominee_id,
                    "answer": answer,
                    "pid": ctx.participant_id,
                    "sid": ctx.session_id,
                    "key": key,
                    "hash": payload_hash,
                    "now": now,
                },
            )
        ).first()
        if inserted is None:
            rows = await _existing(db, ctx.participant_id, ballot_id, key)
            existing = _resolve_existing(rows, ballot_id, key, payload_hash)
            if existing is not None:
                return existing, False
            # Receipt-code collision (vanishingly rare): let the client retry the same key.
            raise AppError("RETRY", "Please try again.", 503)

        await emit(db, ctx.programme_id, "tally", {"e": str(state.event_id), "b": str(ballot_id)})
        created_row = (
            await db.execute(
                text(_RECEIPT_SQL.format(where="v.ballot_id = :bid")),
                {"pid": ctx.participant_id, "bid": ballot_id},
            )
        ).one()
        receipt = _receipt(created_row)
    # The transaction has committed here; only now is success reported.
    return receipt, True


async def get_receipt(db: AsyncSession, ctx: ParticipantContext, ballot_id: uuid.UUID) -> Receipt | None:
    async with db.begin():
        row = (
            await db.execute(
                text(_RECEIPT_SQL.format(where="v.ballot_id = :bid")),
                {"pid": ctx.participant_id, "bid": ballot_id},
            )
        ).first()
    return _receipt(row) if row else None


async def list_receipts(db: AsyncSession, participant_id: uuid.UUID) -> list[Receipt]:
    """Receipts for the rounds participants can see (archived rounds, for example after a
    restart, are hidden; their votes stay stored)."""
    rows = (
        await db.execute(
            text(_RECEIPT_SQL.format(where="e.archived_at IS NULL") + " ORDER BY v.created_at"),
            {"pid": participant_id},
        )
    ).all()
    return [_receipt(r) for r in rows]
