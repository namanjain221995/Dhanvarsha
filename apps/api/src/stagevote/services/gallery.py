"""The photo gallery (/gallery): who is visiting, their selfie, uploads, and the people pages.

Anyone with the link may come in, with no account. The first visit asks for a selfie and a name;
the browser then keeps a long-lived cookie, so the form never shows again there. Someone who comes
from the gallery email, or who is signed in as a voter, is known already: their name is filled in
and their person is tied to their participant, so a second device finds the same page.

The API only stores. Files wait in a queue for the gallery worker (worker/gallery.py), which reads
the faces, so an upload is finished for the person as soon as its last byte has arrived.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Request, UploadFile
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import sessionmaker
from ..deps import check_origin, client_ip, load_participant_session
from ..errors import AppError
from ..models import (
    GalleryDevice,
    GalleryDirectUpload,
    GalleryMedia,
    GalleryPerson,
    GalleryStorageTrash,
    Participant,
    Programme,
)
from ..ratelimit import Limit, enforce, hit
from ..security import is_token_shaped, new_token, token_hash
from ..state import Actor, audit
from . import gallery_store as store
from . import media as uploads
from .gallery_match import ASK_AT, MATCH_AT, SAME_SELFIE_AT, from_bytes, to_bytes
from .gallery_model import MIN_CONFIDENCE, describe
from .participants import name_from_email
from .programmes import active_programme, media_url

# At a venue hundreds of phones can share one public address.
ENROL_LIMIT_PER_IP = 300
ENROL_WINDOW_SECONDS = 600
MAX_SELFIE_BYTES = 11_000_000
# What S3 Standard costs for a gigabyte for a month, near enough, for the organisers' estimate.
S3_DOLLARS_PER_GB_MONTH = 0.025
NAME_MAX = 80
# The pages ask for a growing list, so a long page stays one simple request.
PAGE_MAX = 1000
# Two selfies at a time per API process, with the face models of two threads and no more.
_selfie_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallery-selfie")
# One description at a time per API process: the model serves other work on this machine too.
_describe_slots = asyncio.Semaphore(1)
_ESCAPE_LIKE = re.compile(r"([\\%_])")
INVALID_FILE = "Upload a photo (JPEG, PNG, WebP, GIF, HEIC) or a video (MP4, MOV, WebM, MKV, AVI, 3GP)."
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}


def enrol_required() -> AppError:
    return AppError("GALLERY_ENROL_REQUIRED", "Add your selfie and name first.", 401)


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "This was not found. It may have been removed.", 404)


# ---- the personal link in the gallery email ---------------------------------------------


def _link_signature(participant_id: uuid.UUID) -> bytes:
    secret = get_settings().secret_key_bytes()
    return hmac.new(secret, b"gallery-link:" + participant_id.bytes, hashlib.sha256).digest()


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def link_token(participant_id: uuid.UUID) -> str:
    """Says who opened the gallery email's link. It opens nothing but the gallery: it cannot sign
    in to vote, and it needs no storing, so a resent email carries the same link."""
    return f"{_encode(participant_id.bytes)}.{_encode(_link_signature(participant_id))}"


def gallery_link(base_url: str, participant_id: uuid.UUID) -> str:
    # In the fragment, like the sign-in link: browsers never send it to a server or a Referer.
    return f"{base_url}/gallery#token={link_token(participant_id)}"


def participant_of_token(token: str) -> uuid.UUID | None:
    try:
        first, _, second = token.partition(".")
        participant_id = uuid.UUID(bytes=_decode(first))
        signature = _decode(second)
    except ValueError:
        return None
    return participant_id if hmac.compare_digest(signature, _link_signature(participant_id)) else None


# ---- who is visiting --------------------------------------------------------------------


def clean_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise AppError("VALIDATION_ERROR", "Enter your name.", 422, {"fields": {"name": "Enter your name."}})
    if len(name) > NAME_MAX or re.search(r"[\x00-\x1f\x7f]", name):
        message = f"Enter a name of at most {NAME_MAX} characters."
        raise AppError("VALIDATION_ERROR", message, 422, {"fields": {"name": message}})
    return name


async def _person_of_cookie(db: AsyncSession, token: str | None) -> GalleryPerson | None:
    if not token or not is_token_shaped(token):
        return None
    return (
        await db.execute(
            select(GalleryPerson)
            .join(GalleryDevice, GalleryDevice.person_id == GalleryPerson.id)
            .where(GalleryDevice.token_hash == token_hash(token), GalleryPerson.enrolled_at.is_not(None))
        )
    ).scalar_one_or_none()


async def _signed_in_participant(db: AsyncSession, request: Request) -> Participant | None:
    """The voter this browser is signed in as, if any. Never an error: this page needs no sign-in."""
    token = request.cookies.get(get_settings().participant_cookie_name)
    if not token:
        return None
    # In a session of its own: this check commits, and the caller is inside a transaction.
    async with sessionmaker()() as session:
        try:
            context = await load_participant_session(session, token, client_ip(request))
        except AppError:
            return None
    return await db.get(Participant, context.participant_id)


async def _person_of_participant(
    db: AsyncSession, programme_id: uuid.UUID, participant_id: uuid.UUID
) -> GalleryPerson | None:
    return (
        await db.execute(
            select(GalleryPerson).where(
                GalleryPerson.programme_id == programme_id,
                GalleryPerson.participant_id == participant_id,
                GalleryPerson.enrolled_at.is_not(None),
            )
        )
    ).scalar_one_or_none()


async def _token_participant(
    db: AsyncSession, programme_id: uuid.UUID, token: str | None
) -> Participant | None:
    participant_id = participant_of_token(token) if token else None
    if participant_id is None:
        return None
    participant = await db.get(Participant, participant_id)
    if participant is None or participant.programme_id != programme_id or participant.disabled_at is not None:
        return None
    return participant


async def _new_device(db: AsyncSession, person_id: uuid.UUID) -> str:
    token = new_token()
    db.add(GalleryDevice(token_hash=token_hash(token), person_id=person_id))
    await db.flush()
    return token


@dataclass(frozen=True)
class Identity:
    """Who the page is talking to. `device_token` is set when the browser needs a new cookie."""

    person: GalleryPerson | None
    suggested_name: str
    from_email: bool
    device_token: str | None = None


async def identify(
    db: AsyncSession, request: Request, programme: Programme, token: str | None = None
) -> Identity:
    """Anyone who may see the page; the person, if the form has been filled in."""
    person = await _person_of_cookie(db, request.cookies.get(get_settings().gallery_cookie_name))
    if person is not None and person.programme_id == programme.id:
        return Identity(person, person.name, person.participant_id is not None)
    participant = await _token_participant(db, programme.id, token) or await _signed_in_participant(
        db, request
    )
    if participant is None or participant.programme_id != programme.id:
        return Identity(None, "", False)
    person = await _person_of_participant(db, programme.id, participant.id)
    if person is not None:
        return Identity(person, person.name, True, await _new_device(db, person.id))
    # The same name the gallery email greets them by.
    return Identity(None, participant.display_name or name_from_email(participant.email), True)


async def require_person(db: AsyncSession, request: Request) -> GalleryPerson:
    """The person behind this request, for everything past the form."""
    check_origin(request)
    token = request.cookies.get(get_settings().gallery_cookie_name)
    person = await _person_of_cookie(db, token)
    if person is None:
        participant = await _signed_in_participant(db, request)
        if participant is not None:
            person = await _person_of_participant(db, participant.programme_id, participant.id)
    if person is None:
        raise enrol_required()
    return person


def me_view(person: GalleryPerson | None) -> dict[str, Any] | None:
    if person is None:
        return None
    return {"id": str(person.id), "name": person.name, "avatar_url": media_url(person.avatar_key)}


# ---- the selfie and the form -------------------------------------------------------------


async def _read_selfie_file(selfie: UploadFile) -> bytes:
    data = await selfie.read(MAX_SELFIE_BYTES + 1)
    if len(data) > MAX_SELFIE_BYTES:
        raise AppError(
            "FILE_TOO_LARGE", f"The photo must be {MAX_SELFIE_BYTES // 1_000_000} MB or smaller.", 413
        )
    if not data:
        raise AppError("INVALID_IMAGE", "That file is empty. Try another photo.", 422)
    return data


async def _describe_selfie(data: bytes) -> Any:
    # Imported here: only the requests that read a selfie need OpenCV in the API process.
    from .gallery_files import read_selfie

    return await asyncio.get_running_loop().run_in_executor(_selfie_pool, read_selfie, data)


async def _same_face(db: AsyncSession, programme_id: uuid.UUID, embedding: Any) -> GalleryPerson | None:
    """Someone who already gave a selfie this close: the same person on another device."""
    rows = (
        await db.execute(
            select(GalleryPerson).where(
                GalleryPerson.programme_id == programme_id,
                GalleryPerson.enrolled_at.is_not(None),
                GalleryPerson.embedding.is_not(None),
            )
        )
    ).scalars()
    best: GalleryPerson | None = None
    best_score = SAME_SELFIE_AT
    for person in rows:
        assert person.embedding is not None
        score = float(from_bytes(person.embedding) @ embedding)
        if score >= best_score:
            best, best_score = person, score
    return best


async def enrol(
    db: AsyncSession, request: Request, *, name: str, consent: bool, selfie: UploadFile, token: str | None
) -> tuple[GalleryPerson, str | None]:
    """Save the form: returns the person and, when this browser needs one, its new cookie value."""
    if not consent:
        message = "Tick the box to agree, so we can match your face in the photos."
        raise AppError("VALIDATION_ERROR", message, 422, {"fields": {"consent": message}})
    display_name = clean_name(name)
    limit = Limit(f"gallery-enrol:{client_ip(request)}", ENROL_LIMIT_PER_IP, ENROL_WINDOW_SECONDS)
    async with db.begin():
        await enforce(
            db, limit, message="Too many tries from this network. Wait a few minutes and try again."
        )
        await hit(db, limit)
    described = await _describe_selfie(await _read_selfie_file(selfie))
    face = described.face
    from .gallery_files import remove_public, write_public

    avatar = await asyncio.to_thread(write_public, face.crop, "webp")
    settings = get_settings()
    cookie = request.cookies.get(settings.gallery_cookie_name)
    old_avatar: str | None = None
    try:
        async with db.begin():
            programme = await active_programme(db)
            person = await _person_of_cookie(db, cookie)
            device_known = person is not None
            participant = await _token_participant(db, programme.id, token)
            if participant is None:
                participant = await _signed_in_participant(db, request)
            if person is None and participant is not None:
                person = await _person_of_participant(db, programme.id, participant.id)
            if person is None:
                person = await _same_face(db, programme.id, face.embedding)
                if person is not None:
                    # Another device of someone already here. Their own name and picture stay.
                    await asyncio.to_thread(remove_public, [avatar])
                    avatar = person.avatar_key or avatar
                    display_name = person.name or display_name
            if person is None:
                person = GalleryPerson(programme_id=programme.id)
                db.add(person)
            else:
                old_avatar = person.avatar_key if person.avatar_key != avatar else None
            person.name = display_name
            person.embedding = to_bytes(face.embedding)
            person.aligned = face.aligned
            person.avatar_key = avatar
            now = (await db.execute(text("SELECT now()"))).scalar_one()
            person.enrolled_at = person.enrolled_at or now
            person.consent_at = now
            person.rematch_pending = True
            person.folder_wanted = (person.folder_wanted or 0) + 1
            if participant is not None and person.participant_id is None:
                taken = await _person_of_participant(db, programme.id, participant.id)
                if taken is None or taken.id == person.id:
                    person.participant_id = participant.id
            await db.flush()
            device = None if device_known else await _new_device(db, person.id)
    except Exception:
        await asyncio.to_thread(remove_public, [avatar])
        raise
    if old_avatar:
        await _drop_unused(db, old_avatar)
    return person, device


async def _drop_unused(db: AsyncSession, key: str) -> None:
    """Remove a picture, unless a face or a person still shows it."""
    from .gallery_files import remove_public

    async with db.begin():
        used = (
            await db.execute(
                text(
                    "SELECT 1 FROM gallery_faces WHERE crop_key = :k "
                    "UNION ALL SELECT 1 FROM gallery_people WHERE avatar_key = :k LIMIT 1"
                ),
                {"k": key},
            )
        ).first()
    if used is None:
        await asyncio.to_thread(remove_public, [key])


async def _face_files(db: AsyncSession, person_id: uuid.UUID) -> list[str | None]:
    rows = await db.execute(text("SELECT crop_key FROM gallery_faces WHERE person_id = :p"), {"p": person_id})
    return list(rows.scalars())


async def delete_person(db: AsyncSession, person_id: uuid.UUID) -> bool:
    """Remove someone's name, picture and every record of their face. The photos stay: they may
    show other people, and a photo is removed by the organisers, one at a time."""
    from .gallery_files import remove_public

    async with db.begin():
        person = await db.get(GalleryPerson, person_id)
        if person is None:
            return False
        keys = [person.avatar_key, *await _face_files(db, person.id)]
        await db.delete(person)
    await asyncio.to_thread(remove_public, keys)
    return True


# ---- uploading ---------------------------------------------------------------------------


def _ftyp_type(head: bytes) -> tuple[str, str]:
    brand = head[8:12]
    if brand in _AVIF_BRANDS:
        return "image/avif", "avif"
    if brand in _HEIF_BRANDS:
        return "image/heic", "heic"
    if brand.startswith(b"3g"):
        return "video/3gpp", "3gp"
    return ("video/quicktime", "mov") if brand == b"qt  " else ("video/mp4", "mp4")


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
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo", "avi"
    if head[4:8] == b"ftyp":
        return _ftyp_type(head)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ("video/webm", "webm") if b"webm" in head[:64] else ("video/x-matroska", "mkv")
    return None


def _size_problem(size: int, content_type: str) -> AppError | None:
    """A limit of 0 is no limit."""
    settings = get_settings()
    limit = (
        settings.gallery_max_image_bytes
        if content_type.startswith("image/")
        else settings.gallery_max_video_bytes
    )
    if limit <= 0 or size <= limit:
        return None
    what = "Photos" if content_type.startswith("image/") else "Videos"
    return AppError("FILE_TOO_LARGE", f"{what} must be {limit // (1024 * 1024)} MB or smaller.", 413)


def _reject_if_disk_is_low(size: int) -> None:
    settings = get_settings()
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    if (
        os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
        < size + settings.gallery_free_space_reserve_bytes
    ):
        raise AppError(
            "INSUFFICIENT_STORAGE", "The gallery is out of space for now. Tell the organisers.", 507
        )


async def start_upload(
    db: AsyncSession, person: GalleryPerson, name: str, size: int, content_type: str
) -> str:
    await _check_room(db, person, size, content_type)
    await asyncio.to_thread(_reject_if_disk_is_low, size)
    upload = uploads.Upload(uuid.uuid4().hex, person.id, name, size, content_type)
    await asyncio.to_thread(uploads.create_upload, upload)
    return upload.upload_id


async def _check_room(db: AsyncSession, person: GalleryPerson, size: int, content_type: str) -> None:
    """Whatever an organiser has chosen to limit. Nothing, by default."""
    problem = _size_problem(size, content_type)
    if problem:
        raise problem
    settings = get_settings()
    if settings.gallery_uploads_per_hour > 0:
        limit = Limit(f"gallery-upload:{person.id}", settings.gallery_uploads_per_hour, 3600)
        async with db.begin():
            await enforce(db, limit, message="That is a lot of files in one hour. Wait a while and continue.")
            await hit(db, limit)
    if settings.gallery_max_person_bytes > 0:
        async with db.begin():
            shared = (
                await db.execute(
                    text(
                        "SELECT coalesce(sum(byte_size), 0) FROM gallery_media WHERE uploader_person_id = :p"
                    ),
                    {"p": person.id},
                )
            ).scalar_one()
        if int(shared) + size > settings.gallery_max_person_bytes:
            raise AppError("FILE_TOO_LARGE", "You have reached the gallery's limit for one person.", 413)
    if store.enabled() and settings.gallery_max_total_bytes > 0:
        async with db.begin():
            total = (
                await db.execute(
                    text("SELECT coalesce(sum(byte_size), 0) FROM gallery_media WHERE programme_id = :p"),
                    {"p": person.programme_id},
                )
            ).scalar_one()
        if int(total) + size > settings.gallery_max_total_bytes:
            raise AppError("INSUFFICIENT_STORAGE", "The gallery is full for now. Tell the organisers.", 507)


# ---- straight from the browser to S3 --------------------------------------------------------


async def start_direct_upload(
    db: AsyncSession, person: GalleryPerson, name: str, size: int, content_type: str
) -> dict[str, Any]:
    await _check_room(db, person, size, content_type)
    upload_id = uuid.uuid4().hex
    key = store.incoming_key(upload_id)
    s3_upload_id = await asyncio.to_thread(store.start_multipart, key)
    async with db.begin():
        db.add(
            GalleryDirectUpload(
                id=upload_id,
                person_id=person.id,
                s3_key=key,
                s3_upload_id=s3_upload_id,
                name=name[:255],
                size=size,
            )
        )
    return {"upload_id": upload_id, "part_size": store.part_size_for(size), "mode": "s3"}


async def direct_upload(
    db: AsyncSession, upload_id: str, person: GalleryPerson
) -> GalleryDirectUpload | None:
    """The person's own upload to S3 with this id, or None: it may be an upload through this server."""
    async with db.begin():
        row = await db.get(GalleryDirectUpload, upload_id)
        return row if row is not None and row.person_id == person.id else None


