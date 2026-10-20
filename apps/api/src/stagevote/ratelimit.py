"""Fixed-window counters stored in PostgreSQL so every API process shares them."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import AppError


@dataclass(frozen=True)
class Limit:
    key: str
    limit: int
    window_seconds: int


async def current_count(db: AsyncSession, limit: Limit) -> tuple[int, int]:
    """Return (count in current window, seconds until the window resets)."""
    row = (
        await db.execute(
            text(
                "SELECT count AS hits, GREATEST(0, CEIL(EXTRACT(EPOCH FROM (window_started_at + make_interval(secs => :w) "
                "- now()))))::int AS retry FROM rate_limit_buckets WHERE key = :k "
                "AND window_started_at > now() - make_interval(secs => :w)"
            ),
            {"k": limit.key, "w": limit.window_seconds},
        )
    ).first()
    if row is None:
        return 0, 0
    return int(row.hits), int(row.retry)


async def hit(db: AsyncSession, limit: Limit) -> int:
    """Count one event in the current window (atomic upsert) and return the new count."""
    row = (
        await db.execute(
            text(
                """
                INSERT INTO rate_limit_buckets AS b (key, window_started_at, count)
                VALUES (:k, now(), 1)
                ON CONFLICT (key) DO UPDATE SET
                    count = CASE WHEN b.window_started_at <= now() - make_interval(secs => :w)
                                 THEN 1 ELSE b.count + 1 END,
                    window_started_at = CASE WHEN b.window_started_at <= now() - make_interval(secs => :w)
                                 THEN now() ELSE b.window_started_at END
                RETURNING count
                """
            ),
            {"k": limit.key, "w": limit.window_seconds},
        )
    ).scalar_one()
    return int(row)


async def clear(db: AsyncSession, key: str) -> None:
    await db.execute(text("DELETE FROM rate_limit_buckets WHERE key = :k"), {"k": key})


async def enforce(db: AsyncSession, *limits: Limit, message: str) -> None:
    for limit in limits:
        count, retry = await current_count(db, limit)
        if count >= limit.limit:
            raise AppError(
                "RATE_LIMITED",
                message,
                429,
                {"retry_after_seconds": retry},
                headers={"Retry-After": str(max(retry, 1))},
            )
