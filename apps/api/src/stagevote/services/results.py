"""Aggregate results computed directly from persisted votes (no second tally).

Team, individual and multiple-choice ballots are counted per nominee (answer option).
A typed-answer (text) ballot is counted as a number of answers; the answers themselves
are only ever listed to organisers (`typed_answers`), never to voters.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .participants import name_from_email


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
    order = {str(b): i for i, b in enumerate(ballot_ids)}
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
    answered = (
        await db.execute(
            text(
                """
                SELECT b.id AS ballot_id, b.title, b.kind, count(v.id) AS answers
                FROM ballots b
                LEFT JOIN votes v ON v.ballot_id = b.id AND v.answer_text IS NOT NULL
                WHERE b.id = ANY(:ids) AND b.kind = 'text'
                GROUP BY b.id, b.title, b.kind
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
    for row in answered:
        count = int(row.answers)
        out.append(
            {
                "ballot_id": str(row.ballot_id),
                "title": row.title,
                "kind": row.kind,
                "nominees": [],
                "answer_count": count,
                "total_votes": count,
                "eligible_voters": eligible,
                "remaining_voters": max(eligible - count, 0),
                "leaders": [],
                "tie": False,
            }
        )
    out.sort(key=lambda b: order.get(b["ballot_id"], len(order)))
    return out


def participant_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Published results as voters see them: a typed-answer question shows only how many
    answered, never what anyone typed."""
    return [
        {"ballot_id": r["ballot_id"], "kind": "text", "answer_count": r["answer_count"]}
        if r["kind"] == "text"
        else r
        for r in results
    ]


async def typed_answers(db: AsyncSession, ballot_ids: list[uuid.UUID]) -> dict[str, list[dict[str, Any]]]:
    """For organisers: every typed answer of these ballots with who typed it, oldest first."""
    if not ballot_ids:
        return {}
    rows = (
        await db.execute(
            text(
                """
                SELECT v.ballot_id, v.answer_text, v.created_at, p.display_name, p.email
                FROM votes v
                JOIN participants p ON p.id = v.participant_id
                WHERE v.ballot_id = ANY(:ids) AND v.answer_text IS NOT NULL
                ORDER BY v.created_at, v.id
                """
            ),
            {"ids": ballot_ids},
        )
    ).all()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.ballot_id), []).append(
            {
                "participant_name": row.display_name or name_from_email(row.email),
                "participant_email": row.email,
                "answer": row.answer_text,
                "created_at": row.created_at,
            }
        )
    return out