async def direct_part_urls(row: GalleryDirectUpload, numbers: list[int]) -> dict[str, str]:
    def sign() -> dict[str, str]:
        return {str(number): store.part_url(row.s3_key, row.s3_upload_id, number) for number in numbers}

    return await asyncio.to_thread(sign)


async def finish_direct_upload(
    db: AsyncSession, row: GalleryDirectUpload, person: GalleryPerson, ip: str
) -> dict[str, Any]:
    """Everything has reached S3: check it and queue it. The first bytes decide what it is, as for any file."""
    key, s3_upload_id, name, size, upload_id = row.s3_key, row.s3_upload_id, row.name, row.size, row.id
    parts = await asyncio.to_thread(store.uploaded_parts, key, s3_upload_id)
    received = sum(int(part["Size"]) for part in parts)
    if received != size:
        raise AppError(
            "UPLOAD_INCOMPLETE",
            "The whole file has not arrived yet. Send the rest, then complete the upload.",
            409,
            {"received": received, "size": size},
        )
    await asyncio.to_thread(store.finish_multipart, key, s3_upload_id, parts)
    found = sniff(await asyncio.to_thread(store.first_bytes, key))
    problem = AppError("INVALID_MEDIA", INVALID_FILE, 422) if found is None else _size_problem(size, found[0])
    if problem:
        async with db.begin():
            db.add(GalleryStorageTrash(s3_key=key))
            await db.execute(text("DELETE FROM gallery_s3_uploads WHERE id = :i"), {"i": upload_id})
        raise problem
    assert found is not None
    item = GalleryMedia(
        programme_id=person.programme_id,
        kind="video" if found[0].startswith("video/") else "image",
        source_key=key,
        source_on_s3=True,
        content_type=found[0],
        byte_size=size,
        # Only the worker sees all of it: it puts the real hash here, and spots a file sent twice.
        sha256=f"pending-{upload_id}",
        original_name=name,
        uploader_person_id=person.id,
        uploader_ip=ip[:64],
    )
    async with db.begin():
        db.add(item)
        await db.flush()
        await db.execute(text("DELETE FROM gallery_s3_uploads WHERE id = :i"), {"i": upload_id})
        media_id = item.id
    return {"status": "uploaded", "duplicate": False, "id": str(media_id)}


