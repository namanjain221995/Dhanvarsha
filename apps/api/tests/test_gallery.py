"""The photo gallery: the form, uploads, sorting faces into people, the email, and removal.

OpenCV is replaced by a stand-in in most tests, so each can say exactly who is in a picture: the
colour of its first pixel names the people (see `_fake_faces`). The real detector and recogniser
are tested at the end on a public-domain portrait, when their model files are on this machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import piexif
import pytest
from PIL import Image

from helpers import ADMIN_API, API, World, assert_error, drain
from stagevote.config import get_settings
from stagevote.mailer.providers import RecordingProvider
from stagevote.services import gallery, gallery_files, gallery_match, gallery_model
from stagevote.services.gallery_faces import Face
from stagevote.services.participants import name_from_email
from stagevote.worker import gallery as gallery_worker
from stagevote.worker.gallery import GalleryWorker
from stagevote.worker.runner import Worker

GALLERY = f"{API}/gallery"
ADMIN = f"{ADMIN_API}/gallery"
# AdminClient adds the /api/v1/admin in front.
ORGANISER = "/gallery"
CROP = io.BytesIO()
Image.new("RGB", (8, 8), (9, 9, 9)).save(CROP, format="WEBP")
FIXTURES = Path(__file__).parent / "fixtures"


def _identity(index: int, score: float = 1.0) -> np.ndarray:
    """A face of person `index`: `score` is its cosine with that person's clearest face."""
    vector = np.zeros(128, dtype=np.float32)
    vector[index] = score
    # What differs: its own direction, so two unclear faces are no closer to each other.
    vector[40 + (index * 7 + round(score * 100)) % 88] += math.sqrt(max(0.0, 1 - score * score))
    return vector


def _face(index: int, score: float = 1.0, size_px: int = 200) -> Face:
    return Face(
        embedding=_identity(index, score),
        aligned=f"aligned-{index}".encode(),
        crop=CROP.getvalue(),
        size_px=size_px,
        quality=score * min(1.0, size_px / 160),
        box=(0.1, 0.2, 0.3, 0.4),
        detector_score=0.9,
    )


def _fake_faces(bgr: np.ndarray, *, min_px: int = 24) -> list[Face]:
    """Red names the first person (0 is nobody), blue a second one, green/100 how clear the first is."""
    blue, green, red = (int(v) for v in bgr[0, 0])
    if red == 0:
        return []
    faces = [_face(red, green / 100 if green else 1.0)]
    if blue:
        faces.append(_face(blue))
    return faces


def _picture(person: int = 0, *, clear: int = 0, second: int = 0, salt: int = 0) -> bytes:
    """A PNG naming who is in it. `salt` makes otherwise equal files different."""
    image = Image.new("RGB", (64, 64), (person, clear, second))
    image.putpixel((63, 63), (salt % 256, salt // 256, 7))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def stand_in_for_opencv(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if "real_opencv" not in request.keywords:
        monkeypatch.setattr(gallery_files, "find_faces", _fake_faces)


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bytes, bytes]]:
    """The vision model is asked nothing it can answer. Returns the questions it got."""
    asked: list[tuple[bytes, bytes]] = []

    async def unavailable(first: bytes, second: bytes) -> bool | None:
        asked.append((first, second))
        return None

    monkeypatch.setattr(gallery_worker, "same_person", unavailable)
    return asked


