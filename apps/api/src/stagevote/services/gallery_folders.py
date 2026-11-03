"""Keeps people's folders on S3 in step with the database, and clears what no longer belongs there.

The database decides who is in what. S3 has no rename and no links, so a folder is a set of copies, and this
works like a thermostat: anything that changes a person only says so (folder_wanted goes up), and the worker
compares what should be there with what gallery_person_files says is there, then copies and deletes the
difference. Each step is safe to repeat, and each one is recorded as it happens, so a worker stopped halfway
carries on where it was, and S3 being unreachable only means the folders catch up later. Renaming someone is
nothing special: their files simply belong under a new folder now.

Only the gallery worker runs this.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import sessionmaker
from ..logging import get_logger
from ..models import GalleryPerson
from . import gallery_store as store
from .gallery import drop_direct_upload
from .gallery_files import remove_public

log = get_logger(__name__)
BATCH = 500


async def mark_folders(db: AsyncSession, person_ids: list[uuid.UUID]) -> None:
    """These people's folders may be out of date. The caller holds a transaction."""
    if person_ids:
        await db.execute(
            text("UPDATE gallery_people SET folder_wanted = folder_wanted + 1 WHERE id = ANY(:ids)"),
            {"ids": person_ids},
        )


async def has_work() -> bool:
    async with sessionmaker()() as db, db.begin():
        return bool(
            (
                await db.execute(
                    text(
                        """
                        SELECT EXISTS (SELECT 1 FROM gallery_storage_trash)
                            OR EXISTS (SELECT 1 FROM gallery_people WHERE folder_wanted <> folder_done)
                            OR EXISTS (SELECT 1 FROM gallery_media WHERE status = 'ready' AND location = 'local')
                            OR EXISTS (
                                SELECT 1 FROM gallery_s3_uploads
                                WHERE created_at < now() - make_interval(hours => 72)
                            )
                            OR EXISTS (
                                SELECT 1 FROM gallery_person_files f
                                LEFT JOIN gallery_people p ON p.id = f.person_id
                                LEFT JOIN gallery_media m ON m.id = f.media_id
                                WHERE p.id IS NULL OR m.id IS NULL
                            )
                        """
                    )
                )
            ).scalar_one()
        )


async def do_some_work() -> None:
    """One round of everything that is waiting. Raises if S3 cannot be reached: the worker tries later."""
    await drain_trash()
    await sweep_leftovers()
    await abort_abandoned_uploads()
    await move_one_local_file()
    await sync_one_person()


# ---- deleting -----------------------------------------------------------------------------


async def drain_trash() -> None:
    async with sessionmaker()() as db, db.begin():
        rows = list(
            await db.execute(
                text("SELECT id, s3_key FROM gallery_storage_trash ORDER BY id LIMIT :n"), {"n": BATCH}
            )
        )
    if not rows:
        return
    await asyncio.to_thread(store.delete_keys, [row.s3_key for row in rows])
    async with sessionmaker()() as db, db.begin():
        await db.execute(
            text("DELETE FROM gallery_storage_trash WHERE id = ANY(:ids)"), {"ids": [row.id for row in rows]}
        )


async def sweep_leftovers() -> None:
    """Copies whose person or file has gone."""
    async with sessionmaker()() as db, db.begin():
        rows = list(
            await db.execute(
                text(
                    """
                    SELECT f.person_id, f.media_id, f.s3_key FROM gallery_person_files f
                    LEFT JOIN gallery_people p ON p.id = f.person_id
                    LEFT JOIN gallery_media m ON m.id = f.media_id
                    WHERE p.id IS NULL OR m.id IS NULL
                    LIMIT :n
                    """
                ),
                {"n": BATCH},
            )
        )
    if not rows:
        return
    await asyncio.to_thread(store.delete_keys, [row.s3_key for row in rows])
    async with sessionmaker()() as db, db.begin():
        for row in rows:
            await db.execute(
                text("DELETE FROM gallery_person_files WHERE person_id = :p AND media_id = :m"),
                {"p": row.person_id, "m": row.media_id},
            )


async def abort_abandoned_uploads() -> None:
    """Parts of an upload nobody finished are charged for until S3 is told to drop them. So is the
    whole file, if a request died after S3 had joined them."""
    async with sessionmaker()() as db, db.begin():
        rows = list(
            await db.execute(
                text(
                    "SELECT id, s3_key, s3_upload_id FROM gallery_s3_uploads "
                    "WHERE created_at < now() - make_interval(hours => 72) LIMIT :n"
                ),
                {"n": BATCH},
            )
        )
    for row in rows:
        await asyncio.to_thread(store.abort_multipart, row.s3_key, row.s3_upload_id)
        async with sessionmaker()() as db, db.begin():
            await drop_direct_upload(db, row.id)


# ---- files shared before S3 was set up -------------------------------------------------------