async def abort_direct_upload(db: AsyncSession, row: GalleryDirectUpload) -> None:
    key, s3_upload_id, upload_id = row.s3_key, row.s3_upload_id, row.id
    await asyncio.to_thread(store.abort_multipart, key, s3_upload_id)
    async with db.begin():
        await db.execute(text("DELETE FROM gallery_s3_uploads WHERE id = :i"), {"i": upload_id})


async def load_upload(upload_id: str, person: GalleryPerson) -> uploads.Upload:
    return await uploads.load_owned_upload(upload_id, person.id)


@dataclass(frozen=True)
class _Arrived:
    source_key: str
    content_type: str
    size: int
    sha256: str


def _hash_file(fd: int, size: int) -> str:
    digest = hashlib.sha256()
    position = 0
    while position < size:
        block = os.pread(fd, min(8 * 1024 * 1024, size - position), position)
        if not block:
            break
        digest.update(block)
        position += len(block)
    return digest.hexdigest()


def _arrive(upload: uploads.Upload) -> _Arrived:
    """The finished upload, moved to the worker's folder. The file's first bytes say what it is."""
    from .gallery_files import INCOMING, incoming_dir

    part = uploads.part_path(upload.upload_id)
    with uploads.locked(part) as fd:
        received = os.fstat(fd).st_size
        if received != upload.size:
            raise AppError(
                "UPLOAD_INCOMPLETE",
                "The whole file has not arrived yet. Send the rest, then complete the upload.",
                409,
                {"received": received, "size": upload.size},
            )
        found = sniff(os.pread(fd, 64, 0))
        problem = (
            AppError("INVALID_MEDIA", INVALID_FILE, 422)
            if found is None
            else _size_problem(received, found[0])
        )
        if problem:
            # The whole file is here and was refused: sending it again cannot change that.
            uploads.discard(upload.upload_id)
            raise problem
        assert found is not None
        content_type, extension = found
        digest = _hash_file(fd, received)
        os.fsync(fd)
        incoming_dir().mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.{extension}"
        os.replace(part, incoming_dir() / name)
        uploads.discard(upload.upload_id)
    return _Arrived(f"{INCOMING}/{name}", content_type, received, digest)


