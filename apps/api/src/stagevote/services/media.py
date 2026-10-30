"""The images and videos an opening-page card shows full screen when its button is pressed.

A video is far larger than one request may be (Cloudflare refuses request bodies over
100 MB and Caddy over 12 MB), so files arrive in chunks of at most CHUNK_SIZE bytes:

1. Start: the organiser gives the file's name and size. The size limit and the free disk
   space are checked, and `<media_root>/.uploads/` gets `<id>.part` (empty) and `<id>.json`
   (who started it, the name, the size). Caddy serves only `<32 hex>.<ext>` names from the
   media root, so nothing under .uploads is ever reachable from a browser.
2. Chunks: each request appends its bytes at `offset`, which must equal what is stored so
   far. A retried request can never write a chunk twice, and a mismatch answers with the
   stored size so the browser resumes from there.
3. Complete: the type comes from the file's first bytes, never from its name or from what
   the browser said. Images are decoded and re-encoded to WebP like every other picture.
   Videos are kept byte for byte and never decoded: the .part file is renamed into the
   media root, which is the same filesystem, so the move is atomic.

The state of an upload is its two files, so any API process can serve any request of it.
A lock on the .part file serialises the requests of one upload. Uploads left unfinished
are removed when an upload starts more than STALE_AFTER_SECONDS after their last chunk.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..deps import AdminContext
from ..errors import AppError
from ..models import Asset
from ..state import audit
from .assets import process_image

KIND = "opening_media"
CHUNK_SIZE = 8 * 1024 * 1024
# Free space that must remain on the media volume after an upload: the database, logs and
# the other uploads share the disk.
FREE_SPACE_RESERVE = 1024 * 1024 * 1024
STALE_AFTER_SECONDS = 24 * 3600
UPLOAD_DIR = ".uploads"

MediaType = Literal["image", "video"]

_UPLOAD_ID = re.compile(r"[a-f0-9]{32}")
_MB = 1024 * 1024
_READ_BLOCK = 1024 * 1024
INVALID_MEDIA = "Upload a JPEG, PNG, WebP or GIF image, or an MP4 or WebM video."
# ISO base media files share MP4's "ftyp" box; these major brands are still images (HEIC,
# AVIF), which a <video> element cannot play.
_IMAGE_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"}


@dataclass(frozen=True)
class Upload:
    """An unfinished upload, as its .json file describes it."""

    upload_id: str
    admin_id: uuid.UUID
    name: str
    size: int
    content_type: str


@dataclass(frozen=True)
class Stored:
    """A finished file in the media root."""

    storage_key: str
    content_type: str
    width: int
    height: int
    byte_size: int
    sha256: str


def media_type(content_type: str) -> MediaType:
    return "video" if content_type.startswith("video/") else "image"


def _root() -> Path:
    return Path(get_settings().media_root)


def _uploads() -> Path:
    return _root() / UPLOAD_DIR


def _part(upload_id: str) -> Path:
    return _uploads() / f"{upload_id}.part"


def _meta(upload_id: str) -> Path:
    return _uploads() / f"{upload_id}.json"


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "This upload was not found. Start the upload again.", 404)


def _too_large(what: str, limit: int) -> AppError:
    return AppError("FILE_TOO_LARGE", f"{what} must be {limit // _MB} MB or smaller.", 413)


def _no_space() -> AppError:
    return AppError(
        "INSUFFICIENT_STORAGE", "The server does not have enough free disk space for this file.", 507
    )


def sniff(head: bytes) -> tuple[str, str] | None:
    """(content type, file extension) of an accepted file from its first 64 bytes, else None."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    if head[4:8] == b"ftyp" and head[8:12] not in _IMAGE_BRANDS:
        return "video/mp4", "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in head[:64]:
        return "video/webm", "webm"
    return None


# ---- starting -------------------------------------------------------------------