class Visitor:
    """One browser on the gallery page."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def me(self) -> dict[str, Any]:
        resp = await self.http.get(f"{GALLERY}/me")
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    async def enrol(
        self, name: str = "Asha Rao", selfie: bytes | None = None, *, consent: bool = True, token: str = ""
    ) -> httpx.Response:
        data: dict[str, Any] = {"name": name, "consent": "true" if consent else "false"}
        if token:
            data["token"] = token
        files = {"selfie": ("selfie.png", selfie if selfie is not None else _picture(1), "image/png")}
        return await self.http.post(f"{GALLERY}/enrol", data=data, files=files)

    async def enrolled(self, name: str = "Asha Rao", person: int = 1) -> str:
        resp = await self.enrol(name, _picture(person))
        assert resp.status_code == 200, resp.text
        return str(resp.json()["person"]["id"])

    async def upload(
        self, data: bytes, name: str = "photo.png", content_type: str = "image/png"
    ) -> httpx.Response:
        started = await self.http.post(
            f"{GALLERY}/uploads", json={"name": name, "size": len(data), "content_type": content_type}
        )
        assert started.status_code == 201, started.text
        upload_id = started.json()["upload_id"]
        sent = await self.http.put(
            f"{GALLERY}/uploads/{upload_id}",
            params={"offset": 0},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert sent.status_code == 200, sent.text
        return await self.http.post(f"{GALLERY}/uploads/{upload_id}/complete")

    async def people(self, q: str = "") -> dict[str, Any]:
        resp = await self.http.get(f"{GALLERY}/people", params={"q": q})
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    async def page(self, person_id: str) -> dict[str, Any]:
        resp = await self.http.get(f"{GALLERY}/people/{person_id}")
        assert resp.status_code == 200, resp.text
        return dict(resp.json())


@pytest.fixture
def visitor(world: World) -> Visitor:
    return Visitor(world.http())


async def _sort(world: World) -> None:
    """Run the gallery worker until the queue and the selfies are done."""
    worker = GalleryWorker(get_settings())
    for _ in range(200):
        await worker.tick()
        if worker.in_flight:
            await asyncio.wait(worker.in_flight)
            continue
        left = await world.factory.fetchval(
            "SELECT count(*) FROM gallery_media WHERE status IN ('queued', 'processing') AND next_attempt_at <= now()"
        )
        if not left:
            await worker.tick()
            return
    raise AssertionError("the gallery worker did not finish")


def _root() -> Path:
    return Path(get_settings().media_root)


# ---- the form --------------------------------------------------------------------------------


async def test_the_form_comes_first_and_only_once(world: World, visitor: Visitor) -> None:
    me = await visitor.me()
    assert me["person"] is None
    assert me["programme"]["name"]
    assert_error(await visitor.http.get(f"{GALLERY}/people"), 401, "GALLERY_ENROL_REQUIRED")
    assert_error(
        await visitor.http.post(
            f"{GALLERY}/uploads", json={"name": "a.png", "size": 3, "content_type": "image/png"}
        ),
        401,
        "GALLERY_ENROL_REQUIRED",
    )

    resp = await visitor.enrol("  Asha   Rao ", _picture(1))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    person = resp.json()["person"]
    assert person["name"] == "Asha Rao"
    assert person["avatar_url"].endswith(".webp")
    set_cookie = resp.headers["set-cookie"]
    assert "sv_gallery=" in set_cookie and "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie

    again = await visitor.me()
    assert again["person"]["id"] == person["id"]
    assert (await visitor.people())["people"][0]["is_me"] is True


async def test_the_form_needs_consent_a_name_and_a_face(world: World, visitor: Visitor) -> None:
    assert_error(await visitor.enrol(consent=False), 422, "VALIDATION_ERROR")
    assert_error(await visitor.enrol("   "), 422, "VALIDATION_ERROR")
    assert_error(await visitor.enrol("x" * 81), 422, "VALIDATION_ERROR")
    assert_error(await visitor.enrol(selfie=_picture(0)), 422, "NO_FACE")
    assert_error(await visitor.enrol(selfie=b"not a picture"), 422, "INVALID_IMAGE")
    assert_error(await visitor.enrol(selfie=b""), 422, "INVALID_IMAGE")
    assert (await visitor.me())["person"] is None
    rows = await world.factory.fetchval(
        "SELECT count(*) FROM gallery_people WHERE programme_id = :p", p=world.programme_id
    )
    assert rows == 0


async def test_a_tiny_face_is_refused(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [_face(1, size_px=30)])
    assert_error(await visitor.enrol(), 422, "FACE_TOO_SMALL")


async def test_a_second_device_with_the_same_face_joins_the_same_person(
    world: World, visitor: Visitor
) -> None:
    first = await visitor.enrolled("Asha Rao", person=3)
    phone = Visitor(world.http())
    resp = await phone.enrol("Someone Else", _picture(3))
    assert resp.status_code == 200, resp.text
    assert resp.json()["person"]["id"] == first
    assert resp.json()["person"]["name"] == "Asha Rao"  # the first form's name stays
    stranger = Visitor(world.http())
    other = await stranger.enrol("Ben", _picture(4))
    assert other.json()["person"]["id"] != first


async def test_sending_the_form_again_replaces_name_and_picture(world: World, visitor: Visitor) -> None:
    first = await visitor.enrol("Asha", _picture(1))
    old_avatar = first.json()["person"]["avatar_url"].removeprefix("/media/")
    assert (_root() / old_avatar).exists()
    second = await visitor.enrol("Asha Rao", _picture(1, salt=9))
    assert second.json()["person"]["id"] == first.json()["person"]["id"]
    assert second.json()["person"]["name"] == "Asha Rao"
    assert not (_root() / old_avatar).exists()
    assert "set-cookie" not in second.headers  # the browser's cookie stays


# ---- uploading ---------------------------------------------------------------------------------


async def test_an_upload_only_says_uploaded_and_goes_public_once_read(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    data = _picture(1, salt=1)
    resp = await visitor.upload(data, "Stage & friends.png")
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "uploaded"
    assert set(resp.json()) == {"status", "duplicate", "id"}

    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "queued" and row.storage_key is None
    assert row.source_key.startswith(".gallery-incoming/")  # a folder Caddy never serves
    assert (_root() / row.source_key).read_bytes() == data
    assert row.uploader_ip == world.ip

    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "ready" and row.source_key is None and row.face_count == 1
    assert (_root() / row.storage_key).read_bytes() == data  # exactly what was sent
    assert row.display_key == row.storage_key
    assert row.thumb_key.endswith(".webp")
    assert list((_root() / gallery_files.INCOMING).iterdir()) == []


async def test_the_same_file_twice_counts_once(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    data = _picture(1, salt=2)
    first = await visitor.upload(data)
    other = Visitor(world.http())
    await other.enrolled("Ben", person=2)
    second = await other.upload(data, "copy.png")
    assert first.json()["duplicate"] is False
    assert second.status_code == 201 and second.json()["duplicate"] is True
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM gallery_media WHERE sha256 = :s", s=hashlib.sha256(data).hexdigest()
        )
        == 1
    )


@pytest.mark.parametrize(
    "content",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!doctype html><html></html>",
        b"MZ\x90\x00",
    ],
)
async def test_only_pictures_and_videos_are_accepted(world: World, visitor: Visitor, content: bytes) -> None:
    await visitor.enrolled()
    assert_error(await visitor.upload(content, "photo.jpg", "image/jpeg"), 422, "INVALID_MEDIA")


def test_files_are_named_by_their_first_bytes() -> None:
    ftyp = b"\x00\x00\x00\x18ftyp"
    assert gallery.sniff(b"\xff\xd8\xff\xe0") == ("image/jpeg", "jpg")
    assert gallery.sniff(ftyp + b"heic0000") == ("image/heic", "heic")
    assert gallery.sniff(ftyp + b"mif10000") == ("image/heic", "heic")
    assert gallery.sniff(ftyp + b"avif0000") == ("image/avif", "avif")
    assert gallery.sniff(ftyp + b"qt  0000") == ("video/quicktime", "mov")
    assert gallery.sniff(ftyp + b"isom0000") == ("video/mp4", "mp4")
    assert gallery.sniff(ftyp + b"3gp40000") == ("video/3gpp", "3gp")
    assert gallery.sniff(b"RIFF\x00\x00\x00\x00AVI ") == ("video/x-msvideo", "avi")
    assert gallery.sniff(b"\x1a\x45\xdf\xa3\x01webm") == ("video/webm", "webm")
    assert gallery.sniff(b"\x1a\x45\xdf\xa3\x01matroska") == ("video/x-matroska", "mkv")
    assert gallery.sniff(b"#EXTM3U\n") is None  # a playlist would make ffmpeg fetch other files
    assert gallery.sniff(b"<svg") is None


async def test_size_limits_and_a_full_disk_refuse_at_the_start(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    settings = get_settings()
    monkeypatch.setattr(settings, "gallery_max_image_bytes", 1000)
    too_big = await visitor.http.post(
        f"{GALLERY}/uploads", json={"name": "a.jpg", "size": 1001, "content_type": "image/jpeg"}
    )
    assert_error(too_big, 413, "FILE_TOO_LARGE")
    monkeypatch.setattr(settings, "gallery_max_image_bytes", 0)
    monkeypatch.setattr(settings, "gallery_free_space_reserve_bytes", 1 << 62)
    full = await visitor.http.post(
        f"{GALLERY}/uploads", json={"name": "a.jpg", "size": 10, "content_type": "image/jpeg"}
    )
    assert_error(full, 507, "INSUFFICIENT_STORAGE")


async def test_there_is_no_limit_on_size_or_number_by_default(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    settings = get_settings()
    assert settings.gallery_max_image_bytes == settings.gallery_max_video_bytes == 0
    assert settings.gallery_max_person_bytes == settings.gallery_uploads_per_hour == 0
    me = await visitor.me()
    assert (me["max_image_bytes"], me["max_video_bytes"]) == (0, 0)
    # A 50 TB video may start, whatever the disk has: only the disk itself can refuse it.
    monkeypatch.setattr(gallery, "_reject_if_disk_is_low", lambda size: None)
    monkeypatch.setattr(gallery.uploads, "_free_bytes", lambda path: 1 << 62)
    huge = await visitor.http.post(
        f"{GALLERY}/uploads",
        json={"name": "all-day.mov", "size": 50 * 10**12, "content_type": "video/quicktime"},
    )
    assert huge.status_code == 201, huge.text
    assert (await visitor.http.delete(f"{GALLERY}/uploads/{huge.json()['upload_id']}")).status_code == 204
    for salt in range(3):
        assert (await visitor.upload(_picture(1, salt=140 + salt))).status_code == 201


async def test_the_limits_still_work_when_someone_sets_them(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    settings = get_settings()
    start = {"name": "a.png", "size": 1000, "content_type": "image/png"}
    monkeypatch.setattr(settings, "gallery_uploads_per_hour", 1)
    assert (await visitor.http.post(f"{GALLERY}/uploads", json=start)).status_code == 201
    assert_error(await visitor.http.post(f"{GALLERY}/uploads", json=start), 429, "RATE_LIMITED")
    monkeypatch.setattr(settings, "gallery_uploads_per_hour", 0)
    monkeypatch.setattr(settings, "gallery_max_person_bytes", 100)
    assert_error(await visitor.http.post(f"{GALLERY}/uploads", json=start), 413, "FILE_TOO_LARGE")


async def test_someone_elses_upload_cannot_be_touched(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    started = await visitor.http.post(
        f"{GALLERY}/uploads", json={"name": "a.png", "size": 5, "content_type": "image/png"}
    )
    upload_id = started.json()["upload_id"]
    other = Visitor(world.http())
    await other.enrolled("Ben", person=2)
    assert_error(
        await other.http.put(f"{GALLERY}/uploads/{upload_id}", params={"offset": 0}, content=b"12345"),
        404,
        "NOT_FOUND",
    )
    assert_error(await other.http.post(f"{GALLERY}/uploads/{upload_id}/complete"), 404, "NOT_FOUND")
    admin = await world.admin()
    assert_error(await admin.media_chunk(upload_id, 0, b"12345"), 404, "NOT_FOUND")


async def test_a_request_from_another_site_is_refused(world: World) -> None:
    hostile = Visitor(world.http(origin="https://evil.example"))
    assert_error(await hostile.enrol(), 403, "BAD_ORIGIN")


# ---- files ---------------------------------------------------------------------------------------


async def test_a_jpeg_keeps_its_picture_but_loses_its_location(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = io.BytesIO()
    Image.effect_noise((200, 100), 60).convert("RGB").save(out, format="JPEG", quality=90)
    exif = piexif.dump(
        {
            "0th": {piexif.ImageIFD.Orientation: 6, piexif.ImageIFD.Make: b"Phone"},
            "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((19, 1), (4, 1), (0, 1))},
        }
    )
    target = io.BytesIO()
    piexif.insert(exif, out.getvalue(), target)
    await visitor.enrolled()
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [])
    resp = await visitor.upload(target.getvalue(), "holiday.jpg", "image/jpeg")
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    public = (_root() / row.storage_key).read_bytes()
    assert piexif.load(public)["GPS"] == {}
    assert piexif.load(public)["0th"][piexif.ImageIFD.Make] == b"Phone"
    # The scan data, from the start-of-scan marker on, is the same bytes.
    assert public[public.index(b"\xff\xda") :] == out.getvalue()[out.getvalue().index(b"\xff\xda") :]
    # Width and height as the picture is shown: the camera held it turned.
    assert (row.width, row.height) == (100, 200)


async def test_an_iphone_photo_becomes_a_jpeg_every_browser_shows(world: World, visitor: Visitor) -> None:
    import pillow_heif

    out = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (64, 64), (5, 0, 0))).save(out, quality=90)
    await visitor.enrolled()
    resp = await visitor.upload(out.getvalue(), "IMG_0001.HEIC", "image/heic")
    assert resp.status_code == 201, resp.text
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "ready"
    assert row.storage_key.endswith(".jpg") and row.storage_key == row.display_key
    assert (_root() / row.storage_key).read_bytes().startswith(b"\xff\xd8\xff")


async def test_an_unreadable_file_fails_for_good_and_is_removed(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    resp = await visitor.upload(
        b"\xff\xd8\xff\xe0 this is no picture" + bytes(200), "broken.jpg", "image/jpeg"
    )
    assert resp.status_code == 201  # only the worker decodes it
    source = await world.factory.fetchval(
        "SELECT source_key FROM gallery_media WHERE id = :m", m=resp.json()["id"]
    )
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "failed" and row.source_key is None and row.storage_key is None
    assert row.last_error
    assert not (_root() / source).exists()


async def test_a_worker_that_stops_gives_its_file_back_to_the_queue(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    resp = await visitor.upload(_picture(1, salt=11))
    worker = GalleryWorker(get_settings())
    assert await worker.lease(5) == [uuid.UUID(resp.json()["id"])]
    await world.factory.execute(
        "UPDATE gallery_media SET lease_expires_at = now() - interval '1 minute' WHERE id = :m",
        m=resp.json()["id"],
    )
    await worker.recover()
    assert (
        await world.factory.fetchval("SELECT status FROM gallery_media WHERE id = :m", m=resp.json()["id"])
        == "queued"
    )
    await _sort(world)
    assert (
        await world.factory.fetchval("SELECT status FROM gallery_media WHERE id = :m", m=resp.json()["id"])
        == "ready"
    )


# ---- sorting faces into people ------------------------------------------------------------------


async def test_a_group_photo_shows_on_the_page_of_each_person_in_it(world: World, visitor: Visitor) -> None:
    asha = await visitor.enrolled("Asha", person=1)
    other = Visitor(world.http())
    ben = await other.enrolled("Ben", person=2)
    await _sort(world)
    group = _picture(1, second=2, salt=20)
    resp = await visitor.upload(group, "group.png")
    await _sort(world)

    for person in (asha, ben):
        page = await visitor.page(person)
        assert [m["id"] for m in page["media"]] == [resp.json()["id"]]
        assert page["person"]["photos"] == 1
    item = (await visitor.page(asha))["media"][0]
    assert item["name"] == "group.png" and item["type"] == "image"
    public = item["download_url"].removeprefix("/media/")
    assert (_root() / public).read_bytes() == group  # no marks: the same bytes that were sent


async def test_faces_before_the_selfie_join_the_person_afterwards(world: World, visitor: Visitor) -> None:
    uploader = Visitor(world.http())
    await uploader.enrolled("Ben", person=2)
    for salt in (30, 31):
        await uploader.upload(_picture(9, salt=salt))
    await uploader.upload(_picture(8, salt=32))  # someone else, seen once
    await _sort(world)
    people = await uploader.people()
    unnamed = [p for p in people["people"] if not p["name"]]
    assert len(unnamed) == 1 and unnamed[0]["photos"] == 2  # one sighting is not listed yet
    assert unnamed[0]["avatar_url"]

    person = await visitor.enrolled("Chitra", person=9)
    await _sort(world)
    page = await visitor.page(person)
    assert page["person"]["photos"] == 2
    assert page["person"]["name"] == "Chitra"
    people = await uploader.people()
    assert not [p for p in people["people"] if not p["name"]]
    assert (
        await world.factory.fetchval("SELECT rematch_pending FROM gallery_people WHERE id = :p", p=person)
        is False
    )


async def test_one_picture_cannot_show_a_person_twice(world: World, visitor: Visitor) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    await visitor.upload(_picture(1, second=1, salt=40))
    await _sort(world)
    assert (await visitor.page(person))["person"]["photos"] == 1
    people = await visitor.http.get(f"{ADMIN}/people")  # not an organiser: refused
    assert people.status_code == 401
    assert (
        await world.factory.fetchval(
            "SELECT count(DISTINCT person_id) FROM gallery_faces f JOIN gallery_media m ON m.id = f.media_id "
            "WHERE m.programme_id = :p",
            p=world.programme_id,
        )
        == 2
    )


@pytest.mark.parametrize(
    ("answer", "on_the_page"),
    [(True, True), (False, False), (None, False)],
    ids=["model says yes", "model says no", "model does not answer"],
)
async def test_a_close_call_goes_to_the_model(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch, answer: bool | None, on_the_page: bool
) -> None:
    asked: list[bytes] = []

    async def model(first: bytes, second: bytes) -> bool | None:
        asked.extend((first, second))
        return answer

    monkeypatch.setattr(gallery_worker, "same_person", model)
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    await visitor.upload(_picture(1, clear=42, salt=50))  # cosine 0.42 with the selfie
    await _sort(world)
    assert asked == [b"aligned-1", b"aligned-1"]
    assert ((await visitor.page(person))["person"]["photos"] == 1) is on_the_page
    how = await world.factory.fetchval(
        "SELECT matched_by FROM gallery_faces f JOIN gallery_media m ON m.id = f.media_id WHERE m.programme_id = :p",
        p=world.programme_id,
    )
    assert how == ("model" if on_the_page else "new")


async def test_a_clear_match_and_a_clear_stranger_never_ask_the_model(
    world: World, visitor: Visitor, no_model: list[Any]
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    await visitor.upload(_picture(1, clear=70, salt=60))
    await visitor.upload(_picture(1, clear=20, salt=61))
    await _sort(world)
    assert no_model == []
    assert (await visitor.page(person))["person"]["photos"] == 1


async def test_the_model_decides_for_a_selfie_and_an_earlier_group(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def yes(first: bytes, second: bytes) -> bool | None:
        return True

    monkeypatch.setattr(gallery_worker, "same_person", yes)
    uploader = Visitor(world.http())
    await uploader.enrolled("Ben", person=2)
    await uploader.upload(_picture(1, clear=45, salt=70))
    await _sort(world)
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    assert (await visitor.page(person))["person"]["photos"] == 1


def test_the_rules_for_scores() -> None:
    assert gallery_match.verdict(0.5) == "match"
    assert gallery_match.verdict(0.49) == "ask"
    assert gallery_match.verdict(0.36) == "ask"
    assert gallery_match.verdict(0.35) == "no"
    # 0.286 was a wrong face the model called the same person in real testing: it is never asked.
    assert gallery_match.verdict(0.286) == "no"


def test_candidates_are_each_persons_closest_face_best_first() -> None:
    one, two, three = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    references = [
        gallery_match.Reference(one, _identity(1, 0.4), b"low"),
        gallery_match.Reference(one, _identity(1, 0.8), b"high"),
        gallery_match.Reference(two, _identity(1, 0.9), b"two"),
        gallery_match.Reference(three, _identity(5), b"three"),
    ]
    found = gallery_match.candidates(_identity(1), references)
    assert [(c.person_id, c.aligned) for c in found] == [(two, b"two"), (one, b"high")]
    assert gallery_match.candidates(_identity(1), []) == []


def test_the_same_face_across_video_frames_keeps_its_clearest_sighting() -> None:
    tracks: list[gallery_match.Track] = []
    gallery_match.group_frames(tracks, _identity(1), 0.4, 0, "first")
    gallery_match.group_frames(tracks, _identity(1, 0.9), 0.9, 1000, "clearer")
    gallery_match.group_frames(tracks, _identity(2), 0.5, 2000, "someone else")
    assert [(t.seen, t.frame_ms, t.payload) for t in tracks] == [
        (2, 1000, "clearer"),
        (1, 2000, "someone else"),
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"same_person": true, "confidence": 0.92}', True),
        ('{\n  "same_person": true\n, "confidence": 0.85\n}', False),  # a yes it is not sure of
        ('{"same_person": false, "confidence": 0.95}', False),
        ("Based on the facial structure, they look alike.", None),
        ('{"same_person": "yes", "confidence": 1}', None),
        ('{"confidence": 1}', None),
        ("", None),
    ],
)
def test_reading_the_models_answer(content: str, expected: bool | None) -> None:
    assert gallery_model.read_answer(content) is expected


async def test_an_unconfigured_model_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gallery_model_url", "")
    assert await gallery_model.same_person(b"a", b"b") is None


async def test_a_failing_model_answers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gallery_model_url", "http://127.0.0.1:9/v1")
    assert await gallery_model.same_person(b"a", b"b") is None


# ---- the people page --------------------------------------------------------------------------


async def test_search_finds_a_name_and_nothing_else(world: World, visitor: Visitor) -> None:
    await visitor.enrolled("Asha Rao", person=1)
    await Visitor(world.http()).enrolled("Ben 100%", person=2)
    await _sort(world)
    assert [p["name"] for p in (await visitor.people("asha"))["people"]] == ["Asha Rao"]
    assert [p["name"] for p in (await visitor.people("100%"))["people"]] == ["Ben 100%"]
    assert (await visitor.people("%"))["people"][0]["name"] == "Ben 100%"
    assert (await visitor.people("nobody"))["people"] == []
    assert_error(await visitor.http.get(f"{GALLERY}/people/{uuid.uuid4()}"), 404, "NOT_FOUND")


async def test_people_come_with_most_photos_first_and_me_before_all(world: World, visitor: Visitor) -> None:
    me = await visitor.enrolled("Asha", person=1)
    other = Visitor(world.http())
    busy = await other.enrolled("Ben", person=2)
    await _sort(world)
    await other.upload(_picture(2, salt=80))
    await other.upload(_picture(2, salt=81))
    await _sort(world)
    answer = await visitor.people()
    assert [p["id"] for p in answer["people"]] == [me, busy]
    assert answer["pending"] == 0
    from_ben = await other.people()
    assert [p["id"] for p in from_ben["people"]] == [busy, me]


async def test_a_file_in_the_queue_is_counted_but_not_shown(world: World, visitor: Visitor) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    await visitor.upload(_picture(1, salt=90))
    assert (await visitor.people())["pending"] == 1
    assert (await visitor.page(person))["media"] == []
    await _sort(world)
    assert (await visitor.people())["pending"] == 0
    assert len((await visitor.page(person))["media"]) == 1


# ---- removing yourself and moderation ------------------------------------------------------------


async def test_removing_yourself_forgets_your_face_and_keeps_the_photo(
    world: World, visitor: Visitor
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    other = Visitor(world.http())
    await other.enrolled("Ben", person=2)
    await _sort(world)
    upload = await other.upload(_picture(1, second=2, salt=100))
    await _sort(world)
    avatar = (await visitor.me())["person"]["avatar_url"].removeprefix("/media/")
    crops = list(
        await world.factory.fetch("SELECT crop_key FROM gallery_faces WHERE person_id = :p", p=person)
    )

    resp = await visitor.http.delete(f"{GALLERY}/me")
    assert resp.status_code == 204
    assert (await visitor.me())["person"] is None
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_people WHERE id = :p", p=person) == 0
    assert (
        await world.factory.fetchval("SELECT count(*) FROM gallery_faces WHERE person_id = :p", p=person) == 0
    )
    assert not (_root() / avatar).exists()
    assert not (_root() / crops[0].crop_key).exists()
    # The photo stays, for the other person in it.
    assert (
        await world.factory.fetchval("SELECT status FROM gallery_media WHERE id = :m", m=upload.json()["id"])
        == "ready"
    )


async def test_organisers_remove_a_file_and_everything_made_from_it(world: World, visitor: Visitor) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    upload = await visitor.upload(_picture(1, second=7, salt=110))
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=upload.json()["id"])
    faces = await world.factory.fetch(
        "SELECT crop_key, person_id FROM gallery_faces WHERE media_id = :m", m=row.id
    )
    assert len(faces) == 2

    admin = await world.admin()
    listed = await admin.get(f"{ORGANISER}/media")
    assert listed.json()["total"] == 1
    assert listed.json()["media"][0]["uploader"] == "Asha"
    assert listed.json()["media"][0]["uploader_ip"] == world.ip

    assert (await admin.delete(f"{ORGANISER}/media/{row.id}")).status_code == 204
    assert not (_root() / row.storage_key).exists()
    assert not (_root() / row.thumb_key).exists()
    assert all(not (_root() / face.crop_key).exists() for face in faces)
    assert (await visitor.page(person))["media"] == []
    # Someone with a selfie stays; a stranger seen only in that file does not.
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM gallery_people WHERE programme_id = :p", p=world.programme_id
        )
        == 1
    )
    actions = await world.factory.fetch("SELECT action FROM audit_log WHERE target_id = :t", t=str(row.id))
    assert [a.action for a in actions] == ["gallery.media_deleted"]


async def test_visitors_cannot_use_the_organisers_routes(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    assert (await visitor.http.get(ADMIN)).status_code == 401
    assert (await visitor.http.get(f"{ADMIN}/media")).status_code == 401
    assert (await visitor.http.delete(f"{ADMIN}/media/{uuid.uuid4()}")).status_code == 401
    assert (await visitor.http.post(f"{ADMIN}/email/send")).status_code == 401
    assert (await visitor.http.get(f"{ADMIN}/media/{uuid.uuid4()}/analysis")).status_code == 401
    assert (await visitor.http.post(f"{ADMIN}/media/{uuid.uuid4()}/analysis/describe")).status_code == 401


async def test_organisers_rename_and_merge_people(world: World, visitor: Visitor) -> None:
    uploader = Visitor(world.http())
    await uploader.enrolled("Ben", person=2)
    for salt, person in ((120, 5), (121, 5), (122, 6), (123, 6)):
        await uploader.upload(_picture(person, salt=salt))
    await _sort(world)
    admin = await world.admin()
    people = (await admin.get(f"{ORGANISER}/people")).json()["people"]
    first, second = [p["id"] for p in people if not p["name"]]
    assert (await admin.patch(f"{ORGANISER}/people/{first}", {"name": " Deepa "})).status_code == 200
    merged = await admin.post(f"{ORGANISER}/people/{second}/merge", {"into": first})
    assert merged.status_code == 200, merged.text
    page = await uploader.page(first)
    assert page["person"]["name"] == "Deepa" and page["person"]["photos"] == 4
    assert_error(await uploader.http.get(f"{GALLERY}/people/{second}"), 404, "NOT_FOUND")
    assert_error(
        await admin.post(f"{ORGANISER}/people/{first}/merge", {"into": first}), 422, "VALIDATION_ERROR"
    )


async def test_organisers_retry_a_file_only_when_it_failed_and_is_still_stored(
    world: World, visitor: Visitor
) -> None:
    await visitor.enrolled()
    upload = await visitor.upload(_picture(1, salt=130))
    media_id = upload.json()["id"]
    admin = await world.admin()
    assert_error(await admin.post(f"{ORGANISER}/media/{media_id}/retry"), 409, "CONFLICT")
    await world.factory.execute(
        "UPDATE gallery_media SET status = 'failed', attempts = 3 WHERE id = :m", m=media_id
    )
    assert (await admin.post(f"{ORGANISER}/media/{media_id}/retry")).status_code == 200
    await _sort(world)
    assert (
        await world.factory.fetchval("SELECT status FROM gallery_media WHERE id = :m", m=media_id) == "ready"
    )


# ---- what the analysis saw --------------------------------------------------------------------------


async def test_organisers_see_each_face_with_its_box_and_how_it_was_decided(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def model(first: bytes, second: bytes) -> bool | None:
        return second == b"aligned-1"  # yes for this close face, no for the other

    monkeypatch.setattr(gallery_worker, "same_person", model)
    me = await visitor.enrolled("Asha", person=1)
    await Visitor(world.http()).enrolled("Ben", person=2)
    await _sort(world)
    upload = await visitor.upload(_picture(1, clear=42, second=2, salt=150), "stage.png")
    await _sort(world)

    admin = await world.admin()
    view = (await admin.get(f"{ORGANISER}/media/{upload.json()['id']}/analysis")).json()
    assert view["read"] is True and view["media"]["name"] == "stage.png"
    assert view["frames_read"] == 1 and view["faces_seen"] == 2
    assert view["model_questions"] == 1 and view["took_ms"] >= 0
    assert view["rules"] == {"match_at": 0.5, "ask_at": 0.36, "model_confidence": 0.9}
    assert [face["number"] for face in view["faces"]] == [1, 2]
    by_name = {face["name"]: face for face in view["faces"]}
    assert by_name["Asha"]["person_id"] == me
    assert by_name["Asha"]["matched_by"] == "model"
    assert by_name["Asha"]["checked"] == [{"name": "Asha", "listed": True, "score": 0.42, "model": "yes"}]
    assert by_name["Ben"]["matched_by"] == "opencv"
    assert by_name["Ben"]["checked"][0]["model"] == "not_asked"
    for face in view["faces"]:
        assert face["box"] == [0.1, 0.2, 0.3, 0.4] and face["detector_score"] == 0.9
        assert face["crop_url"].endswith(".webp")
    # Visitors get the file and nothing about the analysis.
    item = (await visitor.page(me))["media"][0]
    assert "box" not in str(item) and "analysis" not in item and "faces" not in item
    assert_error(await admin.get(f"{ORGANISER}/media/{uuid.uuid4()}/analysis"), 404, "NOT_FOUND")


async def test_a_refusal_and_a_silent_model_are_on_record(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers: list[bool | None] = [False, None]

    async def model(first: bytes, second: bytes) -> bool | None:
        return answers.pop(0)

    monkeypatch.setattr(gallery_worker, "same_person", model)
    await visitor.enrolled("Asha", person=1)
    await _sort(world)
    first = await visitor.upload(_picture(1, clear=41, salt=160))
    second = await visitor.upload(_picture(1, clear=43, salt=161))
    await _sort(world)
    admin = await world.admin()
    seen = []
    for upload in (first, second):
        view = (await admin.get(f"{ORGANISER}/media/{upload.json()['id']}/analysis")).json()
        assert view["faces"][0]["matched_by"] == "new"
        seen.append(view["faces"][0]["checked"][0]["model"])
    assert sorted(seen) == ["no", "no_answer"]


async def test_the_model_describes_a_file_once_when_an_organiser_asks(
    world: World, visitor: Visitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    await _sort(world)
    upload = await visitor.upload(_picture(1, salt=170))
    media_id = upload.json()["id"]
    await _sort(world)
    admin = await world.admin()
    path = f"{ORGANISER}/media/{media_id}/analysis/describe"

    monkeypatch.setattr(get_settings(), "gallery_model_url", "")
    assert_error(await admin.post(path), 409, "MODEL_NOT_SET")
    monkeypatch.setattr(get_settings(), "gallery_model_url", "http://model.test/v1")

    calls: list[list[bytes]] = []
    answers: list[dict[str, object] | None] = [None, {"summary": "People on a stage.", "people": 3}]

    async def describe(pictures: list[bytes]) -> dict[str, object] | None:
        calls.append(pictures)
        return answers.pop(0)

    monkeypatch.setattr(gallery, "describe", describe)
    assert_error(await admin.post(path), 503, "MODEL_UNAVAILABLE")
    described = await admin.post(path)
    assert described.status_code == 200, described.text
    assert described.json() == {"summary": "People on a stage.", "people": 3}
    assert (await admin.post(path)).json() == described.json()  # kept: the model is not asked again
    assert len(calls) == 2
    assert calls[0][0].startswith(b"\xff\xd8\xff")  # a small JPEG copy
    assert (await admin.get(f"{ORGANISER}/media/{media_id}/analysis")).json()[
        "description"
    ] == described.json()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"summary": " People  on stage. ", "people": 4}', {"summary": "People on stage.", "people": 4}),
        ('{"summary": "x", "people": -2}', {"summary": "x", "people": 0}),
        ('{"summary": "", "people": 1}', None),
        ('{"people": 1}', None),
        ("A group of people.", None),
    ],
)
def test_reading_the_models_description(content: str, expected: dict[str, object] | None) -> None:
    assert gallery_model.read_description(content) == expected


async def test_a_file_read_before_the_analysis_view_is_filled_in_without_changing_its_matches(
    world: World, visitor: Visitor
) -> None:
    me = await visitor.enrolled("Asha", person=1)
    await _sort(world)
    upload = await visitor.upload(_picture(1, second=7, salt=180))
    media_id = upload.json()["id"]
    await _sort(world)
    before = await world.factory.fetch(
        "SELECT id, person_id, matched_by FROM gallery_faces WHERE media_id = :m", m=media_id
    )
    assert len(before) == 2
    await world.factory.execute("UPDATE gallery_media SET analysis = '{}'::jsonb WHERE id = :m", m=media_id)
    await world.factory.execute(
        "UPDATE gallery_faces SET analysis = '{}'::jsonb WHERE media_id = :m", m=media_id
    )
    admin = await world.admin()
    path = f"{ORGANISER}/media/{media_id}/analysis"
    waiting = (await admin.get(path)).json()
    assert waiting["read"] is False and all(face["box"] is None for face in waiting["faces"])

    await _sort(world)
    view = (await admin.get(path)).json()
    assert view["read"] is True and view["faces_seen"] == 2
    assert all(face["box"] == [0.1, 0.2, 0.3, 0.4] for face in view["faces"])
    assert all(face["checked"] is None for face in view["faces"])  # that record was never kept
    after = await world.factory.fetch(
        "SELECT id, person_id, matched_by FROM gallery_faces WHERE media_id = :m", m=media_id
    )
    assert sorted(map(tuple, after)) == sorted(map(tuple, before))
    assert (await visitor.page(me))["person"]["photos"] == 1


async def test_a_file_that_cannot_be_read_again_is_not_tried_forever(world: World, visitor: Visitor) -> None:
    await visitor.enrolled()
    await _sort(world)
    upload = await visitor.upload(_picture(1, salt=190))
    media_id = upload.json()["id"]
    await _sort(world)
    public = await world.factory.fetchval("SELECT display_key FROM gallery_media WHERE id = :m", m=media_id)
    (_root() / public).write_bytes(b"no longer a picture")
    await world.factory.execute("UPDATE gallery_media SET analysis = '{}'::jsonb WHERE id = :m", m=media_id)
    await _sort(world)
    assert (
        await world.factory.fetchval("SELECT analysis::text FROM gallery_media WHERE id = :m", m=media_id)
        == '{"read_again": false}'
    )


# ---- the email ------------------------------------------------------------------------------------


async def _display_name(world: World, participant_id: uuid.UUID) -> str:
    return str(
        await world.factory.fetchval("SELECT display_name FROM participants WHERE id = :p", p=participant_id)
    )


async def test_the_link_in_the_email_says_who_it_is_for(world: World) -> None:
    info = await world.factory.create_participant(world.programme_id, activated=True)
    token = gallery.link_token(info.id)
    assert gallery.participant_of_token(token) == info.id
    assert gallery.participant_of_token("") is None
    assert gallery.participant_of_token("example") is None
    assert gallery.participant_of_token(token[:-2] + ("aa" if token[-2:] != "aa" else "bb")) is None
    forged = f"{gallery._encode(uuid.uuid4().bytes)}.{token.split('.')[1]}"
    assert gallery.participant_of_token(forged) is None


async def test_the_form_from_the_email_ties_the_person_and_follows_to_a_second_device(world: World) -> None:
    info = await world.factory.create_participant(world.programme_id, activated=True)
    link_before = await world.factory.fetchval(
        "SELECT link_token_hash FROM participants WHERE id = :p", p=info.id
    )
    token = gallery.link_token(info.id)
    laptop = Visitor(world.http())

    known = await laptop.http.post(f"{GALLERY}/me", json={"token": token})
    assert known.status_code == 200 and known.json()["person"] is None
    assert known.json()["suggested_name"] == await _display_name(world, info.id)
    assert known.json()["known"] is True

    resp = await laptop.enrol("Asha Rao", _picture(1), token=token)
    assert resp.status_code == 200, resp.text
    person = resp.json()["person"]["id"]
    assert (
        await world.factory.fetchval("SELECT participant_id FROM gallery_people WHERE id = :p", p=person)
        == info.id
    )

    phone = Visitor(world.http())
    again = await phone.http.post(f"{GALLERY}/me", json={"token": token})
    assert again.json()["person"]["id"] == person  # no form on the second device
    assert (await phone.me())["person"]["id"] == person
    # The voter's own sign-in link has nothing to do with this.
    assert (
        await world.factory.fetchval("SELECT link_token_hash FROM participants WHERE id = :p", p=info.id)
        == link_before
    )


async def test_someone_with_no_display_name_gets_the_name_their_email_gives(world: World) -> None:
    info = await world.factory.create_participant(
        world.programme_id, activated=True, email="asha.rao@example.com", display_name=""
    )
    known = await Visitor(world.http()).http.post(
        f"{GALLERY}/me", json={"token": gallery.link_token(info.id)}
    )
    assert known.json()["suggested_name"] == name_from_email("asha.rao@example.com")
    assert known.json()["suggested_name"]


async def test_a_signed_in_voter_is_known_and_a_forged_token_is_not(world: World) -> None:
    voter = await world.participant()
    known = await voter.http.get(f"{GALLERY}/me")
    assert known.json()["known"] is True
    assert known.json()["suggested_name"] == await _display_name(world, voter.info.id)
    stranger = Visitor(world.http())
    forged = await stranger.http.post(f"{GALLERY}/me", json={"token": "abc.def"})
    assert forged.status_code == 200
    assert forged.json()["known"] is False and forged.json()["suggested_name"] == ""


async def test_the_email_goes_to_each_person_once_with_their_own_link(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    first = await world.factory.create_participant(
        world.programme_id, activated=True, email="first@example.com"
    )
    second = await world.factory.create_participant(
        world.programme_id, activated=True, email="second@example.com"
    )
    disabled = await world.factory.create_participant(
        world.programme_id, activated=True, email="off@example.com"
    )
    await world.factory.execute("UPDATE participants SET disabled_at = now() WHERE id = :p", p=disabled.id)
    admin = await world.admin()
    summary = (await admin.get(ORGANISER)).json()
    assert summary["email"]["to_send"] == 2
    assert summary["share_url"] == "http://testserver/gallery"

    sent = await admin.post(f"{ORGANISER}/email/send")
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"queued": 2, "already": 0, "reserved": 0}
    again = await admin.post(f"{ORGANISER}/email/send")
    assert again.json() == {"queued": 0, "already": 2, "reserved": 0}

    await worker.email_tick()
    await drain(worker)
    assert sorted(m.to for m in provider.sent) == ["first@example.com", "second@example.com"]
    for message in provider.sent:
        participant = first if message.to.startswith("first") else second
        link = f"http://testserver/gallery#token={gallery.link_token(participant.id)}"
        assert link in message.text and link in message.html
        assert "http://testserver/gallery\n" in message.text or "http://testserver/gallery " in message.text
        assert "Open the gallery" in message.html
        assert "/enter#token=" not in message.html  # never the voter's sign-in link
    assert (await admin.get(ORGANISER)).json()["email"] == {"to_send": 0, "already": 2, "reserved": 0}


async def test_a_real_provider_is_never_given_reserved_addresses(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world.factory.create_participant(world.programme_id, activated=True, email="guest@example.com")
    real = await world.factory.create_participant(
        world.programme_id, activated=True, email="real@example.com"
    )
    await world.factory.execute(
        "UPDATE participants SET email = 'real@techsarasolutions.com', "
        "email_normalized = 'real@techsarasolutions.com' WHERE id = :p",
        p=real.id,
    )
    monkeypatch.setattr(get_settings(), "email_provider", "smtp")
    admin = await world.admin()
    sent = await admin.post(f"{ORGANISER}/email/send")
    assert sent.json() == {"queued": 1, "already": 0, "reserved": 1}
    recipients = await world.factory.fetch(
        "SELECT recipient FROM email_jobs WHERE programme_id = :p AND kind = 'gallery'", p=world.programme_id
    )
    assert [r.recipient for r in recipients] == ["real@techsarasolutions.com"]


async def test_the_gallery_email_has_its_own_wording_and_preview(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    admin = await world.admin()
    path = "/email/templates/gallery"
    loaded = (await admin.get(path)).json()
    assert loaded["customised"] is False and "Thank you" in loaded["wording"]["heading"]
    wording = {**loaded["wording"], "heading": "Photos from {programme}"}
    saved = await admin.put(path, wording)
    assert saved.status_code == 200, saved.text
    assert saved.json()["customised"] is True
    invitation = (await admin.get("/email/templates/invitation")).json()
    assert invitation["customised"] is False  # the invitation is untouched

    preview = await admin.post(f"{path}/preview", wording)
    assert preview.status_code == 200, preview.text
    assert "Photos from" in preview.json()["html"]
    assert "Asha Rao" in preview.json()["text"]

    test = await admin.post("/email/test", {"to": "organiser@example.com", "template": "gallery"})
    assert test.status_code == 200, test.text
    await worker.email_tick()
    await drain(worker)
    message = provider.sent[-1]
    assert "Photos from" in message.html and "[Preview]" in message.subject
    assert "http://testserver/gallery" in message.text
    assert "#token=" not in message.text


# ---- the real OpenCV models and ffmpeg --------------------------------------------------------------


def _models_folder() -> str | None:
    folder = os.environ.get("GALLERY_TEST_MODELS_DIR", "")
    return folder if folder and Path(folder).is_dir() else None


real_models = pytest.mark.skipif(
    _models_folder() is None, reason="set GALLERY_TEST_MODELS_DIR to the ONNX folder"
)


@pytest.mark.real_opencv
@real_models
async def test_real_opencv_finds_a_face_and_matches_two_framings_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "gallery_models_dir", _models_folder())
    from stagevote.services.gallery_faces import find_faces

    def faces_of(name: str) -> list[Face]:
        with Image.open(FIXTURES / name) as image:
            return find_faces(np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    selfie = faces_of("portrait-selfie.jpg")
    wide = faces_of("portrait-wide.jpg")
    assert len(selfie) == 1 and len(wide) == 1
    assert float(selfie[0].embedding @ wide[0].embedding) >= gallery_match.MATCH_AT
    assert abs(float(np.linalg.norm(selfie[0].embedding)) - 1) < 1e-3
    assert selfie[0].aligned.startswith(b"\xff\xd8\xff")
    assert selfie[0].crop[:4] == b"RIFF"
    blank = np.zeros((400, 400, 3), dtype=np.uint8)
    assert find_faces(blank) == []


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")


def _make_video(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=5:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", *arguments, str(path)],
        check=True,
    )  # fmt: skip


def _turn_video(source: Path, target: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(source), "-c", "copy", str(target)],
        check=True,
    )  # fmt: skip


@needs_ffmpeg
async def test_a_video_browsers_play_is_kept_without_its_metadata(
    world: World, visitor: Visitor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mov"
    _make_video(
        clip, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata", "location=+19.0760+072.8777/", "-metadata", "title=secret",
    )  # fmt: skip
    await visitor.enrolled()
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [])
    resp = await visitor.upload(clip.read_bytes(), "clip.mov", "video/quicktime")
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "ready", row.last_error
    assert row.storage_key.endswith(".mp4") and row.storage_key == row.display_key
    assert (row.width, row.height) == (320, 240)
    assert 2500 <= row.duration_ms <= 3500
    public = _root() / row.storage_key
    info = gallery_files.probe(public)
    assert "19.0760" in str(gallery_files.probe(clip))  # the phone's location was there to begin with
    assert "19.0760" not in str(info) and "secret" not in str(info)
    assert {s["codec_name"] for s in info["streams"]} >= {"h264", "aac"}  # type: ignore[union-attr]
    assert public.read_bytes().find(b"moov") < public.read_bytes().find(b"mdat")  # plays while loading


@needs_ffmpeg
async def test_a_video_browsers_cannot_play_gets_a_copy_they_can(
    world: World, visitor: Visitor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mkv"
    _make_video(clip, "-c:v", "mpeg4", "-c:a", "mp2")
    await visitor.enrolled()
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [])
    resp = await visitor.upload(clip.read_bytes(), "old-camera.mkv", "video/x-matroska")
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "ready", row.last_error
    display = gallery_files.probe(_root() / row.display_key)
    assert {s["codec_name"] for s in display["streams"]} == {"h264", "aac"}  # type: ignore[union-attr]
    assert row.thumb_key.endswith(".webp")


@needs_ffmpeg
async def test_a_turned_phone_video_keeps_its_turn(
    world: World, visitor: Visitor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain.mp4"
    _make_video(plain, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    turned = tmp_path / "turned.mp4"
    _turn_video(plain, turned)
    await visitor.enrolled()
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [])
    resp = await visitor.upload(turned.read_bytes(), "phone.mp4", "video/mp4")
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "ready", row.last_error
    assert (row.width, row.height) == (240, 320)
    assert "rotation" in str(gallery_files.probe(_root() / row.storage_key)).lower()


async def test_something_that_is_not_a_video_is_refused(world: World, visitor: Visitor) -> None:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed here")
    await visitor.enrolled()
    fake = b"\x00\x00\x00\x18ftypisom" + bytes(4000)
    resp = await visitor.upload(fake, "fake.mp4", "video/mp4")
    await _sort(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=resp.json()["id"])
    assert row.status == "failed" and row.storage_key is None


def _portrait_video(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-loop", "1", "-t", "4", "-i", str(FIXTURES / "portrait-wide.jpg"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "5", str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.real_opencv
@real_models
@needs_ffmpeg
async def test_real_opencv_finds_a_face_in_a_real_video_and_counts_it_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "gallery_models_dir", _models_folder())
    clip = tmp_path / "clip.mp4"
    _portrait_video(clip)
    prepared = await asyncio.to_thread(gallery_files.prepare_video, clip)
    assert prepared.display_key == prepared.storage_key
    assert len(prepared.faces) == 1  # the same face in every frame is one sighting
    assert prepared.frames_read == 4 and prepared.faces_seen >= 4
    assert prepared.frames and all((_root() / key).exists() for _, key in prepared.frames)
    box = prepared.faces[0][0].box
    assert 0 < box[2] < 1 and 0 < box[3] < 1

    from stagevote.services.gallery_faces import find_faces

    with Image.open(FIXTURES / "portrait-selfie.jpg") as image:
        selfie = find_faces(np.asarray(image.convert("RGB"))[:, :, ::-1].copy())[0]
    face, moment = prepared.faces[0]
    assert float(selfie.embedding @ face.embedding) >= gallery_match.MATCH_AT
    assert moment is not None