async def finish_upload(
    db: AsyncSession, upload: uploads.Upload, person: GalleryPerson, ip: str
) -> dict[str, Any]:
    """Queue the file. The same file sent twice counts once, for everyone."""
    arrived = await asyncio.to_thread(_arrive, upload)
    source = Path(get_settings().media_root) / arrived.source_key
    item = GalleryMedia(
        programme_id=person.programme_id,
        kind="video" if arrived.content_type.startswith("video/") else "image",
        source_key=arrived.source_key,
        content_type=arrived.content_type,
        byte_size=arrived.size,
        sha256=arrived.sha256,
        original_name=upload.name[:255],
        uploader_person_id=person.id,
        uploader_ip=ip[:64],
    )
    try:
        async with db.begin():
            db.add(item)
            await db.flush()
            media_id = item.id
    except IntegrityError:
        await asyncio.to_thread(source.unlink, missing_ok=True)
        return {"status": "uploaded", "duplicate": True}
    except Exception:
        await asyncio.to_thread(source.unlink, missing_ok=True)
        raise
    return {"status": "uploaded", "duplicate": False, "id": str(media_id)}


# ---- reading the gallery -----------------------------------------------------------------


def _download_name(row: Any) -> str:
    base = row.original_name.rsplit(".", 1)[0] if "." in row.original_name else row.original_name
    return f"{base or 'dhanvarsha'}.{store.extension_of(row.storage_key or '')}"