async def move_one_local_file() -> None:
    """Send one older file to S3. The local copy goes only once the database says it is on S3."""
    async with sessionmaker()() as db, db.begin():
        row = (
            await db.execute(
                text(
                    "SELECT id, storage_key, display_key, original_name FROM gallery_media "
                    "WHERE status = 'ready' AND location = 'local' ORDER BY created_at LIMIT 1"
                )
            )
        ).first()
    if row is None:
        return
    root = Path(get_settings().media_root)
    storage = store.file_key(row.id, row.original_name, store.extension_of(row.storage_key))
    display = (
        storage
        if row.display_key == row.storage_key
        else store.playable_key(row.id, store.extension_of(row.display_key))
    )
    try:
        await asyncio.to_thread(store.upload_file, root / row.storage_key, storage)
        if display != storage:
            await asyncio.to_thread(store.upload_file, root / row.display_key, display)
    except FileNotFoundError:
        log.warning("gallery file missing from the media volume", extra={"media_id": str(row.id)})
        async with sessionmaker()() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE gallery_media SET status = 'failed', last_error = 'The file is missing from the server.' "
                    "WHERE id = :m"
                ),
                {"m": row.id},
            )
        return
    async with sessionmaker()() as db, db.begin():
        await db.execute(
            text(
                "UPDATE gallery_media SET location = 's3', storage_key = :s, display_key = :d WHERE id = :m"
            ),
            {"s": storage, "d": display, "m": row.id},
        )
        await db.execute(
            text(
                "UPDATE gallery_people SET folder_wanted = folder_wanted + 1 "
                "WHERE id IN (SELECT person_id FROM gallery_faces WHERE media_id = :m)"
            ),
            {"m": row.id},
        )
    await asyncio.to_thread(remove_public, [row.storage_key, row.display_key])


# ---- one person's folder -------------------------------------------------------------------


async def _folder_for(db: AsyncSession, person: GalleryPerson) -> str | None:
    """The person's folder name, unique in the programme. It stays as long as their name does."""
    if not person.name:
        person.folder = None
        return None
    wanted = store.safe_name(person.name, "Unnamed")
    current = person.folder
    if current and (current == wanted or current.startswith(f"{wanted} (")):
        return current
    taken = {
        value.lower()
        for value in (
            await db.execute(
                text(
                    "SELECT folder FROM gallery_people WHERE programme_id = :p AND folder IS NOT NULL AND id <> :me"
                ),
                {"p": person.programme_id, "me": person.id},
            )
        ).scalars()
    }
    number = 1
    folder = wanted
    while folder.lower() in taken:
        number += 1
        folder = f"{wanted} ({number})"
    person.folder = folder
    return folder


async def _plan(
    person_id: uuid.UUID,
) -> tuple[int, dict[uuid.UUID, tuple[str, str]], dict[uuid.UUID, str]] | None:
    """(the version this plan answers, {file: (real copy, copy in the folder)}, {file: copy there now})."""
    async with sessionmaker()() as db, db.begin():
        person = (
            await db.execute(select(GalleryPerson).where(GalleryPerson.id == person_id).with_for_update())
        ).scalar_one_or_none()
        if person is None:
            return None
        version = person.folder_wanted
        folder = await _folder_for(db, person)
        wanted: dict[uuid.UUID, tuple[str, str]] = {}
        if folder is not None:
            files = await db.execute(
                text(
                    """
                    SELECT m.id, m.storage_key, m.original_name FROM gallery_media m
                    WHERE m.status = 'ready' AND m.location = 's3'
                      AND m.id IN (SELECT media_id FROM gallery_faces WHERE person_id = :p)
                    """
                ),
                {"p": person_id},
            )
            for row in files:
                wanted[row.id] = (
                    row.storage_key,
                    store.person_key(folder, row.id, row.original_name, row.storage_key),
                )
        present = await db.execute(
            text("SELECT media_id, s3_key FROM gallery_person_files WHERE person_id = :p"), {"p": person_id}
        )
        return version, wanted, {row.media_id: row.s3_key for row in present}


async def _record(person_id: uuid.UUID, media_id: uuid.UUID, key: str) -> None:
    async with sessionmaker()() as db, db.begin():
        await db.execute(
            text(
                """
                INSERT INTO gallery_person_files (person_id, media_id, s3_key) VALUES (:p, :m, :k)
                ON CONFLICT (person_id, media_id) DO UPDATE SET s3_key = EXCLUDED.s3_key
                """
            ),
            {"p": person_id, "m": media_id, "k": key},
        )


async def sync_person(person_id: uuid.UUID) -> None:
    plan = await _plan(person_id)
    if plan is None:
        return
    version, wanted, present = plan
    for media_id, (source, target) in wanted.items():
        old = present.get(media_id)
        if old == target:
            continue
        await asyncio.to_thread(store.copy_object, source, target)
        await _record(person_id, media_id, target)
        if old:  # the name changed: the new copy is in place before the old one goes
            await asyncio.to_thread(store.delete_keys, [old])
    gone = {media_id: key for media_id, key in present.items() if media_id not in wanted}
    if gone:
        await asyncio.to_thread(store.delete_keys, list(gone.values()))
        async with sessionmaker()() as db, db.begin():
            await db.execute(
                text("DELETE FROM gallery_person_files WHERE person_id = :p AND media_id = ANY(:ids)"),
                {"p": person_id, "ids": list(gone)},
            )
    # A change that arrived meanwhile has raised folder_wanted past this, so it runs again.
    async with sessionmaker()() as db, db.begin():
        await db.execute(
            text("UPDATE gallery_people SET folder_done = :v WHERE id = :p"), {"v": version, "p": person_id}
        )


async def sync_one_person() -> None:
    async with sessionmaker()() as db, db.begin():
        person_id = (
            await db.execute(
                text(
                    "SELECT id FROM gallery_people WHERE folder_wanted <> folder_done ORDER BY updated_at LIMIT 1"
                )
            )
        ).scalar_one_or_none()
    if person_id is not None:
        await sync_person(person_id)


async def recheck_everyone() -> None:
    """Now and then, in case something changed without saying so. Only costs a look at the database."""
    async with sessionmaker()() as db, db.begin():
        await db.execute(text("UPDATE gallery_people SET folder_wanted = folder_wanted + 1 WHERE name <> ''"))
