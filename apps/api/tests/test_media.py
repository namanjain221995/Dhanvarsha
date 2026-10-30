"""Chunked uploads of the image or video an opening-page card shows full screen.

The upload is started with its name and size, sent in chunks at explicit offsets (so a
retried chunk can never be stored twice) and completed. The file's own first bytes decide
what it is: images are re-encoded to WebP, videos are stored byte for byte.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from helpers import ADMIN_API, AdminClient, World, assert_error
from stagevote.config import get_settings
from stagevote.services import media

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 40
# EBML header with the DocType "webm", then a little filler.
WEBM = (
    b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08"
    b"\x42\x82\x84webm\x42\x87\x81\x04\x42\x85\x81\x02" + b"\x18\x53\x80\x67" + bytes(4000)
)
INVALID = "Upload a JPEG, PNG, WebP or GIF image, or an MP4 or WebM video."


def _root() -> Path:
    return Path(get_settings().media_root)


def _temporary(upload_id: str) -> list[str]:
    directory = _root() / media.UPLOAD_DIR
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(upload_id))


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (212, 175, 55)).save(buf, format="PNG")
    return buf.getvalue()


async def _start(admin: AdminClient, data: bytes, content_type: str = "video/mp4") -> str:
    resp = await admin.start_media("clip.mp4", len(data), content_type)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["upload_id"])


async def test_a_video_is_uploaded_in_chunks_and_stored_byte_for_byte(world: World) -> None:
    admin = await world.admin()
    resp = await admin.start_media("Opening night.mp4", len(MP4), "video/mp4")
    assert resp.status_code == 201, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    started = resp.json()
    assert set(started) == {"upload_id", "chunk_size"}
    assert re.fullmatch(r"[a-f0-9]{32}", started["upload_id"])
    assert started["chunk_size"] == 8 * 1024 * 1024
    upload_id = started["upload_id"]
    assert _temporary(upload_id) == [f"{upload_id}.json", f"{upload_id}.part"]

    for offset in range(0, len(MP4), 4000):
        sent = await admin.media_chunk(upload_id, offset, MP4[offset : offset + 4000])
        assert sent.status_code == 200, sent.text
        assert sent.headers["cache-control"].startswith("no-store")
        assert sent.json() == {"received": min(offset + 4000, len(MP4))}

    resp = await admin.post(f"/media/uploads/{upload_id}/complete")
    assert resp.status_code == 201, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    done: dict[str, Any] = resp.json()
    assert set(done) == {"id", "url", "type", "name", "byte_size", "width", "height"}
    assert re.fullmatch(r"/media/[a-f0-9]{32}\.mp4", done["url"])
    assert (done["type"], done["name"], done["byte_size"], done["width"], done["height"]) == (
        "video",
        "Opening night.mp4",
        len(MP4),
        0,
        0,
    )
    assert (_root() / done["url"].removeprefix("/media/")).read_bytes() == MP4
    assert _temporary(upload_id) == []

    row = await world.factory.fetchrow(
        "SELECT kind, content_type, original_name, byte_size, width, height, sha256 FROM assets WHERE id = :i",
        i=done["id"],
    )
    assert (row.kind, row.content_type, row.original_name, row.byte_size) == (
        "opening_media",
        "video/mp4",
        "Opening night.mp4",
        len(MP4),
    )
    assert (row.width, row.height) == (0, 0) and len(row.sha256) == 64
    entry = await world.factory.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'asset.uploaded' AND target_id = :i", i=done["id"]
    )
    assert entry.details == {"kind": "opening_media", "type": "video", "bytes": len(MP4)}

    # The upload is over: its id no longer takes chunks or completes again.
    assert_error(await admin.media_chunk(upload_id, len(MP4), b""), 404, "NOT_FOUND")
    assert_error(await admin.post(f"/media/uploads/{upload_id}/complete"), 404, "NOT_FOUND")


async def test_a_webm_video_keeps_its_type(world: World) -> None:
    admin = await world.admin()
    resp = await admin.upload_media(WEBM, "loop.webm", "video/webm", chunk=1000)
    assert resp.status_code == 201, resp.text
    done = resp.json()
    assert re.fullmatch(r"/media/[a-f0-9]{32}\.webm", done["url"]) and done["type"] == "video"
    assert (_root() / done["url"].removeprefix("/media/")).read_bytes() == WEBM
    content_type = await world.factory.fetchval("SELECT content_type FROM assets WHERE id = :i", i=done["id"])
    assert content_type == "video/webm"

    # The browser's word does not matter: an MP4 sent as "image/png" is still a video.
    resp = await admin.upload_media(MP4, "renamed.png", "image/png")
    assert resp.status_code == 201, resp.text
    assert resp.json()["url"].endswith(".mp4")


async def test_an_image_becomes_webp_of_at_most_3840_pixels(world: World) -> None:
    admin = await world.admin()
    resp = await admin.upload_media(_png(4000, 2000), "stage.png", "image/png", chunk=3000)
    assert resp.status_code == 201, resp.text
    done = resp.json()
    assert re.fullmatch(r"/media/[a-f0-9]{32}\.webp", done["url"])
    assert (done["type"], done["name"], done["width"], done["height"]) == ("image", "stage.png", 3840, 1920)
    stored = (_root() / done["url"].removeprefix("/media/")).read_bytes()
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WEBP" and done["byte_size"] == len(stored)
    with Image.open(io.BytesIO(stored)) as img:
        assert (img.format, img.size) == ("WEBP", (3840, 1920))
    row = await world.factory.fetchrow(
        "SELECT kind, content_type, original_name FROM assets WHERE id = :i", i=done["id"]
    )
    assert tuple(row) == ("opening_media", "image/webp", "stage.png")
    entry = await world.factory.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'asset.uploaded' AND target_id = :i", i=done["id"]
    )
    assert entry.details == {"kind": "opening_media", "type": "image", "bytes": len(stored)}

    # A small image keeps its size.
    resp = await admin.upload_media(_png(320, 200), "small.png", "image/png")
    assert resp.status_code == 201, resp.text
    assert (resp.json()["width"], resp.json()["height"]) == (320, 200)


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"<!doctype html><html><script>alert(document.cookie)</script></html>", "text/html"),
        (b"<!doctype html><script>alert(1)</script>" + bytes(100), "video/mp4"),
        (b"Just some notes.\n" * 20, "text/plain"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
        (b"\x00\x00\x00\x18ftypheic" + bytes(100), "image/heic"),  # a still image, not a video
        (b"\x1a\x45\xdf\xa3\x9f\x42\x82\x88matroska" + bytes(100), "video/x-matroska"),
        (b"\x89PNG\r\n\x1a\n" + b"broken" * 20, "image/png"),  # looks like a PNG, does not decode
    ],
)
async def test_other_files_are_refused(world: World, data: bytes, content_type: str) -> None:
    admin = await world.admin()
    upload_id = await _start(admin, data, content_type)
    assert (await admin.media_chunk(upload_id, 0, data)).status_code == 200
    before = await world.factory.fetchval("SELECT count(*) FROM assets")
    error = assert_error(await admin.post(f"/media/uploads/{upload_id}/complete"), 422, "INVALID_MEDIA")
    assert error["message"] == INVALID
    # Sending the same file again cannot change the answer, so nothing of it is kept.
    assert _temporary(upload_id) == []
    assert await world.factory.fetchval("SELECT count(*) FROM assets") == before


async def test_chunks_must_follow_on_and_the_browser_resumes_from_received(world: World) -> None:
    admin = await world.admin()
    upload_id = await _start(admin, MP4)
    assert (await admin.media_chunk(upload_id, 0, MP4[:1000])).json() == {"received": 1000}

    # The same chunk again (a retry after a lost answer), a gap, and a negative offset.
    for offset in (0, 500, 1500):
        error = assert_error(
            await admin.media_chunk(upload_id, offset, MP4[offset : offset + 1000]),
            409,
            "UPLOAD_OFFSET_MISMATCH",
        )
        assert error["details"] == {"received": 1000}
    resp = await admin.media_chunk(upload_id, -1, b"x")
    assert_error(resp, 422, "VALIDATION_FAILED")
    resp = await admin.http.put(
        f"{ADMIN_API}/media/uploads/{upload_id}", content=b"x", headers={"X-CSRF-Token": admin.csrf}
    )
    assert_error(resp, 422, "VALIDATION_FAILED")  # the offset is required

    # Completing early is refused and keeps what arrived.
    error = assert_error(await admin.post(f"/media/uploads/{upload_id}/complete"), 409, "UPLOAD_INCOMPLETE")
    assert error["details"] == {"received": 1000, "size": len(MP4)}

    # An empty chunk at the right offset reports the stored size; then the rest follows.
    assert (await admin.media_chunk(upload_id, 1000, b"")).json() == {"received": 1000}
    assert (await admin.media_chunk(upload_id, 1000, MP4[1000:])).json() == {"received": len(MP4)}
    resp = await admin.post(f"/media/uploads/{upload_id}/complete")
    assert resp.status_code == 201, resp.text
    assert (_root() / resp.json()["url"].removeprefix("/media/")).read_bytes() == MP4


async def test_parallel_requests_for_one_upload_take_turns(world: World) -> None:
    """The same chunk sent twice at once (a retry racing the original) is stored once, and
    an upload completed twice at once becomes one asset."""
    admin = await world.admin()
    upload_id = await _start(admin, MP4)
    half = len(MP4) // 2
    first, second = await asyncio.gather(
        admin.media_chunk(upload_id, 0, MP4[:half]), admin.media_chunk(upload_id, 0, MP4[:half])
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    refused = first if first.status_code == 409 else second
    assert refused.json()["error"]["details"] == {"received": half}
    assert (await admin.media_chunk(upload_id, half, MP4[half:])).json() == {"received": len(MP4)}

    before = await world.factory.fetchval("SELECT count(*) FROM assets WHERE kind = 'opening_media'")
    done, again = await asyncio.gather(
        admin.post(f"/media/uploads/{upload_id}/complete"), admin.post(f"/media/uploads/{upload_id}/complete")
    )
    assert sorted([done.status_code, again.status_code]) == [201, 404]
    winner = done if done.status_code == 201 else again
    assert (_root() / winner.json()["url"].removeprefix("/media/")).read_bytes() == MP4
    after = await world.factory.fetchval("SELECT count(*) FROM assets WHERE kind = 'opening_media'")
    assert after == before + 1


async def test_no_byte_past_the_declared_size_is_stored(world: World) -> None:
    admin = await world.admin()
    upload_id = await _start(admin, MP4[:100])
    error = assert_error(await admin.media_chunk(upload_id, 0, MP4[:101]), 413, "UPLOAD_SIZE_EXCEEDED")
    assert error["details"] == {"received": 0}
    assert (await admin.media_chunk(upload_id, 0, MP4[:60])).json() == {"received": 60}
    assert_error(await admin.media_chunk(upload_id, 60, MP4[60:120]), 413, "UPLOAD_SIZE_EXCEEDED")
    assert (await admin.media_chunk(upload_id, 60, MP4[60:100])).json() == {"received": 100}


async def test_a_full_chunk_passes_and_a_larger_one_is_refused(world: World) -> None:
    admin = await world.admin()
    size = media.CHUNK_SIZE + 64
    data = MP4[:64] + bytes(size - 64)
    upload_id = await _start(admin, data)
    # Over the API's usual 2 MB request limit, and exactly one chunk.
    sent = await admin.media_chunk(upload_id, 0, data[: media.CHUNK_SIZE])
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"received": media.CHUNK_SIZE}

    # One byte over the chunk size is refused by the endpoint, far over it before the body is read.
    other = await _start(admin, data)
    assert_error(await admin.media_chunk(other, 0, data[: media.CHUNK_SIZE + 1]), 413, "PAYLOAD_TOO_LARGE")
    assert_error(
        await admin.media_chunk(other, 0, bytes(media.CHUNK_SIZE + 200_000)), 413, "PAYLOAD_TOO_LARGE"
    )
    assert (await admin.media_chunk(other, 0, b"")).json() == {"received": 0}
    # The larger limit is only for chunks: other admin requests keep the JSON limit.
    resp = await admin.http.post(
        f"{ADMIN_API}/media/uploads",
        content=b"{" + b" " * 2_100_000 + b"}",
        headers={"X-CSRF-Token": admin.csrf, "Content-Type": "application/json"},
    )
    assert_error(resp, 413, "PAYLOAD_TOO_LARGE")


async def test_files_over_the_limits_are_refused(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = await world.admin()
    settings = get_settings()
    error = assert_error(
        await admin.start_media("huge.mp4", settings.max_media_bytes + 1, "video/mp4"), 413, "FILE_TOO_LARGE"
    )
    assert error["message"] == "Videos must be 1024 MB or smaller."
    error = assert_error(
        await admin.start_media("huge.png", 40 * 1024 * 1024 + 1, "image/png"), 413, "FILE_TOO_LARGE"
    )
    assert error["message"] == "Images must be 40 MB or smaller."
    assert (await admin.start_media("exact.mp4", settings.max_media_bytes, "video/mp4")).status_code == 201
    for size in (0, -5, "lots"):
        resp = await admin.post(
            "/media/uploads", {"name": "x.mp4", "size": size, "content_type": "video/mp4"}
        )
        assert_error(resp, 422, "VALIDATION_FAILED")
    for name in ("", "   ", "n" * 256, "line\nbreak.mp4"):
        resp = await admin.post("/media/uploads", {"name": name, "size": 10, "content_type": "video/mp4"})
        assert_error(resp, 422, "VALIDATION_FAILED")

    # The image limit also holds when the browser called the image something else.
    png = _png(64, 64)
    monkeypatch.setattr(settings, "max_media_image_bytes", len(png) - 1)
    upload_id = await _start(admin, png, "video/mp4")
    assert (await admin.media_chunk(upload_id, 0, png)).status_code == 200
    error = assert_error(await admin.post(f"/media/uploads/{upload_id}/complete"), 413, "FILE_TOO_LARGE")
    assert "Images must be" in error["message"]
    assert _temporary(upload_id) == []


async def test_an_upload_needs_free_disk_space(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = await world.admin()
    reserve = media.FREE_SPACE_RESERVE
    monkeypatch.setattr(media, "_free_bytes", lambda _path: reserve + 999)
    error = assert_error(await admin.start_media("big.mp4", 1000, "video/mp4"), 507, "INSUFFICIENT_STORAGE")
    assert error["message"] == "The server does not have enough free disk space for this file."
    assert (await admin.start_media("fits.mp4", 999, "video/mp4")).status_code == 201


async def test_uploads_belong_to_the_organiser_who_started_them(world: World) -> None:
    owner = await world.admin()
    other = await world.admin(fresh=True)
    upload_id = await _start(owner, MP4)
    assert (await owner.media_chunk(upload_id, 0, MP4[:100])).json() == {"received": 100}

    assert_error(await other.media_chunk(upload_id, 100, MP4[100:200]), 404, "NOT_FOUND")
    assert_error(await other.post(f"/media/uploads/{upload_id}/complete"), 404, "NOT_FOUND")
    assert_error(await other.delete(f"/media/uploads/{upload_id}"), 404, "NOT_FOUND")
    # Unknown and malformed ids look the same.
    for unknown in ("0" * 32, "A" * 32, "not-an-upload", "0" * 31, "0" * 32 + ".part"):
        assert_error(await owner.media_chunk(unknown, 0, b"x"), 404, "NOT_FOUND")
        assert_error(await owner.post(f"/media/uploads/{unknown}/complete"), 404, "NOT_FOUND")
        assert_error(await owner.delete(f"/media/uploads/{unknown}"), 404, "NOT_FOUND")

    # The owner's upload is untouched.
    assert (await owner.media_chunk(upload_id, 100, MP4[100:])).json() == {"received": len(MP4)}
    assert (await owner.post(f"/media/uploads/{upload_id}/complete")).status_code == 201


async def test_an_upload_can_be_abandoned(world: World) -> None:
    admin = await world.admin()
    upload_id = await _start(admin, MP4)
    assert (await admin.media_chunk(upload_id, 0, MP4[:500])).status_code == 200
    resp = await admin.delete(f"/media/uploads/{upload_id}")
    assert resp.status_code == 204 and resp.content == b""
    assert resp.headers["cache-control"].startswith("no-store")
    assert _temporary(upload_id) == []
    assert_error(await admin.media_chunk(upload_id, 500, MP4[500:]), 404, "NOT_FOUND")
    assert_error(await admin.post(f"/media/uploads/{upload_id}/complete"), 404, "NOT_FOUND")
    assert_error(await admin.delete(f"/media/uploads/{upload_id}"), 404, "NOT_FOUND")


async def test_starting_an_upload_removes_ones_left_for_a_day(world: World) -> None:
    admin = await world.admin()
    old = await _start(admin, MP4)
    recent = await _start(admin, MP4)
    directory = _root() / media.UPLOAD_DIR
    day_ago = time.time() - 24 * 3600 - 60
    for name in _temporary(old):
        os.utime(directory / name, (day_ago, day_ago))
    # An upload still receiving chunks stays, however long ago it started.
    os.utime(directory / f"{recent}.json", (day_ago, day_ago))
    assert (await admin.media_chunk(recent, 0, MP4[:10])).status_code == 200

    await _start(admin, MP4)
    assert _temporary(old) == []
    assert _temporary(recent) == [f"{recent}.json", f"{recent}.part"]
    assert_error(await admin.media_chunk(old, 0, MP4[:10]), 404, "NOT_FOUND")


async def test_uploads_need_an_organiser_and_the_csrf_token(world: World) -> None:
    anonymous = world.http()
    body = {"name": "clip.mp4", "size": 10, "content_type": "video/mp4"}
    assert_error(await anonymous.post(f"{ADMIN_API}/media/uploads", json=body), 401, "UNAUTHENTICATED")
    assert_error(
        await anonymous.put(f"{ADMIN_API}/media/uploads/{'0' * 32}?offset=0", content=b"x"),
        401,
        "UNAUTHENTICATED",
    )

    admin = await world.admin()
    upload_id = await _start(admin, MP4[:10])
    bad = {"X-CSRF-Token": "wrong"}
    for resp in (
        await admin.http.post(f"{ADMIN_API}/media/uploads", json=body, headers=bad),
        await admin.http.post(f"{ADMIN_API}/media/uploads", json=body),
        await admin.http.put(
            f"{ADMIN_API}/media/uploads/{upload_id}?offset=0", content=MP4[:10], headers=bad
        ),
        await admin.http.put(f"{ADMIN_API}/media/uploads/{upload_id}?offset=0", content=MP4[:10]),
        await admin.http.post(f"{ADMIN_API}/media/uploads/{upload_id}/complete", headers=bad),
        await admin.http.delete(f"{ADMIN_API}/media/uploads/{upload_id}", headers=bad),
    ):
        assert_error(resp, 403, "CSRF_FAILED")
    # Nothing was written by the refused requests.
    assert (await admin.media_chunk(upload_id, 0, MP4[:10])).json() == {"received": 10}

    # A request from another site is refused before anything else.
    foreign = await admin.http.post(
        f"{ADMIN_API}/media/uploads",
        json=body,
        headers={"X-CSRF-Token": admin.csrf, "Origin": "https://evil.example"},
    )
    assert_error(foreign, 403, "BAD_ORIGIN")