def media_view(row: Any) -> dict[str, Any]:
    on_s3 = row.location == "s3" and row.storage_key and row.display_key
    return {
        "id": str(row.id),
        "type": row.kind,
        "url": store.signed_url(row.display_key) if on_s3 else media_url(row.display_key),
        "download_url": store.signed_url(row.storage_key, _download_name(row))
        if on_s3
        else media_url(row.storage_key),
        "thumb_url": media_url(row.thumb_key),
        "width": row.width,
        "height": row.height,
        "duration_ms": row.duration_ms,
        "name": row.original_name,
    }


def _like(query: str) -> str:
    return f"%{_ESCAPE_LIKE.sub(r'\\\1', query)}%"


async def list_people(
    db: AsyncSession, programme_id: uuid.UUID, me: uuid.UUID | None, query: str, offset: int, limit: int
) -> dict[str, Any]:
    """Everyone who has a name, and every unnamed person in a sorted file, so each face in a group
    photo appears at once. A search looks only at names, so it finds a person with no photos yet too.
    `me` comes first, then the most photos."""
    rows = await db.execute(
        text(
            """
            WITH counts AS (
                SELECT f.person_id,
                       count(DISTINCT f.media_id) FILTER (WHERE m.kind = 'image') AS photos,
                       count(DISTINCT f.media_id) FILTER (WHERE m.kind = 'video') AS videos
                FROM gallery_faces f JOIN gallery_media m ON m.id = f.media_id AND m.status = 'ready'
                WHERE m.programme_id = :programme
                GROUP BY f.person_id
            )
            SELECT p.id, p.name, p.avatar_key, coalesce(c.photos, 0) AS photos, coalesce(c.videos, 0) AS videos,
                   count(*) OVER () AS total
            FROM gallery_people p LEFT JOIN counts c ON c.person_id = p.id
            WHERE p.programme_id = :programme
              AND (:query = '' OR p.name ILIKE :like)
              AND (p.enrolled_at IS NOT NULL OR c.person_id IS NOT NULL)
            ORDER BY (p.id = :me) IS TRUE DESC, coalesce(c.photos, 0) + coalesce(c.videos, 0) DESC,
                     p.name <> '' DESC, p.name, p.id
            OFFSET :offset LIMIT :limit
            """
        ),
        {
            "programme": programme_id,
            "query": query,
            "like": _like(query),
            "me": me,
            "offset": offset,
            "limit": limit,
        },
    )
    people = list(rows)
    pending = (
        await db.execute(
            text(
                "SELECT count(*) FROM gallery_media WHERE programme_id = :p AND status IN ('queued', 'processing')"
            ),
            {"p": programme_id},
        )
    ).scalar_one()
    return {
        "people": [
            {
                "id": str(row.id),
                "name": row.name,
                "avatar_url": media_url(row.avatar_key),
                "photos": int(row.photos),
                "videos": int(row.videos),
                "is_me": row.id == me,
            }
            for row in people
        ],
        "total": int(people[0].total) if people else 0,
        "pending": int(pending),
    }


