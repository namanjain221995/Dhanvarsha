"""Audit log, outbox notifications, state versions and the programme live lock.

Lock ordering (documented in docs/architecture.md and followed everywhere):

1. programme live lock  - transaction-scoped advisory lock keyed by programme id.
   Votes take it SHARED; live transitions (open, close, start-next, deadline change,
   scheduled open, deadline sweep, schedule resume), settings changes and every event,
   ballot and nominee editor take it EXCLUSIVE before touching rows.
2. programme row        - FOR UPDATE, only inside live transitions and settings edits.
   The opening page editor takes only this lock: it changes nothing a vote reads.
3. event / ballot rows  - FOR UPDATE inside transitions and editors.
4. participant row      - FOR SHARE in votes, FOR UPDATE in link sign-in, invitations,
   disable and session reset.
5. session row          - FOR SHARE in votes, UPDATE in revocation.

Nothing that holds a later lock ever waits for an earlier one, so these paths cannot
deadlock with each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import redact, request_id_var
from .models import AuditEntry, OutboxEvent


@dataclass(frozen=True)
class Actor:
    type: str  # admin | participant | system
    id: uuid.UUID | None = None
    label: str = ""
    ip: str | None = None

    @classmethod
    def system(cls, label: str = "scheduler") -> Actor:
        return cls("system", None, label)


def _lock_key(programme_id: uuid.UUID) -> str:
    return f"stagevote:live:{programme_id}"


async def lock_live_shared(db: AsyncSession, programme_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:k, 0))"), {"k": _lock_key(programme_id)}
    )


async def lock_live_exclusive(db: AsyncSession, programme_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": _lock_key(programme_id)}
    )


async def db_now(db: AsyncSession) -> Any:
    """Wall-clock database time at this statement (not the transaction start)."""
    return (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def emit(db: AsyncSession, programme_id: uuid.UUID | None, topic: str, payload: dict[str, Any]) -> None:
    """Queue a notification. It is delivered only if the surrounding transaction commits."""
    db.add(OutboxEvent(programme_id=programme_id, topic=topic, payload=payload))
    await db.flush()


async def bump_state(
    db: AsyncSession, programme_id: uuid.UUID, *, live: bool = False, reason: str = ""
) -> int:
    """Increment the participant-visible state version (and the live version) and notify."""
    row = (
        await db.execute(
            text(
                "UPDATE programmes SET state_version = state_version + 1, "
                "live_version = live_version + CASE WHEN :live THEN 1 ELSE 0 END, updated_at = now() "
                "WHERE id = :pid RETURNING state_version, live_version"
            ),
            {"pid": programme_id, "live": live},
        )
    ).one()
    await emit(db, programme_id, "state", {"v": row.state_version, "lv": row.live_version, "r": reason})
    return int(row.state_version)


async def audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    *,
    programme_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEntry(
            programme_id=programme_id,
            actor_type=actor.type,
            actor_id=actor.id,
            actor_label=actor.label[:200],
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=redact(details or {}),
            request_id=request_id_var.get(),
            ip=actor.ip,
        )
    )
    await db.flush()
