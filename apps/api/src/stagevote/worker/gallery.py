"""The gallery worker: reads the faces in uploaded files and sorts them into people.

It runs on its own (`stagevote gallery-worker`), apart from the worker that sends email and opens
rounds, so a long video can never delay either, and with a CPU and memory ceiling so it can never
take much from the voting. Run exactly one: reading files happens side by side, but deciding who a
face belongs to happens one file at a time (`_matching`), so two photos of a stranger arriving
together cannot each start a new person.

Questions to the vision model happen outside any database transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select, text

from ..config import Settings, get_settings
from ..db import dispose_engine, init_engine, sessionmaker
from ..logging import configure_logging, get_logger
from ..models import GalleryFace, GalleryMedia, GalleryPerson
from ..services.gallery import refresh_avatar
from ..services.gallery_faces import Face
from ..services.gallery_files import (
    Prepared,
    UnreadableFile,
    prepare_image,
    prepare_video,
    read_again,
    remove_public,
    write_public,
)
from ..services.gallery_match import (
    MATCH_AT,
    MAX_CANDIDATES,
    REFERENCES_PER_PERSON,
    Reference,
    candidates,
    from_bytes,
    to_bytes,
    verdict,
)
from ..services.gallery_model import same_person

log = get_logger("stagevote.gallery")
LEASE_SECONDS = 180
RENEW_SECONDS = 45
MAX_ATTEMPTS = 3
# Close calls one selfie may put to the model, across the groups already found.
MAX_REMATCH_QUESTIONS = 40
RETRY_MESSAGE = "This file could not be processed. Try again."
# The same pixels read the same way give the same description: from this close, it is that face.
SAME_FACE_AGAIN_AT = 0.9
MODEL_WORDS: dict[bool | None, str] = {True: "yes", False: "no", None: "no_answer"}


@dataclass(frozen=True)
class Assignment:
    """What was decided for one face."""

    person_id: uuid.UUID
    is_new: bool
    score: float | None
    matched_by: str
    is_reference: bool
    checked: list[dict[str, Any]]


async def load_references(db: Any, programme_id: uuid.UUID) -> list[Reference]:
    rows = await db.execute(
        text(
            """
            SELECT p.id AS person_id, p.embedding, p.aligned FROM gallery_people p
            WHERE p.programme_id = :p AND p.embedding IS NOT NULL
            UNION ALL
            SELECT f.person_id, f.embedding, f.aligned
            FROM gallery_faces f JOIN gallery_people p ON p.id = f.person_id
            WHERE p.programme_id = :p AND f.is_reference
            """
        ),
        {"p": programme_id},
    )
    return [Reference(row.person_id, from_bytes(row.embedding), row.aligned) for row in rows]


async def decide(
    face: Face, references: list[Reference], taken: set[uuid.UUID]
) -> tuple[tuple[uuid.UUID, float, str] | None, list[dict[str, Any]]]:
    """The person this face belongs to, if any (the clear match, or what the model confirms), and
    the record of who was considered, for the organisers' analysis view."""
    asked = 0
    checked: list[dict[str, Any]] = []
    for candidate in candidates(face.embedding, references):
        if candidate.person_id in taken:
            continue
        entry = {
            "person_id": str(candidate.person_id),
            "score": round(candidate.score, 3),
            "model": "not_asked",
        }
        answer = verdict(candidate.score)
        if answer == "match":
            checked.append(entry)
            return (candidate.person_id, candidate.score, "opencv"), checked
        if answer == "ask":
            asked += 1
            if asked > MAX_CANDIDATES:
                return None, checked
            said = await same_person(candidate.aligned, face.aligned)
            entry["model"] = MODEL_WORDS[said]
            checked.append(entry)
            if said is True:
                return (candidate.person_id, candidate.score, "model"), checked
    return None, checked


def _where_it_is(face: Face) -> dict[str, Any]:
    return {"box": list(face.box), "detector_score": face.detector_score, "size_px": face.size_px}


async def assign_faces(
    faces: list[Face], references: list[Reference], one_face_each: bool
) -> list[Assignment]:
    """Decide who each face belongs to, clearest face first. `references` grows along the way."""
    assignments: list[Assignment] = []
    taken: set[uuid.UUID] = set()
    per_person: dict[uuid.UUID, int] = {}
    for reference in references:
        per_person[reference.person_id] = per_person.get(reference.person_id, 0) + 1
    for face in faces:
        decision, checked = await decide(face, references, taken)
        if decision is None:
            person_id, score, how, is_new = uuid.uuid4(), None, "new", True
        else:
            person_id, score, how = decision
            is_new = False
        count = per_person.get(person_id, 0)
        stands_for = is_new or (
            how == "opencv" and score is not None and score >= MATCH_AT and count < REFERENCES_PER_PERSON
        )
        if stands_for:
            references.append(Reference(person_id, face.embedding, face.aligned))
            per_person[person_id] = count + 1
        if one_face_each:  # one picture cannot show the same person twice
            taken.add(person_id)
        assignments.append(Assignment(person_id, is_new, score, how, stands_for, checked))
    return assignments


class GalleryWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = settings.worker_id or f"gallery-{socket.gethostname()}-{os.getpid()}"
        self.stopping = asyncio.Event()
        self.in_flight: set[asyncio.Task[None]] = set()
        self._matching = asyncio.Lock()

    # ---- the queue ------------------------------------------------------------------

    async def lease(self, count: int) -> list[uuid.UUID]:
        async with sessionmaker()() as db, db.begin():
            rows = await db.execute(
                text(
                    f"""
                    UPDATE gallery_media SET status = 'processing', lease_owner = :w,
                           lease_expires_at = now() + interval '{LEASE_SECONDS} seconds',
                           attempts = attempts + 1
                    WHERE id IN (
                        SELECT id FROM gallery_media
                        WHERE status = 'queued' AND next_attempt_at <= now()
                        ORDER BY next_attempt_at, created_at
                        LIMIT :n
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id
                    """  # noqa: S608 - a constant
                ),
                {"w": self.worker_id, "n": count},
            )
            return list(rows.scalars())

    async def recover(self) -> None:
        """Files whose worker stopped in the middle go back to the queue."""
        async with sessionmaker()() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE gallery_media SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
                    "next_attempt_at = now() WHERE status = 'processing' AND lease_expires_at < now()"
                )
            )

    async def _keep_lease(self, media_id: uuid.UUID) -> None:
        """A long video outlasts any one lease, so it is renewed while the work goes on."""
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            async with sessionmaker()() as db, db.begin():
                await db.execute(
                    text(
                        f"UPDATE gallery_media SET lease_expires_at = now() + interval '{LEASE_SECONDS} seconds' "  # noqa: S608
                        "WHERE id = :m AND lease_owner = :w"
                    ),
                    {"m": media_id, "w": self.worker_id},
                )

    async def _give_up(self, media_id: uuid.UUID, message: str, *, retry: bool) -> None:
        async with sessionmaker()() as db, db.begin():
            item = await db.get(GalleryMedia, media_id)
            if item is None:
                return
            item.lease_owner, item.lease_expires_at = None, None
            item.last_error = message[:300]
            if retry and item.attempts < MAX_ATTEMPTS:
                item.status = "queued"
                await db.execute(
                    text(
                        "UPDATE gallery_media SET next_attempt_at = now() + make_interval(secs => :s) WHERE id = :m"
                    ),
                    {"s": 60 * item.attempts, "m": media_id},
                )
            else:
                item.status = "failed"
                if not retry and item.source_key:
                    # Nothing can come of it, and it was never public: free the disk.
                    source = Path(self.settings.media_root) / item.source_key
                    await asyncio.to_thread(source.unlink, missing_ok=True)
                    item.source_key = None

    # ---- one file -------------------------------------------------------------------

    async def process(self, media_id: uuid.UUID) -> None:
        keeper = asyncio.create_task(self._keep_lease(media_id))
        try:
            await self._process(media_id)
        except UnreadableFile as exc:
            log.info("gallery file refused", extra={"media_id": str(media_id), "reason": str(exc)})
            await self._give_up(media_id, str(exc), retry=False)
        except Exception:
            log.exception("gallery file failed", extra={"media_id": str(media_id)})
            await self._give_up(media_id, RETRY_MESSAGE, retry=True)
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper

    async def _process(self, media_id: uuid.UUID) -> None:
        async with sessionmaker()() as db, db.begin():
            item = await db.get(GalleryMedia, media_id)
            if item is None or item.status != "processing" or not item.source_key:
                return
            kind, source_key, programme_id = item.kind, item.source_key, item.programme_id
        source = Path(self.settings.media_root) / source_key
        if not source.exists():
            raise UnreadableFile("The uploaded file is missing.")
        started = time.monotonic()
        prepared = await asyncio.to_thread(prepare_image if kind == "image" else prepare_video, source)
        written: list[str | None] = [
            prepared.storage_key,
            prepared.display_key,
            prepared.thumb_key,
            *[key for _, key in prepared.frames],
        ]
        try:
            crops = await asyncio.to_thread(
                lambda: [write_public(face.crop, "webp") for face, _ in prepared.faces]
            )
            written += crops
            async with self._matching:
                stored = await self._store(media_id, programme_id, kind, prepared, crops, started)
        except Exception:
            await asyncio.to_thread(remove_public, written)
            raise
        if not stored:  # an organiser removed it while it was being read
            await asyncio.to_thread(remove_public, written)
        await asyncio.to_thread(source.unlink, missing_ok=True)

    async def _store(
        self,
        media_id: uuid.UUID,
        programme_id: uuid.UUID,
        kind: str,
        prepared: Prepared,
        crops: list[str],
        started: float,
    ) -> bool:
        async with sessionmaker()() as db, db.begin():
            references = await load_references(db, programme_id)
        order = sorted(range(len(prepared.faces)), key=lambda i: prepared.faces[i][0].quality, reverse=True)
        faces = [prepared.faces[i][0] for i in order]
        assignments = await assign_faces(faces, references, one_face_each=kind == "image")
        async with sessionmaker()() as db, db.begin():
            item = await db.get(GalleryMedia, media_id)
            if item is None:
                return False
            touched: set[uuid.UUID] = set()
            for index, assignment in zip(order, assignments, strict=True):
                face, frame_ms = prepared.faces[index]
                if assignment.is_new:
                    db.add(GalleryPerson(id=assignment.person_id, programme_id=programme_id))
                    await db.flush()
                db.add(
                    GalleryFace(
                        media_id=media_id,
                        person_id=assignment.person_id,
                        embedding=to_bytes(face.embedding),
                        aligned=face.aligned,
                        crop_key=crops[index],
                        quality=face.quality,
                        frame_ms=frame_ms,
                        match_score=assignment.score,
                        matched_by=assignment.matched_by,
                        is_reference=assignment.is_reference,
                        analysis={**_where_it_is(face), "checked": assignment.checked},
                    )
                )
                touched.add(assignment.person_id)
            await db.flush()
            for person_id in touched:
                await refresh_avatar(db, person_id)
            item.storage_key, item.display_key = prepared.storage_key, prepared.display_key
            item.thumb_key = prepared.thumb_key
            item.width, item.height = prepared.width, prepared.height
            item.duration_ms = prepared.duration_ms
            item.face_count = len(prepared.faces)
            item.analysis = {
                "frames_read": prepared.frames_read,
                "faces_seen": prepared.faces_seen,
                "took_ms": round((time.monotonic() - started) * 1000),
                "model_questions": sum(
                    entry["model"] != "not_asked"
                    for assignment in assignments
                    for entry in assignment.checked
                ),
                "frames": [{"ms": ms, "key": key} for ms, key in prepared.frames],
            }
            item.status = "ready"
            item.source_key = None
            item.lease_owner, item.lease_expires_at, item.last_error = None, None, None
            item.processed_at = (await db.execute(text("SELECT now()"))).scalar_one()
        return True

    # ---- someone just gave a selfie ---------------------------------------------------

    async def rematch(self) -> bool:
        """Look for the newest selfie among the unnamed groups. True if there was one to do."""
        async with self._matching:
            async with sessionmaker()() as db, db.begin():
                person = (
                    await db.execute(
                        select(GalleryPerson)
                        .where(GalleryPerson.rematch_pending, GalleryPerson.embedding.is_not(None))
                        .order_by(GalleryPerson.updated_at)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if person is None:
                    return False
                person_id, programme_id = person.id, person.programme_id
                selfie, aligned = person.embedding, person.aligned
                assert selfie is not None and aligned is not None
                faces = list(
                    await db.execute(
                        text(
                            """
                            SELECT f.person_id, f.embedding, f.aligned
                            FROM gallery_faces f JOIN gallery_people p ON p.id = f.person_id
                            WHERE p.programme_id = :p AND p.enrolled_at IS NULL
                            """
                        ),
                        {"p": programme_id},
                    )
                )
            joins = await self._groups_of_selfie(from_bytes(selfie), aligned, faces)
            async with sessionmaker()() as db, db.begin():
                for group_id, (score, how) in joins.items():
                    await db.execute(
                        text(
                            "UPDATE gallery_faces SET person_id = :to, matched_by = :how, "
                            "match_score = coalesce(match_score, :score) WHERE person_id = :from"
                        ),
                        {"to": person_id, "from": group_id, "how": how, "score": score},
                    )
                if joins:
                    await db.execute(
                        text("DELETE FROM gallery_people WHERE id = ANY(:ids) AND enrolled_at IS NULL"),
                        {"ids": list(joins)},
                    )
                # A newer selfie that arrived meanwhile keeps the flag, and is looked for next.
                await db.execute(
                    text(
                        "UPDATE gallery_people SET rematch_pending = false WHERE id = :p AND embedding = :e"
                    ),
                    {"p": person_id, "e": selfie},
                )
            if joins:
                log.info("selfie joined groups", extra={"person_id": str(person_id), "groups": len(joins)})
        return True

    async def _groups_of_selfie(
        self, selfie: np.ndarray, aligned: bytes, faces: list[Any]
    ) -> dict[uuid.UUID, tuple[float, str]]:
        """The unnamed groups that are this person: {group: (score, how it was decided)}."""
        if not faces:
            return {}
        scores = np.stack([from_bytes(f.embedding) for f in faces]) @ selfie
        best: dict[uuid.UUID, tuple[float, bytes]] = {}
        for face, raw in zip(faces, scores, strict=True):
            score = float(raw)
            if face.person_id not in best or score > best[face.person_id][0]:
                best[face.person_id] = (score, face.aligned)
        joins: dict[uuid.UUID, tuple[float, str]] = {}
        questions = 0
        for group_id, (score, face_aligned) in sorted(
            best.items(), key=lambda item: item[1][0], reverse=True
        ):
            answer = verdict(score)
            if answer == "no":
                break
            if answer == "match":
                joins[group_id] = (score, "opencv")
            elif questions < MAX_REMATCH_QUESTIONS:
                questions += 1
                if await same_person(aligned, face_aligned) is True:
                    joins[group_id] = (score, "model")
        return joins

    # ---- files read before the analysis view existed -------------------------------------

    async def _next_unread(self) -> Any:
        async with sessionmaker()() as db, db.begin():
            return (
                await db.execute(
                    text(
                        "SELECT id, kind, display_key FROM gallery_media "
                        "WHERE status = 'ready' AND analysis = '{}'::jsonb ORDER BY created_at LIMIT 1"
                    )
                )
            ).first()

    async def _fill_in_analysis(self, row: Any) -> None:
        """Read one older file again, for where its faces are. Who they belong to stays as it is."""
        try:
            reading = await asyncio.to_thread(
                read_again, row.kind, Path(self.settings.media_root) / row.display_key
            )
            async with sessionmaker()() as db, db.begin():
                item = await db.get(GalleryMedia, row.id)
                if item is None:  # an organiser removed it meanwhile
                    remove_public([key for _, key in reading.frames])
                    return
                stored = (
                    await db.execute(select(GalleryFace).where(GalleryFace.media_id == row.id))
                ).scalars()
                for face in stored:
                    known = from_bytes(face.embedding)
                    found = [(float(known @ new.embedding), new, ms) for new, ms in reading.faces]
                    best = max(found, key=lambda entry: entry[0], default=None)
                    if best is not None and best[0] >= SAME_FACE_AGAIN_AT:
                        face.analysis = _where_it_is(best[1])
                        face.frame_ms = best[2]
                item.analysis = {
                    "frames_read": reading.frames_read,
                    "faces_seen": reading.faces_seen,
                    "frames": [{"ms": ms, "key": key} for ms, key in reading.frames],
                }
        except Exception:
            # Not again and again: the view works without it, and says nothing was read.
            log.exception("older gallery file not read again", extra={"media_id": str(row.id)})
            async with sessionmaker()() as db, db.begin():
                await db.execute(
                    text(
                        """UPDATE gallery_media SET analysis = '{"read_again": false}'::jsonb WHERE id = :m"""
                    ),
                    {"m": row.id},
                )

    # ---- lifecycle --------------------------------------------------------------------

    async def tick(self) -> None:
        capacity = self.settings.gallery_concurrency - len(self.in_flight)
        if capacity > 0:
            for media_id in await self.lease(capacity):
                task = asyncio.create_task(self.process(media_id))
                self.in_flight.add(task)
                task.add_done_callback(self.in_flight.discard)
        while not self.stopping.is_set() and await self.rematch():
            pass
        if not self.in_flight and not self.stopping.is_set():
            unread = await self._next_unread()
            if unread is not None:
                task = asyncio.create_task(self._fill_in_analysis(unread))
                self.in_flight.add(task)
                task.add_done_callback(self.in_flight.discard)

    async def run(self) -> None:
        log.info(
            "gallery worker starting",
            extra={"worker_id": self.worker_id, "vision_model": bool(self.settings.gallery_model_url)},
        )
        ticks = 0
        while not self.stopping.is_set():
            try:
                if ticks % 30 == 0:
                    await self.recover()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("gallery worker tick failed")
            ticks += 1
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), timeout=1.0)
        log.info("gallery worker stopping", extra={"in_flight": len(self.in_flight)})
        if self.in_flight:
            # A file still in progress when this ends goes back to the queue when its lease runs out.
            await asyncio.wait(self.in_flight, timeout=20)

    def request_stop(self) -> None:
        self.stopping.set()


async def run_gallery_worker() -> None:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level)
    init_engine(settings)
    worker = GalleryWorker(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        await worker.run()
    finally:
        await dispose_engine()
