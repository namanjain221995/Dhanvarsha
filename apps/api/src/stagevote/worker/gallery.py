"""The gallery worker: reads the faces in uploaded files and sorts them into people.

It runs on its own (`stagevote gallery-worker`), apart from the worker that sends email and opens
rounds, so a long video can never delay either. Run exactly one: reading files happens side by side, but
deciding who a face belongs to happens one file at a time (`_matching`), so two photos of a stranger
arriving together cannot each start a new person.

It scales by itself: the longer the queue, the more files it reads at once, and it drops back as the queue
empties, whenever a round is open for voting, and whenever the machine is busy with other work. Its
container also has a low CPU priority, so what it uses is only what nothing else wants.

Questions to the vision model happen outside any database transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import filecmp
import hashlib
import os
import signal
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ..config import Settings, get_settings
from ..db import dispose_engine, init_engine, sessionmaker
from ..logging import configure_logging, get_logger
from ..models import GalleryFace, GalleryMedia, GalleryPerson, GalleryStorageTrash
from ..services import gallery_folders as folders
from ..services import gallery_store as store
from ..services.gallery import refresh_avatar
from ..services.gallery_faces import Face
from ..services.gallery_files import (
    INCOMING,
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
    Candidate,
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
# When S3 cannot be reached, how long before trying its chores again. Files keep waiting safely here.
STORAGE_RETRY_SECONDS = 30
# How often to look over everyone's folder, in case something changed without saying so.
RECHECK_TICKS = 900
# One more file read at once for each this many waiting.
FILES_PER_EXTRA = 5
# Above this share of its cores in use, the machine has better things to do than a long queue.
BUSY_SHARE = 0.85
HASH_BLOCK = 8 * 1024 * 1024


def wanted_concurrency(
    *, waiting: int, voting: bool, machine_load: float, cpus: int, low: int, high: int
) -> int:
    """How many files to handle at once right now. `machine_load` leaves out this worker's own."""
    if voting or machine_load >= cpus * BUSY_SHARE:
        return low
    return max(low, min(high, low + waiting // FILES_PER_EXTRA))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


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
            SELECT 'person' AS kind, p.id AS source_id, p.id AS person_id, p.embedding FROM gallery_people p
            WHERE p.programme_id = :p AND p.embedding IS NOT NULL
            UNION ALL
            SELECT 'face', f.id, f.person_id, f.embedding
            FROM gallery_faces f JOIN gallery_people p ON p.id = f.person_id
            WHERE p.programme_id = :p AND f.is_reference
            """
        ),
        {"p": programme_id},
    )
    return [
        Reference(row.person_id, from_bytes(row.embedding), None, (row.kind, row.source_id)) for row in rows
    ]


async def _aligned_crop(candidate: Candidate) -> bytes:
    """The crop to show the model. Only close calls need one, so it is not loaded with every reference."""
    if candidate.aligned is not None:
        return candidate.aligned
    assert candidate.source is not None
    kind, source_id = candidate.source
    table = "gallery_people" if kind == "person" else "gallery_faces"
    async with sessionmaker()() as db, db.begin():
        crop = (
            await db.execute(text(f"SELECT aligned FROM {table} WHERE id = :i"), {"i": source_id})  # noqa: S608 - one of two names
        ).scalar_one()
    return bytes(crop)


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
            said = await same_person(await _aligned_crop(candidate), face.aligned)
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
        self._storage_task: asyncio.Task[None] | None = None
        self._storage_retry_at = 0.0
        self.concurrency = settings.gallery_min_concurrency
        # Reading happens on these threads and no others: each holds a copy of the face models.
        self._readers = ThreadPoolExecutor(
            max_workers=max(1, settings.gallery_reader_threads), thread_name_prefix="gallery-reader"
        )

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
                    # Nothing can come of it, and it was never public: free the space.
                    if item.source_on_s3:
                        db.add(GalleryStorageTrash(s3_key=item.source_key))
                    else:
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
            original_name, on_s3 = item.original_name, item.source_on_s3
        started = time.monotonic()
        root = Path(self.settings.media_root)
        if not on_s3:
            source = root / source_key
            if not source.exists():
                raise UnreadableFile("The uploaded file is missing.")
            await self._read_and_store(media_id, programme_id, kind, original_name, source, None, started)
            await asyncio.to_thread(source.unlink, missing_ok=True)
            return
        # Sent straight to S3: it comes here only to be read, and then it goes.
        source = root / INCOMING / uuid.uuid4().hex
        await asyncio.to_thread(source.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(store.download_file, source_key, source)
            if await self._first_of_its_kind(
                media_id, source_key, await asyncio.to_thread(_hash_file, source)
            ):
                await self._read_and_store(
                    media_id, programme_id, kind, original_name, source, source_key, started
                )
        finally:
            await asyncio.to_thread(source.unlink, missing_ok=True)

    async def _first_of_its_kind(self, media_id: uuid.UUID, source_key: str, digest: str) -> bool:
        """The same file sent twice counts once, for everyone. The second copy simply goes away."""
        try:
            async with sessionmaker()() as db, db.begin():
                await db.execute(
                    text("UPDATE gallery_media SET sha256 = :h WHERE id = :m"), {"h": digest, "m": media_id}
                )
            return True
        except IntegrityError:
            async with sessionmaker()() as db, db.begin():
                db.add(GalleryStorageTrash(s3_key=source_key))
                await db.execute(text("DELETE FROM gallery_media WHERE id = :m"), {"m": media_id})
            return False

    async def _read_and_store(
        self,
        media_id: uuid.UUID,
        programme_id: uuid.UUID,
        kind: str,
        original_name: str,
        source: Path,
        s3_source: str | None,
        started: float,
    ) -> None:
        prepared = await asyncio.get_running_loop().run_in_executor(
            self._readers, prepare_image if kind == "image" else prepare_video, source
        )
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
            # On S3 before the file counts as ready. Its keys never change, so a retry just replaces it.
            remote = (
                await asyncio.to_thread(
                    self._send_to_s3, media_id, original_name, prepared, source, s3_source
                )
                if store.enabled()
                else None
            )
            async with self._matching:
                stored = await self._store(
                    media_id, programme_id, kind, prepared, crops, started, remote, s3_source
                )
        except Exception:
            await asyncio.to_thread(remove_public, written)
            raise
        if not stored:  # an organiser removed it while it was being read
            await asyncio.to_thread(remove_public, written)
            if remote:
                async with sessionmaker()() as db, db.begin():
                    db.add_all([GalleryStorageTrash(s3_key=key) for key in set(remote)])
        elif remote:  # S3 holds the full-size files now; the small pictures stay here
            await asyncio.to_thread(remove_public, [prepared.storage_key, prepared.display_key])

    def _send_to_s3(
        self, media_id: uuid.UUID, original_name: str, prepared: Prepared, source: Path, s3_source: str | None
    ) -> tuple[str, str]:
        root = Path(self.settings.media_root)
        storage = store.file_key(media_id, original_name, store.extension_of(prepared.storage_key))
        if s3_source and filecmp.cmp(source, root / prepared.storage_key, shallow=False):
            # Already in the bucket, byte for byte: a copy there uses none of this machine's internet line.
            store.copy_object(s3_source, storage)
        else:
            store.upload_file(root / prepared.storage_key, storage)
        if prepared.display_key == prepared.storage_key:
            return storage, storage
        display = store.playable_key(media_id, store.extension_of(prepared.display_key))
        store.upload_file(root / prepared.display_key, display)
        return storage, display

    async def _store(
        self,
        media_id: uuid.UUID,
        programme_id: uuid.UUID,
        kind: str,
        prepared: Prepared,
        crops: list[str],
        started: float,
        remote: tuple[str, str] | None,
        s3_source: str | None,
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
            await folders.mark_folders(db, list(touched))
            item.storage_key, item.display_key = remote or (prepared.storage_key, prepared.display_key)
            item.location = "s3" if remote else "local"
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
            if s3_source:
                db.add(GalleryStorageTrash(s3_key=s3_source))
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
                if joins:
                    await folders.mark_folders(db, [person_id])
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
                        "SELECT id, kind, display_key FROM gallery_media WHERE status = 'ready' "
                        "AND location = 'local' AND analysis = '{}'::jsonb ORDER BY created_at LIMIT 1"
                    )
                )
            ).first()

    async def _fill_in_analysis(self, row: Any) -> None:
        """Read one older file again, for where its faces are. Who they belong to stays as it is."""
        try:
            reading = await asyncio.get_running_loop().run_in_executor(
                self._readers, read_again, row.kind, Path(self.settings.media_root) / row.display_key
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

    # ---- S3 --------------------------------------------------------------------------

    async def _storage_chores(self) -> None:
        try:
            await folders.do_some_work()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nothing is lost: everything waits in the database, and the files stay where they are.
            log.exception("S3 work failed; trying again shortly")
            self._storage_retry_at = time.monotonic() + STORAGE_RETRY_SECONDS

    async def _start_storage_chores(self) -> None:
        if not store.enabled() or self.stopping.is_set() or time.monotonic() < self._storage_retry_at:
            return
        if self._storage_task is not None and not self._storage_task.done():
            return
        if await folders.has_work():
            self._storage_task = asyncio.create_task(self._storage_chores())
            self.in_flight.add(self._storage_task)
            self._storage_task.add_done_callback(self.in_flight.discard)

    # ---- lifecycle --------------------------------------------------------------------

    async def _scale(self) -> None:
        async with sessionmaker()() as db, db.begin():
            row = (
                await db.execute(
                    text(
                        """
                        SELECT (SELECT count(*) FROM gallery_media WHERE status = 'queued' AND next_attempt_at <= now()) AS waiting,
                               EXISTS (SELECT 1 FROM events WHERE status = 'open' AND archived_at IS NULL) AS voting
                        """
                    )
                )
            ).one()
        wanted = wanted_concurrency(
            waiting=row.waiting,
            voting=row.voting,
            # What else is running: this worker's own files must not count as the machine being busy.
            machine_load=max(0.0, os.getloadavg()[0] - len(self.in_flight)),
            cpus=os.cpu_count() or 1,
            low=self.settings.gallery_min_concurrency,
            high=self.settings.gallery_max_concurrency,
        )
        if wanted != self.concurrency:
            log.info("gallery worker scaled", extra={"reading_at_once": wanted, "waiting": row.waiting})
            self.concurrency = wanted

    async def tick(self) -> None:
        await self._scale()
        capacity = self.concurrency - len(self.in_flight)
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
        await self._start_storage_chores()

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
                if ticks % RECHECK_TICKS == RECHECK_TICKS - 1 and store.enabled():
                    await folders.recheck_everyone()
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
