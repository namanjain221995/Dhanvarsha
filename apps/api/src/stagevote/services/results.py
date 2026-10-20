"""Aggregate results computed directly from persisted votes (no second tally)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def eligible_voters(db: AsyncSession, programme_id: uuid.UUID) -> int:
    """Participants who may vote: not disabled (activation still pending counts)."""
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM participants WHERE programme_id = :p AND disabled_at IS NULL"),
                {"p": programme_id},
            )
        ).scalar_one()
    )


async def ballot_results(
    db: AsyncSession, ballot_ids: list[uuid.UUID], *, programme_id: uuid.UUID
) -> list[dict[str, Any]]:
    if not ballot_ids:
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT b.id AS ballot_id, b.title, b.kind, b.position AS bpos,
                       n.id AS nominee_id, n.name, n.affiliation, n.position AS npos,
                       count(v.id) AS votes
                FROM ballots b
                JOIN nominees n ON n.ballot_id = b.id
                LEFT JOIN votes v ON v.ballot_id = n.ballot_id AND v.nominee_id = n.id
                WHERE b.id = ANY(:ids)
                GROUP BY b.id, b.title, b.kind, b.position, n.id, n.name, n.affiliation, n.position
                ORDER BY b.position, n.position, n.name
                """
            ),
            {"ids": ballot_ids},
        )
    ).all()
    eligible = await eligible_voters(db, programme_id)
    by_ballot: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        ballot = by_ballot.setdefault(
            row.ballot_id,
            {"ballot_id": str(row.ballot_id), "title": row.title, "kind": row.kind, "nominees": []},
        )
        ballot["nominees"].append(
            {
                "nominee_id": str(row.nominee_id),
                "name": row.name,
                "affiliation": row.affiliation,
                "votes": int(row.votes),
            }
        )
    out = []
    for ballot in by_ballot.values():
        total = sum(n["votes"] for n in ballot["nominees"])
        top = max((n["votes"] for n in ballot["nominees"]), default=0)
        leaders = [n["nominee_id"] for n in ballot["nominees"] if top > 0 and n["votes"] == top]
        for n in ballot["nominees"]:
            n["percent"] = round(100.0 * n["votes"] / total, 1) if total else 0.0
            n["leading"] = n["nominee_id"] in leaders
        ballot.update(
            {
                "total_votes": total,
                "eligible_voters": eligible,
                "remaining_voters": max(eligible - total, 0),
                "leaders": leaders,
                "tie": len(leaders) > 1,
            }
        )
        out.append(ballot)
    return out