def check_size(size: int, content_type: str) -> None:
    """The limit for the kind of file the browser says it is. Complete checks the real kind."""
    settings = get_settings()
    if content_type.startswith("image/"):
        if size > settings.max_media_image_bytes:
            raise _too_large("Images", settings.max_media_image_bytes)
    elif size > settings.max_media_bytes:
        raise _too_large("Videos", settings.max_media_bytes)


def _sweep(directory: Path, now: float) -> None:
    """Remove every upload whose newest file is older than STALE_AFTER_SECONDS."""
    newest: dict[str, float] = {}
    files: dict[str, list[Path]] = {}
    for entry in directory.iterdir():
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        key = entry.name.split(".", 1)[0]
        newest[key] = max(newest.get(key, 0.0), mtime)
        files.setdefault(key, []).append(entry)
    for key, mtime in newest.items():
        if now - mtime > STALE_AFTER_SECONDS:
            for entry in files[key]:
                entry.unlink(missing_ok=True)


def _create(upload: Upload) -> None:
    directory = _uploads()
    directory.mkdir(parents=True, exist_ok=True)
    _sweep(directory, time.time())
    if _free_bytes(directory) < upload.size + FREE_SPACE_RESERVE:
        raise _no_space()
    _part(upload.upload_id).touch(exist_ok=False)
    meta = {
        "admin_id": str(upload.admin_id),
        "name": upload.name,
        "size": upload.size,
        "content_type": upload.content_type,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = _uploads() / f"{upload.upload_id}.json.tmp"
    tmp.write_text(json.dumps(meta))
    os.replace(tmp, _meta(upload.upload_id))


async def start_upload(ctx: AdminContext, name: str, size: int, content_type: str) -> str:
    check_size(size, content_type)
    upload = Upload(uuid.uuid4().hex, ctx.admin_id, name, size, content_type)
    await asyncio.to_thread(_create, upload)
    return upload.upload_id


# ---- chunks -----------------------------------------------------------------------


def _read_meta(upload_id: str) -> Upload | None:
    try:
        raw = json.loads(_meta(upload_id).read_text())
        return Upload(
            upload_id,
            uuid.UUID(raw["admin_id"]),
            str(raw["name"]),
            int(raw["size"]),
            str(raw.get("content_type", "")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def load_upload(upload_id: str, ctx: AdminContext) -> Upload:
    """The upload, if it exists and this organiser started it (404 otherwise)."""
    if not _UPLOAD_ID.fullmatch(upload_id):
        raise _not_found()
    upload = await asyncio.to_thread(_read_meta, upload_id)
    if upload is None or upload.admin_id != ctx.admin_id:
        raise _not_found()
    return upload


@contextmanager
def _locked(path: Path) -> Iterator[int]:
    """The .part file, open and locked. 404 when the upload ended while this request waited."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        raise _not_found() from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            same = os.stat(path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if not same:  # completed (renamed) or aborted (removed) by another request
            raise _not_found()
        yield fd
    finally:
        os.close(fd)  # also releases the lock


def _append(upload: Upload, offset: int, data: bytearray) -> int:
    with _locked(_part(upload.upload_id)) as fd:
        received = os.fstat(fd).st_size
        if offset != received:
            raise AppError(
                "UPLOAD_OFFSET_MISMATCH",
                "This part of the file does not follow the part stored so far. Resume from the stored size.",
                409,
                {"received": received},
            )
        if received + len(data) > upload.size:
            raise AppError(
                "UPLOAD_SIZE_EXCEEDED",
                "The file is larger than the size given when the upload started.",
                413,
                {"received": received},
            )
        view = memoryview(data)
        position = received
        try:
            while view:
                written = os.pwrite(fd, view, position)
                view = view[written:]
                position += written
        except OSError as exc:
            os.ftruncate(fd, received)  # keep the stored part whole, so the chunk can be resent
            if exc.errno == errno.ENOSPC:
                raise _no_space() from exc
            raise
        return position


async def write_chunk(upload: Upload, offset: int, data: bytearray) -> int:
    """Append `data` at `offset`; returns the bytes stored so far."""
    return await asyncio.to_thread(_append, upload, offset, data)


# ---- completing -------------------------------------------------------------------


def _discard(upload_id: str) -> None:
    _part(upload_id).unlink(missing_ok=True)
    _meta(upload_id).unlink(missing_ok=True)


def _read_all(fd: int, size: int) -> bytes:
    chunks = []
    position = 0
    while position < size:
        block = os.pread(fd, min(_READ_BLOCK, size - position), position)
        if not block:
            break
        chunks.append(block)
        position += len(block)
    return b"".join(chunks)


def _store_image(fd: int, size: int) -> Stored:
    settings = get_settings()
    if size > settings.max_media_image_bytes:
        raise _too_large("Images", settings.max_media_image_bytes)
    try:
        encoded, width, height = process_image(_read_all(fd, size), KIND)
    except AppError as exc:
        if exc.code == "INVALID_IMAGE":
            raise AppError("INVALID_MEDIA", INVALID_MEDIA, 422) from exc
        raise
    key = f"{uuid.uuid4().hex}.webp"
    tmp = _root() / f".{key}.tmp"
    tmp.write_bytes(encoded)
    os.replace(tmp, _root() / key)
    return Stored(key, "image/webp", width, height, len(encoded), hashlib.sha256(encoded).hexdigest())


def _store_video(fd: int, part: Path, size: int, content_type: str, extension: str) -> Stored:
    settings = get_settings()
    if size > settings.max_media_bytes:
        raise _too_large("Videos", settings.max_media_bytes)
    digest = hashlib.sha256()
    position = 0
    while position < size:
        block = os.pread(fd, min(_READ_BLOCK, size - position), position)
        if not block:
            break
        digest.update(block)
        position += len(block)
    os.fsync(fd)
    key = f"{uuid.uuid4().hex}.{extension}"
    os.replace(part, _root() / key)
    return Stored(key, content_type, 0, 0, size, digest.hexdigest())


def _finish(upload: Upload) -> Stored:
    part = _part(upload.upload_id)
    with _locked(part) as fd:
        received = os.fstat(fd).st_size
        if received != upload.size:
            raise AppError(
                "UPLOAD_INCOMPLETE",
                "The whole file has not arrived yet. Send the rest, then complete the upload.",
                409,
                {"received": received, "size": upload.size},
            )
        found = sniff(os.pread(fd, 64, 0))
        try:
            if found is None:
                raise AppError("INVALID_MEDIA", INVALID_MEDIA, 422)
            content_type, extension = found
            if content_type.startswith("video/"):
                stored = _store_video(fd, part, received, content_type, extension)
            else:
                stored = _store_image(fd, received)
        except AppError:
            # The whole file is here and was refused: sending it again cannot change that.
            _discard(upload.upload_id)
            raise
        _discard(upload.upload_id)
        return stored


async def finish_upload(db: AsyncSession, upload: Upload, ctx: AdminContext) -> Asset:
    stored = await asyncio.to_thread(_finish, upload)
    asset = Asset(
        kind=KIND,
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        width=stored.width,
        height=stored.height,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        original_name=upload.name,
        created_by_admin_id=ctx.admin_id,
    )
    try:
        async with db.begin():
            db.add(asset)
            await db.flush()
            await audit(
                db,
                ctx.actor,
                "asset.uploaded",
                target_type="asset",
                target_id=asset.id,
                details={"kind": KIND, "type": media_type(stored.content_type), "bytes": stored.byte_size},
            )
    except Exception:
        await asyncio.to_thread((_root() / stored.storage_key).unlink, missing_ok=True)
        raise
    return asset


# ---- aborting -----------------------------------------------------------------------


def _abort(upload: Upload) -> None:
    try:
        with _locked(_part(upload.upload_id)):
            _discard(upload.upload_id)
    except AppError:
        _discard(upload.upload_id)  # the .part file was already gone: drop what is left


async def abort_upload(upload: Upload) -> None:
    await asyncio.to_thread(_abort, upload)