async def person_page(
    db: AsyncSession,
    programme_id: uuid.UUID,
    person_id: uuid.UUID,
    me: uuid.UUID | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    person = await db.get(GalleryPerson, person_id)
    if person is None or person.programme_id != programme_id:
        raise _not_found()
    rows = await db.execute(
        text(
            """
            SELECT m.id, m.kind, m.location, m.display_key, m.storage_key, m.thumb_key, m.width, m.height,
                   m.duration_ms, m.original_name, count(*) OVER () AS total,
                   count(*) FILTER (WHERE m.kind = 'video') OVER () AS videos
            FROM gallery_media m
            WHERE m.status = 'ready'
              AND m.id IN (SELECT media_id FROM gallery_faces WHERE person_id = :person)
            ORDER BY m.created_at DESC, m.id
            OFFSET :offset LIMIT :limit
            """
        ),
        {"person": person_id, "offset": offset, "limit": limit},
    )
    items = list(rows)
    total = int(items[0].total) if items else 0
    videos = int(items[0].videos) if items else 0
    return {
        "person": {
            "id": str(person.id),
            "name": person.name,
            "avatar_url": media_url(person.avatar_key),
            "photos": total - videos,
            "videos": videos,
            "is_me": person.id == me,
        },
        # Signing a long page of links takes a moment, and must not hold up other requests.
        "media": await asyncio.to_thread(lambda: [media_view(row) for row in items]),
        "total": total,
    }


# ---- organisers ---------------------------------------------------------------------------

MediaFilter = Literal["all", "failed", "pending"]


async def summary(db: AsyncSession, programme_id: uuid.UUID) -> dict[str, Any]:
    counts = (
        await db.execute(
            text(
                """
                SELECT count(*) FILTER (WHERE status = 'ready' AND kind = 'image') AS photos,
                       count(*) FILTER (WHERE status = 'ready' AND kind = 'video') AS videos,
                       count(*) FILTER (WHERE status IN ('queued', 'processing')) AS pending,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       coalesce(sum(byte_size), 0) AS bytes
                FROM gallery_media WHERE programme_id = :p
                """
            ),
            {"p": programme_id},
        )
    ).one()
    people = (
        await db.execute(
            text(
                "SELECT count(*) FILTER (WHERE enrolled_at IS NOT NULL) AS enrolled, "
                "count(*) FILTER (WHERE enrolled_at IS NULL) AS unnamed "
                "FROM gallery_people WHERE programme_id = :p"
            ),
            {"p": programme_id},
        )
    ).one()
    settings = get_settings()
    copies = (
        await db.execute(
            text(
                "SELECT coalesce(sum(m.byte_size), 0) FROM gallery_person_files f "
                "JOIN gallery_media m ON m.id = f.media_id WHERE m.programme_id = :p"
            ),
            {"p": programme_id},
        )
    ).scalar_one()
    on_s3 = store.enabled()
    gigabytes = (int(counts.bytes) + int(copies)) / 1024**3
    return {
        "share_url": f"{settings.app_base_url}/gallery",
        "storage": {
            "where": "s3" if on_s3 else "local",
            "bucket": settings.gallery_s3_bucket if on_s3 else None,
            "folder_bytes": int(copies),
            "monthly_usd": round(gigabytes * S3_DOLLARS_PER_GB_MONTH, 2) if on_s3 else None,
            "max_total_bytes": settings.gallery_max_total_bytes if on_s3 else 0,
        },
        "photos": int(counts.photos),
        "videos": int(counts.videos),
        "pending": int(counts.pending),
        "failed": int(counts.failed),
        "bytes": int(counts.bytes),
        "enrolled": int(people.enrolled),
        "unnamed": int(people.unnamed),
        "model_enabled": bool(settings.gallery_model_url),
    }


async def admin_media(
    db: AsyncSession, programme_id: uuid.UUID, kind: MediaFilter, offset: int, limit: int
) -> dict[str, Any]:
    statuses = {
        "all": "",
        "failed": "AND m.status = 'failed'",
        "pending": "AND m.status IN ('queued', 'processing')",
    }
    rows = await db.execute(
        text(
            f"""
            SELECT m.id, m.kind, m.status, m.location, m.display_key, m.storage_key, m.thumb_key, m.width,
                   m.height, m.duration_ms, m.original_name, m.byte_size, m.face_count, m.last_error, m.created_at,
                   m.uploader_ip, p.name AS uploader, count(*) OVER () AS total
            FROM gallery_media m LEFT JOIN gallery_people p ON p.id = m.uploader_person_id
            WHERE m.programme_id = :p {statuses[kind]}
            ORDER BY m.created_at DESC, m.id
            OFFSET :offset LIMIT :limit
            """  # noqa: S608 - the fragment comes from the dictionary above
        ),
        {"p": programme_id, "offset": offset, "limit": limit},
    )
    items = list(rows)

    def view() -> list[dict[str, Any]]:
        return [
            {
                **media_view(row),
                "status": row.status,
                "byte_size": int(row.byte_size),
                "faces": row.face_count,
                "error": row.last_error,
                "uploaded_at": row.created_at,
                "uploader": row.uploader or "",
                "uploader_ip": row.uploader_ip or "",
            }
            for row in items
        ]

    return {"media": await asyncio.to_thread(view), "total": int(items[0].total) if items else 0}


async def delete_media(db: AsyncSession, programme_id: uuid.UUID, media_id: uuid.UUID, actor: Actor) -> bool:
    """Remove a file, its faces and anyone left with no faces and no selfie."""
    from .gallery_files import remove_public

    async with db.begin():
        item = await db.get(GalleryMedia, media_id)
        if item is None or item.programme_id != programme_id:
            return False
        faces = list(
            await db.execute(
                text("SELECT person_id, crop_key FROM gallery_faces WHERE media_id = :m"), {"m": media_id}
            )
        )
        people = list({f.person_id for f in faces})
        source = (
            Path(get_settings().media_root) / item.source_key
            if item.source_key and not item.source_on_s3
            else None
        )
        if item.source_key and item.source_on_s3:
            db.add(GalleryStorageTrash(s3_key=item.source_key))
        keys: list[str | None] = [
            item.storage_key,
            item.display_key,
            item.thumb_key,
            *[f.crop_key for f in faces],
            *[frame["key"] for frame in (item.analysis or {}).get("frames", [])],
        ]
        if item.location == "s3":
            # The worker deletes them from S3, and their copies in people's folders, which it can retry.
            keys = keys[2:]
            for s3_key in {item.storage_key, item.display_key} - {None}:
                db.add(GalleryStorageTrash(s3_key=s3_key))
        await audit(
            db,
            actor,
            "gallery.media_deleted",
            programme_id=programme_id,
            target_type="gallery_media",
            target_id=media_id,
            details={"name": item.original_name, "kind": item.kind, "uploader_ip": item.uploader_ip},
        )
        await db.delete(item)
        await db.flush()
        for person_id in people:
            await refresh_avatar(db, person_id)
        await db.execute(
            text("UPDATE gallery_people SET folder_wanted = folder_wanted + 1 WHERE id = ANY(:ids)"),
            {"ids": people},
        )
        await db.execute(
            text(
                "DELETE FROM gallery_people p WHERE p.enrolled_at IS NULL AND p.id = ANY(:ids) "
                "AND NOT EXISTS (SELECT 1 FROM gallery_faces f WHERE f.person_id = p.id)"
            ),
            {"ids": people},
        )
    await asyncio.to_thread(remove_public, keys)
    if source:
        await asyncio.to_thread(source.unlink, missing_ok=True)
    return True


def _face_view(row: Any, number: int, names: dict[str, str]) -> dict[str, Any]:
    analysis = row.analysis or {}
    return {
        "id": str(row.id),
        "number": number,
        "person_id": str(row.person_id),
        "name": row.name,
        "has_selfie": row.enrolled_at is not None,
        "crop_url": media_url(row.crop_key),
        "frame_ms": row.frame_ms,
        "box": analysis.get("box"),
        "detector_score": analysis.get("detector_score"),
        "size_px": analysis.get("size_px"),
        "match_score": round(row.match_score, 3) if row.match_score is not None else None,
        "matched_by": row.matched_by,
        # Whom it was compared with, under their names today. None: read before this was recorded.
        "checked": [
            {
                "name": names.get(entry["person_id"], ""),
                "listed": entry["person_id"] in names,
                "score": entry["score"],
                "model": entry["model"],
            }
            for entry in analysis["checked"]
        ]
        if "checked" in analysis
        else None,
    }


async def analysis_view(db: AsyncSession, programme_id: uuid.UUID, media_id: uuid.UUID) -> dict[str, Any]:
    """What OpenCV and the vision model made of one file, for the organisers only."""
    item = await db.get(GalleryMedia, media_id)
    if item is None or item.programme_id != programme_id:
        raise _not_found()
    faces = list(
        await db.execute(
            text(
                """
                SELECT f.id, f.person_id, f.crop_key, f.frame_ms, f.match_score, f.matched_by, f.analysis,
                       p.name, p.enrolled_at
                FROM gallery_faces f JOIN gallery_people p ON p.id = f.person_id
                WHERE f.media_id = :m
                """
            ),
            {"m": media_id},
        )
    )
    mentioned = {entry["person_id"] for face in faces for entry in (face.analysis or {}).get("checked", [])}
    names: dict[str, str] = {}
    if mentioned:
        rows = await db.execute(
            text("SELECT id, name FROM gallery_people WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(value) for value in mentioned]},
        )
        names = {str(row.id): row.name for row in rows}

    def place(face: Any) -> tuple[int, float, float]:
        box = (face.analysis or {}).get("box") or [0.0, 0.0]
        return (face.frame_ms or 0, float(box[0]), float(box[1]))

    settings = get_settings()
    analysis = item.analysis or {}
    return {
        "media": {
            **media_view(item),
            "status": item.status,
            "byte_size": int(item.byte_size),
            "error": item.last_error,
            "uploaded_at": item.created_at,
        },
        # Empty for a file not read yet by this version: the worker is filling it in.
        "read": bool(analysis),
        "frames": [
            {"ms": frame["ms"], "url": media_url(frame["key"])} for frame in analysis.get("frames", [])
        ],
        "frames_read": analysis.get("frames_read"),
        "faces_seen": analysis.get("faces_seen"),
        "took_ms": analysis.get("took_ms"),
        "model_questions": analysis.get("model_questions"),
        "description": analysis.get("description"),
        "faces": [_face_view(face, index + 1, names) for index, face in enumerate(sorted(faces, key=place))],
        "rules": {"match_at": MATCH_AT, "ask_at": ASK_AT, "model_confidence": MIN_CONFIDENCE},
        "model_enabled": bool(settings.gallery_model_url),
        "model_name": settings.gallery_model_name,
    }


def _pictures_to_describe(thumb_key: str | None, frame_keys: list[str]) -> list[bytes]:
    """Small JPEG copies of pictures the worker made itself: a video's frames, or the thumbnail."""
    root = Path(get_settings().media_root)
    keys = frame_keys or ([thumb_key] if thumb_key else [])
    pictures: list[bytes] = []
    for key in keys:
        out = io.BytesIO()
        with Image.open(root / key) as image:
            small = image.convert("RGB")
            small.thumbnail((768, 768))
            small.save(out, format="JPEG", quality=85)
        pictures.append(out.getvalue())
    return pictures


async def describe_media(db: AsyncSession, programme_id: uuid.UUID, media_id: uuid.UUID) -> dict[str, Any]:
    """Ask the vision model what a file shows, once. The answer is kept with the file."""
    async with db.begin():
        item = await db.get(GalleryMedia, media_id)
        if item is None or item.programme_id != programme_id:
            raise _not_found()
        if item.status != "ready":
            raise AppError("CONFLICT", "This file has not been read yet.", 409)
        known = (item.analysis or {}).get("description")
        if known:
            return dict(known)
        keys = [frame["key"] for frame in (item.analysis or {}).get("frames", [])]
        spread = [keys[round(i * (len(keys) - 1) / 3)] for i in range(4)] if len(keys) > 4 else keys
        thumb_key = item.thumb_key
    if not get_settings().gallery_model_url:
        raise AppError("MODEL_NOT_SET", "No vision model is set.", 409)
    pictures = await asyncio.to_thread(_pictures_to_describe, thumb_key, spread)
    async with _describe_slots:
        answer = await describe(pictures)
    if answer is None:
        raise AppError("MODEL_UNAVAILABLE", "The vision model did not answer. Try again.", 503)
    async with db.begin():
        item = await db.get(GalleryMedia, media_id)
        if item is None:
            raise _not_found()
        item.analysis = {**(item.analysis or {}), "description": answer}
    return answer


async def refresh_avatar(db: AsyncSession, person_id: uuid.UUID) -> None:
    """An unnamed group shows its clearest face. Someone with a selfie keeps the selfie."""
    await db.execute(
        text(
            """
            UPDATE gallery_people p SET avatar_key = (
                SELECT f.crop_key FROM gallery_faces f WHERE f.person_id = p.id
                ORDER BY f.quality DESC, f.id LIMIT 1
            ), updated_at = now()
            WHERE p.id = :p AND p.enrolled_at IS NULL
            """
        ),
        {"p": person_id},
    )


async def merge_people(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """Everything of `source` becomes `target`'s. The caller holds a transaction."""
    source = await db.get(GalleryPerson, source_id)
    target = await db.get(GalleryPerson, target_id)
    if source is None or target is None or source.programme_id != target.programme_id:
        raise _not_found()
    await db.execute(
        text("UPDATE gallery_faces SET person_id = :t, matched_by = 'merge' WHERE person_id = :s"),
        {"s": source_id, "t": target_id},
    )
    await db.execute(
        text("UPDATE gallery_devices SET person_id = :t WHERE person_id = :s"),
        {"s": source_id, "t": target_id},
    )
    await db.execute(
        text("UPDATE gallery_media SET uploader_person_id = :t WHERE uploader_person_id = :s"),
        {"s": source_id, "t": target_id},
    )
    participant = source.participant_id
    if target.enrolled_at is None and source.enrolled_at is not None:
        target.embedding, target.aligned = source.embedding, source.aligned
        target.avatar_key, target.enrolled_at = source.avatar_key, source.enrolled_at
        target.consent_at = source.consent_at
    target.name = target.name or source.name
    target.folder_wanted = (target.folder_wanted or 0) + 1
    await db.delete(source)
    await db.flush()
    if participant is not None and target.participant_id is None:
        target.participant_id = participant
    await refresh_avatar(db, target_id)
