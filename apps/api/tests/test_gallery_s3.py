"""The gallery's files on S3: one real copy of each, and a folder per named person kept up to date.

moto stands in for S3, so every call goes through boto3 as it would for real and no test can reach a real
bucket (conftest.py also empties the bucket setting and the key for every other test).
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import boto3
import pytest
from moto import mock_aws

from helpers import AdminClient, World, assert_error
from stagevote.config import get_settings
from stagevote.services import gallery, gallery_files, gallery_folders, gallery_store
from stagevote.worker.gallery import GalleryWorker, wanted_concurrency
from test_gallery import (
    GALLERY,
    ORGANISER,
    XMP_PLACE,
    Visitor,
    _console_upload,
    _fake_faces,
    _picture,
    _png_with_xmp,
)

BUCKET = "gallery-test"
GALLERY_TABLES = (
    "gallery_s3_uploads",
    "gallery_faces",
    "gallery_person_files",
    "gallery_storage_trash",
    "gallery_media",
    "gallery_devices",
    "gallery_people",
)


def _root() -> Path:
    return Path(get_settings().media_root)


@pytest.fixture
async def clean(world: World) -> AsyncIterator[None]:
    """Earlier tests left local files behind, which the worker would move to this bucket."""
    for table in GALLERY_TABLES:
        await world.factory.execute(f"DELETE FROM {table}")
    yield


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch, clean: None) -> Iterator[Any]:
    monkeypatch.setattr(gallery_files, "find_faces", _fake_faces)
    with mock_aws():
        gallery_store.reset_client()
        monkeypatch.setattr(get_settings(), "gallery_s3_bucket", BUCKET)
        monkeypatch.setattr(get_settings(), "gallery_s3_region", "us-east-1")
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client
    gallery_store.reset_client()


@pytest.fixture
def visitor(world: World) -> Visitor:
    return Visitor(world.http())


def _keys(client: Any, prefix: str = "") -> list[str]:
    answer = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(item["Key"] for item in answer.get("Contents", []))


def _body(client: Any, key: str) -> bytes:
    return bytes(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())


async def _settle(world: World, worker: GalleryWorker | None = None) -> None:
    """Run the gallery worker until files, selfies and folders are all done."""
    worker = worker or GalleryWorker(get_settings())
    for _ in range(400):
        await worker.tick()
        if worker.in_flight:
            await asyncio.wait(worker.in_flight)
            continue
        busy = await world.factory.fetchval(
            "SELECT (SELECT count(*) FROM gallery_media WHERE status IN ('queued', 'processing') AND next_attempt_at <= now())"
            " + (SELECT count(*) FROM gallery_people WHERE rematch_pending)"
        )
        if not busy and not await gallery_folders.has_work():
            return
    raise AssertionError("the gallery worker did not finish")


# ---- the real copy --------------------------------------------------------------------------


async def test_a_file_lives_on_s3_as_it_was_sent_and_browsers_get_signed_links(
    world: World, visitor: Visitor, s3: Any
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    before = set(_root().glob("*.png"))
    data = _picture(1, salt=1)
    upload = await visitor.upload(data, "Stage night.png")
    media_id = upload.json()["id"]
    await _settle(world)

    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and row.location == "s3" and row.source_key is None
    assert row.storage_key == f"files/{media_id}/Stage night.png" == row.display_key
    assert _body(s3, row.storage_key) == data
    head = s3.head_object(Bucket=BUCKET, Key=row.storage_key)
    assert head["ContentType"] == "image/png" and "immutable" in head["CacheControl"]
    # The full-size file left this machine; the small picture stays for fast pages.
    assert set(_root().glob("*.png")) == before
    assert (_root() / row.thumb_key).exists()

    item = (await visitor.page(person))["media"][0]
    assert item["url"].startswith(f"https://{BUCKET}.s3.amazonaws.com/") and "X-Amz-Signature=" in item["url"]
    assert item["thumb_url"].startswith("/media/")
    assert "response-content-disposition=attachment" in item["download_url"]
    assert "Stage%2520night.png" in item["download_url"]  # the saved file keeps its name


async def test_the_same_file_never_lands_twice_and_a_retry_just_replaces_it(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(9, salt=2)
    first = await visitor.upload(data, "one.png")
    second = await visitor.upload(data, "two.png")
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    await _settle(world)
    assert _keys(s3, "files/") == [f"files/{first.json()['id']}/one.png"]


async def test_s3_being_down_loses_nothing(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=3)
    media_id = (await visitor.upload(data)).json()["id"]
    real_upload = gallery_store.upload_file

    def broken(path: Path, key: str) -> None:
        raise OSError("S3 is down")

    monkeypatch.setattr(gallery_store, "upload_file", broken)
    worker = GalleryWorker(get_settings())
    await worker.tick()
    await asyncio.wait(worker.in_flight)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "queued" and row.source_key  # it waits, with the file it arrived as
    assert (_root() / row.source_key).read_bytes() == data
    assert _keys(s3, "files/") == []

    monkeypatch.setattr(gallery_store, "upload_file", real_upload)
    await world.factory.execute("UPDATE gallery_media SET next_attempt_at = now() WHERE id = :m", m=media_id)
    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and _body(s3, row.storage_key) == data


async def test_a_file_shared_before_s3_moves_there_by_itself(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "gallery_s3_bucket", "")
    person = await visitor.enrolled("Asha", person=1)
    data = _picture(1, salt=4)
    media_id = (await visitor.upload(data, "old.png")).json()["id"]
    worker = GalleryWorker(get_settings())
    for _ in range(5):
        await worker.tick()
        if worker.in_flight:
            await asyncio.wait(worker.in_flight)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and row.location == "local"
    local = _root() / row.storage_key
    assert local.read_bytes() == data

    monkeypatch.setattr(get_settings(), "gallery_s3_bucket", BUCKET)
    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.location == "s3" and _body(s3, row.storage_key) == data
    assert not local.exists()
    assert len(_keys(s3, "people/Asha/")) == 1
    assert "amazonaws.com" in (await visitor.page(person))["media"][0]["url"]


async def test_a_file_that_is_gone_does_not_stop_the_others(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "gallery_s3_bucket", "")
    await visitor.enrolled("Asha", person=1)
    gone = (await visitor.upload(_picture(1, salt=5), "gone.png")).json()["id"]
    kept = (await visitor.upload(_picture(1, salt=6), "kept.png")).json()["id"]
    worker = GalleryWorker(get_settings())
    for _ in range(5):
        await worker.tick()
        if worker.in_flight:
            await asyncio.wait(worker.in_flight)
    key = await world.factory.fetchval("SELECT storage_key FROM gallery_media WHERE id = :m", m=gone)
    (_root() / key).unlink()
    monkeypatch.setattr(get_settings(), "gallery_s3_bucket", BUCKET)
    await _settle(world)
    assert await world.factory.fetchval("SELECT status FROM gallery_media WHERE id = :m", m=gone) == "failed"
    assert await world.factory.fetchval("SELECT location FROM gallery_media WHERE id = :m", m=kept) == "s3"


async def test_the_ceiling_stops_uploads_only_on_s3(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    start = {"name": "a.png", "size": 1001, "content_type": "image/png"}
    monkeypatch.setattr(get_settings(), "gallery_max_total_bytes", 1000)
    assert_error(await visitor.http.post(f"{GALLERY}/uploads", json=start), 507, "INSUFFICIENT_STORAGE")
    monkeypatch.setattr(get_settings(), "gallery_max_total_bytes", 0)
    assert (await visitor.http.post(f"{GALLERY}/uploads", json=start)).status_code == 201
    monkeypatch.setattr(get_settings(), "gallery_max_total_bytes", 1000)
    monkeypatch.setattr(get_settings(), "gallery_s3_bucket", "")
    assert (await visitor.http.post(f"{GALLERY}/uploads", json=start)).status_code == 201


async def test_a_prefix_keeps_a_preview_apart(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "gallery_s3_prefix", "preview/")
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    await visitor.upload(_picture(1, salt=7))
    await _settle(world)
    keys = _keys(s3)
    assert len(keys) == 2 and all(key.startswith("preview/") for key in keys)
    assert [key for key in keys if key.startswith("preview/people/Asha/")]


# ---- people's folders ------------------------------------------------------------------------


async def test_a_group_photo_is_in_the_folder_of_each_named_person_only(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled("Asha", person=1)
    await Visitor(world.http()).enrolled("Ben", person=2)
    await _settle(world)
    data = _picture(1, second=2, salt=10)
    media_id = (await visitor.upload(data, "group.png")).json()["id"]
    stranger = (await visitor.upload(_picture(9, salt=11), "stranger.png")).json()["id"]
    await _settle(world)

    tail = f"group {uuid.UUID(media_id).hex[:6]}.png"
    assert _keys(s3, "people/") == [f"people/Asha/{tail}", f"people/Ben/{tail}"]
    assert _body(s3, f"people/Ben/{tail}") == data
    # Someone nobody has named gets no folder, but their file is safe under files/.
    assert f"files/{stranger}/stranger.png" in _keys(s3, "files/")
    done = await world.factory.fetch(
        "SELECT folder, folder_wanted, folder_done FROM gallery_people WHERE name <> ''"
    )
    assert sorted(row.folder for row in done) == ["Asha", "Ben"]
    assert all(row.folder_wanted == row.folder_done for row in done)


async def test_a_selfie_added_later_fills_the_folder(world: World, visitor: Visitor, s3: Any) -> None:
    uploader = Visitor(world.http())
    await uploader.enrolled("Ben", person=2)
    await _settle(world)
    await uploader.upload(_picture(9, salt=12), "first.png")
    await uploader.upload(_picture(9, salt=13), "second.png")
    await _settle(world)
    assert _keys(s3, "people/Chitra") == []
    await visitor.enrolled("Chitra", person=9)
    await _settle(world)
    assert len(_keys(s3, "people/Chitra/")) == 2


async def test_renaming_someone_moves_their_folder_and_nothing_else(
    world: World, visitor: Visitor, s3: Any
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=14)
    media_id = (await visitor.upload(data, "photo.png")).json()["id"]
    await _settle(world)
    assert len(_keys(s3, "people/Asha/")) == 1
    canonical = _keys(s3, "files/")

    admin = await world.admin()
    assert (await admin.patch(f"{ORGANISER}/people/{person}", {"name": "Asha Rao"})).status_code == 200
    await _settle(world)
    assert _keys(s3, "people/Asha/") == []
    moved = _keys(s3, "people/Asha Rao/")
    assert moved == [f"people/Asha Rao/photo {uuid.UUID(media_id).hex[:6]}.png"]
    assert _body(s3, moved[0]) == data
    assert _keys(s3, "files/") == canonical
    assert await world.factory.fetchval("SELECT s3_key FROM gallery_person_files") == moved[0]

    # Clearing the name takes the folder away; the file stays.
    assert (await admin.patch(f"{ORGANISER}/people/{person}", {"name": ""})).status_code == 200
    await _settle(world)
    assert _keys(s3, "people/") == [] and _keys(s3, "files/") == canonical


async def test_two_people_with_one_name_get_two_folders(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("Asha", person=1)
    await Visitor(world.http()).enrolled("ASHA", person=2)
    await _settle(world)
    await visitor.upload(_picture(1, second=2, salt=15))
    await _settle(world)
    folders = {key.split("/")[1] for key in _keys(s3, "people/")}
    assert len(folders) == 2
    assert {name.lower() for name in folders} == {"asha", "asha (2)"}
    # The numbered folder stays as it is when nothing about the name changed.
    await gallery_folders.recheck_everyone()
    before = _keys(s3, "people/")
    await _settle(world)
    assert _keys(s3, "people/") == before


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Asha Rao", "Asha Rao"),
        ("  Asha   Rao ", "Asha Rao"),
        ("../../etc/passwd", "etc passwd"),
        ("a/b\\c", "a b c"),
        ("नमन जैन", "नमन जैन"),
        ("નમન જૈન", "નમન જૈન"),
        ("O'Brien & Sons", "O'Brien & Sons"),
        ("..", "fallback"),
        ("///", "fallback"),
        ("", "fallback"),
        ("x" * 200, "x" * 80),
        ("<script>alert(1)</script>", "script alert(1) script"),
    ],
)
def test_names_become_safe_keys(typed: str, expected: str) -> None:
    assert gallery_store.safe_name(typed, "fallback") == expected


async def test_a_hostile_name_cannot_leave_its_folder(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("../../files/owned", person=1)
    await _settle(world)
    media_id = (await visitor.upload(_picture(1, salt=16), "../../evil/../x.png")).json()["id"]
    await _settle(world)
    for key in _keys(s3):
        assert ".." not in key and key.split("/")[0] in ("files", "people")
    assert [key for key in _keys(s3, "files/") if media_id in key]
    assert len(_keys(s3, "people/")) == 1 and _keys(s3, "people/")[0].count("/") == 2


async def test_removing_yourself_empties_your_folder_and_leaves_the_rest(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled("Asha", person=1)
    other = Visitor(world.http())
    await other.enrolled("Ben", person=2)
    await _settle(world)
    await other.upload(_picture(1, second=2, salt=17))
    await _settle(world)
    assert len(_keys(s3, "people/Asha/")) == len(_keys(s3, "people/Ben/")) == 1
    assert (await visitor.http.delete(f"{GALLERY}/me")).status_code == 204
    await _settle(world)
    assert _keys(s3, "people/Asha/") == []
    assert len(_keys(s3, "people/Ben/")) == 1 and len(_keys(s3, "files/")) == 1


async def test_removing_a_file_removes_every_copy(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("Asha", person=1)
    await Visitor(world.http()).enrolled("Ben", person=2)
    await _settle(world)
    media_id = (await visitor.upload(_picture(1, second=2, salt=18))).json()["id"]
    await _settle(world)
    assert len(_keys(s3)) == 3
    admin = await world.admin()
    assert (await admin.delete(f"{ORGANISER}/media/{media_id}")).status_code == 204
    await _settle(world)
    assert _keys(s3) == []
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_storage_trash") == 0
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_person_files") == 0


async def test_merging_people_merges_their_folders(world: World, visitor: Visitor, s3: Any) -> None:
    keeper = await visitor.enrolled("Asha", person=1)
    other = Visitor(world.http())
    second = await other.enrolled("Asha R", person=2)
    await _settle(world)
    await visitor.upload(_picture(1, salt=19), "one.png")
    await other.upload(_picture(2, salt=20), "two.png")
    await _settle(world)
    admin = await world.admin()
    merged = await admin.post(f"{ORGANISER}/people/{second}/merge", {"into": keeper})
    assert merged.status_code == 200, merged.text
    await _settle(world)
    assert _keys(s3, "people/Asha R/") == []
    assert len(_keys(s3, "people/Asha/")) == 2


async def test_a_worker_stopped_halfway_carries_on_where_it_was(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    real_copy = gallery_store.copy_object
    monkeypatch.setattr(gallery_store, "copy_object", lambda source, target: None)
    await visitor.upload(_picture(1, salt=21), "one.png")
    await visitor.upload(_picture(1, salt=22), "two.png")
    await _settle(world)
    await world.factory.execute("DELETE FROM gallery_person_files")

    copied: list[str] = []

    def fails_on_the_second(source: str, target: str) -> None:
        if copied:
            raise OSError("the connection dropped")
        real_copy(source, target)
        copied.append(target)

    monkeypatch.setattr(gallery_store, "copy_object", fails_on_the_second)
    wanted = await world.factory.fetchval("SELECT folder_wanted FROM gallery_people WHERE id = :p", p=person)
    await world.factory.execute(
        "UPDATE gallery_people SET folder_wanted = :v WHERE id = :p", v=wanted + 1, p=person
    )
    worker = GalleryWorker(get_settings())
    await worker.tick()
    await asyncio.wait(worker.in_flight)
    assert len(copied) == 1 and worker._storage_retry_at > 0
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_person_files") == 1
    row = await world.factory.fetchrow(
        "SELECT folder_wanted, folder_done FROM gallery_people WHERE id = :p", p=person
    )
    assert row.folder_wanted != row.folder_done  # not marked done, so it runs again

    monkeypatch.setattr(gallery_store, "copy_object", real_copy)
    worker._storage_retry_at = 0
    await _settle(world, worker)
    assert len(_keys(s3, "people/Asha/")) == 2
    row = await world.factory.fetchrow(
        "SELECT folder_wanted, folder_done FROM gallery_people WHERE id = :p", p=person
    )
    assert row.folder_wanted == row.folder_done


async def test_organisers_see_where_the_files_are_and_the_cost(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=23)
    await visitor.upload(data)
    await _settle(world)
    admin = await world.admin()
    storage = (await admin.get(ORGANISER)).json()["storage"]
    assert storage["where"] == "s3" and storage["bucket"] == BUCKET
    assert storage["folder_bytes"] == len(data) and storage["monthly_usd"] >= 0
    listed = (await admin.get(f"{ORGANISER}/media")).json()["media"][0]
    assert "X-Amz-Signature=" in listed["download_url"] and listed["thumb_url"].startswith("/media/")
    analysis = (await admin.get(f"{ORGANISER}/media/{listed['id']}/analysis")).json()
    assert "X-Amz-Signature=" in analysis["media"]["url"]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")


def _old_camera_video(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=5:duration=2",
         "-c:v", "mpeg4", str(path)],
        check=True,
    )  # fmt: skip


@needs_ffmpeg
async def test_a_video_browsers_cannot_play_has_both_copies_on_s3(
    world: World, visitor: Visitor, s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mkv"
    _old_camera_video(clip)
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    monkeypatch.setattr(gallery_files, "find_faces", lambda bgr, *, min_px=24: [])
    media_id = (await visitor.upload(clip.read_bytes(), "old-camera.mkv", "video/x-matroska")).json()["id"]
    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and row.location == "s3", row.last_error
    assert row.storage_key == f"files/{media_id}/old-camera.mp4"
    assert row.display_key == f"files/{media_id}/playable.mp4"
    assert sorted(_keys(s3, f"files/{media_id}/")) == sorted({row.storage_key, row.display_key})


# ---- straight from the browser to S3 --------------------------------------------------------


def _send_part(client: Any, url: str, number: int, body: bytes) -> None:
    """What the browser does with a signed link, through boto3 because moto only hears boto3."""
    assert "partNumber=" in url and "X-Amz-Signature=" in url
    client.upload_part(
        Bucket=BUCKET,
        Key=unquote(urlsplit(url).path.lstrip("/")),
        UploadId=parse_qs(urlsplit(url).query)["uploadId"][0],
        PartNumber=number,
        Body=body,
    )


async def _send_directly(
    visitor: Visitor, client: Any, data: bytes, name: str, *, part: int | None = None
) -> dict[str, Any]:
    """What the page does: asks for signed links, sends the parts to S3 itself, then says it is done."""
    started = await visitor.http.post(
        f"{GALLERY}/uploads",
        json={"name": name, "size": len(data), "content_type": "image/png", "direct": True},
    )
    assert started.status_code == 201, started.text
    answer = started.json()
    assert answer["mode"] == "s3"
    part_size = part or answer["part_size"]
    count = max(1, -(-len(data) // part_size))
    signed = await visitor.http.post(
        f"{GALLERY}/uploads/{answer['upload_id']}/parts", json={"numbers": list(range(1, count + 1))}
    )
    assert signed.status_code == 200, signed.text
    for number in range(1, count + 1):
        _send_part(
            client,
            signed.json()["urls"][str(number)],
            number,
            data[(number - 1) * part_size : number * part_size],
        )
    return dict(answer)


async def test_a_file_sent_straight_to_s3_ends_up_like_any_other(
    world: World, visitor: Visitor, s3: Any
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=30)
    started = await _send_directly(visitor, s3, data, "direct.png")
    # It reached S3 without passing through this server: nothing on the disk here.
    assert not list((_root() / gallery_files.INCOMING).glob("*"))
    done = await visitor.http.post(f"{GALLERY}/uploads/{started['upload_id']}/complete")
    assert done.status_code == 201, done.text
    media_id = done.json()["id"]
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.source_on_s3 is True and row.status == "queued" and row.sha256.startswith("pending-")
    assert row.source_key in _keys(s3, "incoming/")

    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and row.location == "s3" and row.source_key is None
    assert row.sha256 == hashlib.sha256(data).hexdigest()
    assert _body(s3, row.storage_key) == data
    assert _keys(s3, "incoming/") == []  # the waiting copy has gone
    assert len(_keys(s3, "people/Asha/")) == 1
    assert (await visitor.page(person))["person"]["photos"] == 1
    assert list((_root() / gallery_files.INCOMING).glob("*")) == []


async def test_a_large_file_goes_up_in_parts(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    part = 5 * 1024 * 1024
    data = _picture(1, salt=31) + bytes(2 * part + 1234)  # a PNG with padding after it
    started = await _send_directly(visitor, s3, data, "big.png", part=part)
    done = await visitor.http.post(f"{GALLERY}/uploads/{started['upload_id']}/complete")
    assert done.status_code == 201, done.text
    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=done.json()["id"])
    assert row.status == "ready" and _body(s3, row.storage_key) == data


async def test_a_file_only_part_way_there_is_not_accepted(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled()
    data = _picture(1, salt=32)
    started = await visitor.http.post(
        f"{GALLERY}/uploads",
        json={"name": "a.png", "size": len(data) + 100, "content_type": "image/png", "direct": True},
    )
    upload_id = started.json()["upload_id"]
    signed = await visitor.http.post(f"{GALLERY}/uploads/{upload_id}/parts", json={"numbers": [1]})
    _send_part(s3, signed.json()["urls"]["1"], 1, data)
    answer = await visitor.http.post(f"{GALLERY}/uploads/{upload_id}/complete")
    assert_error(answer, 409, "UPLOAD_INCOMPLETE")
    assert answer.json()["error"]["details"]["received"] == len(data)
    assert (await visitor.http.delete(f"{GALLERY}/uploads/{upload_id}")).status_code == 204
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 0


async def test_something_that_is_not_a_picture_is_refused_and_removed(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled()
    started = await _send_directly(visitor, s3, b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "x.svg")
    assert_error(
        await visitor.http.post(f"{GALLERY}/uploads/{started['upload_id']}/complete"), 422, "INVALID_MEDIA"
    )
    await _settle(world)
    assert _keys(s3, "incoming/") == []
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_media") == 0


async def test_the_same_file_sent_straight_twice_counts_once(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=33)
    for name in ("one.png", "two.png"):
        started = await _send_directly(visitor, s3, data, name)
        assert (
            await visitor.http.post(f"{GALLERY}/uploads/{started['upload_id']}/complete")
        ).status_code == 201
    await _settle(world)
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_media") == 1
    assert _keys(s3, "incoming/") == [] and len(_keys(s3, "files/")) == 1


async def test_someone_elses_direct_upload_cannot_be_touched(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled()
    started = await _send_directly(visitor, s3, _picture(1, salt=34), "a.png")
    other = Visitor(world.http())
    await other.enrolled("Ben", person=2)
    upload_id = started["upload_id"]
    assert_error(
        await other.http.post(f"{GALLERY}/uploads/{upload_id}/parts", json={"numbers": [1]}), 404, "NOT_FOUND"
    )
    assert_error(await other.http.post(f"{GALLERY}/uploads/{upload_id}/complete"), 404, "NOT_FOUND")
    assert_error(await other.http.delete(f"{GALLERY}/uploads/{upload_id}"), 404, "NOT_FOUND")


async def test_a_browser_that_cannot_reach_s3_sends_through_the_server_instead(
    world: World, visitor: Visitor, s3: Any
) -> None:
    """The page's fallback: no `direct` in its request. The file still ends up on S3."""
    me = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    assert (await visitor.me())["direct_uploads"] is True
    data = _picture(1, salt=35)
    answer = await visitor.upload(data, "through.png")
    assert answer.status_code == 201, answer.text
    await _settle(world)
    assert (await visitor.page(me))["person"]["photos"] == 1
    assert len(_keys(s3, "files/")) == 1


async def test_an_upload_nobody_finished_is_cleared_after_three_days(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled()
    await _settle(world)
    started = await visitor.http.post(
        f"{GALLERY}/uploads", json={"name": "a.png", "size": 10, "content_type": "image/png", "direct": True}
    )
    upload_id = started.json()["upload_id"]
    await _settle(world)
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 1  # still in progress
    await world.factory.execute(
        "UPDATE gallery_s3_uploads SET created_at = now() - interval '4 days' WHERE id = :i", i=upload_id
    )
    await _settle(world)
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 0
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []


# ---- from the organisers' console -----------------------------------------------------------


async def _console_sends_directly(admin: AdminClient, client: Any, data: bytes, name: str) -> str:
    """What the console does, which is what the page does, under the organiser's session."""
    started = await admin.post(
        f"{ORGANISER}/uploads",
        {"name": name, "size": len(data), "content_type": "image/png", "direct": True},
    )
    assert started.status_code == 201, started.text
    assert started.json()["mode"] == "s3" and started.json()["part_size"] > 0
    upload_id = str(started.json()["upload_id"])
    signed = await admin.post(f"{ORGANISER}/uploads/{upload_id}/parts", {"numbers": [1]})
    assert signed.status_code == 200, signed.text
    _send_part(client, signed.json()["urls"]["1"], 1, data)
    return upload_id


async def test_an_organisers_file_sent_straight_to_s3_ends_up_like_any_other(
    world: World, visitor: Visitor, s3: Any
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await Visitor(world.http()).enrolled("Ben", person=2)
    await _settle(world)
    admin = await world.admin()
    data = _picture(1, second=2, salt=80)
    upload_id = await _console_sends_directly(admin, s3, data, "group.png")
    # It reached S3 without passing through this server: nothing on the disk here.
    assert not list((_root() / gallery_files.INCOMING).glob("*"))
    done = await admin.post(f"{ORGANISER}/uploads/{upload_id}/complete")
    assert done.status_code == 201, done.text
    media_id = done.json()["id"]
    assert done.json() == {"status": "uploaded", "duplicate": False, "id": media_id}
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.source_on_s3 is True and row.status == "queued" and row.source_key in _keys(s3, "incoming/")
    assert row.uploader_admin_id == admin.info.id and row.uploader_person_id is None
    assert row.uploader_ip == world.ip

    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=media_id)
    assert row.status == "ready" and row.location == "s3" and row.source_key is None
    assert row.sha256 == hashlib.sha256(data).hexdigest()
    assert row.uploader_admin_id == admin.info.id
    assert _body(s3, row.storage_key) == data
    assert _keys(s3, "incoming/") == []
    tail = f"group {uuid.UUID(media_id).hex[:6]}.png"
    assert _keys(s3, "people/") == [f"people/Asha/{tail}", f"people/Ben/{tail}"]
    assert _body(s3, f"people/Ben/{tail}") == data
    assert (await visitor.page(person))["media"][0]["id"] == media_id

    listed = (await admin.get(f"{ORGANISER}/media")).json()["media"]
    assert [(m["id"], m["uploader"]) for m in listed] == [(media_id, "Organiser")]
    rows = await world.factory.fetch(
        "SELECT actor_id, target_type, target_id, details FROM audit_log "
        "WHERE programme_id = :p AND action = 'gallery.media_uploaded'",
        p=world.programme_id,
    )
    assert [tuple(row) for row in rows] == [
        (
            admin.info.id,
            "gallery_media",
            media_id,
            # Only the worker sees all of it, so here nobody can say yet.
            {"name": "group.png", "bytes": len(data), "duplicate": None},
        )
    ]


async def test_an_organiser_and_a_guest_cannot_touch_each_others_direct_uploads(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled()
    guests = (await _send_directly(visitor, s3, _picture(1, salt=81), "guest.png"))["upload_id"]
    admin = await world.admin()
    colleague = await world.admin(fresh=True)
    mine = await _console_sends_directly(admin, s3, _picture(1, salt=82), "console.png")

    for organiser, upload_id in ((colleague, mine), (admin, guests), (colleague, guests)):
        parts = await organiser.post(f"{ORGANISER}/uploads/{upload_id}/parts", {"numbers": [1]})
        assert_error(parts, 404, "NOT_FOUND")
        assert_error(await organiser.post(f"{ORGANISER}/uploads/{upload_id}/complete"), 404, "NOT_FOUND")
        assert_error(await organiser.delete(f"{ORGANISER}/uploads/{upload_id}"), 404, "NOT_FOUND")
    path = f"{GALLERY}/uploads/{mine}"
    assert_error(await visitor.http.post(f"{path}/parts", json={"numbers": [1]}), 404, "NOT_FOUND")
    assert_error(await visitor.http.post(f"{path}/complete"), 404, "NOT_FOUND")
    assert_error(await visitor.http.delete(path), 404, "NOT_FOUND")
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 2

    # The right ones still finish theirs.
    assert (await admin.post(f"{ORGANISER}/uploads/{mine}/complete")).status_code == 201
    assert (await visitor.http.post(f"{GALLERY}/uploads/{guests}/complete")).status_code == 201


async def test_an_organiser_can_abandon_a_direct_upload(world: World, s3: Any) -> None:
    admin = await world.admin()
    upload_id = await _console_sends_directly(admin, s3, _picture(1, salt=83), "a.png")
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) != []
    assert (await admin.delete(f"{ORGANISER}/uploads/{upload_id}")).status_code == 204
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 0
    # No object was ever joined, and looking for it leaves nothing waiting.
    await _settle(world)
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_storage_trash") == 0
    assert _keys(s3) == []


async def test_the_ceiling_stops_organisers_too(
    world: World, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await world.admin()
    start = {"name": "a.png", "size": 1001, "content_type": "image/png"}
    monkeypatch.setattr(get_settings(), "gallery_max_total_bytes", 1000)
    for direct in (False, True):
        answer = await admin.post(f"{ORGANISER}/uploads", {**start, "direct": direct})
        assert_error(answer, 507, "INSUFFICIENT_STORAGE")
    monkeypatch.setattr(get_settings(), "gallery_max_total_bytes", 1001)
    assert (await admin.post(f"{ORGANISER}/uploads", {**start, "direct": True})).status_code == 201


async def test_something_from_an_organiser_that_is_not_a_picture_is_refused_and_removed(
    world: World, s3: Any
) -> None:
    admin = await world.admin()
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    upload_id = await _console_sends_directly(admin, s3, svg, "x.svg")
    assert_error(await admin.post(f"{ORGANISER}/uploads/{upload_id}/complete"), 422, "INVALID_MEDIA")
    await _settle(world)
    assert _keys(s3, "incoming/") == []
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_media") == 0
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 0
    assert not await world.factory.fetch(
        "SELECT id FROM audit_log WHERE programme_id = :p AND action = 'gallery.media_uploaded'",
        p=world.programme_id,
    )


async def test_the_same_file_sent_straight_by_an_organiser_counts_once(
    world: World, visitor: Visitor, s3: Any
) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    data = _picture(1, salt=84)
    first = await visitor.upload(data, "guest.png")
    await _settle(world)
    admin = await world.admin()
    upload_id = await _console_sends_directly(admin, s3, data, "console.png")
    # Only the worker sees all of it, so here it is still a new file.
    done = await admin.post(f"{ORGANISER}/uploads/{upload_id}/complete")
    assert done.json()["duplicate"] is False
    await _settle(world)
    rows = await world.factory.fetch("SELECT id, original_name FROM gallery_media")
    assert [(str(row.id), row.original_name) for row in rows] == [(first.json()["id"], "guest.png")]
    assert _keys(s3, "incoming/") == [] and len(_keys(s3, "files/")) == 1
    # The log does not claim what nobody knew when it was written. It stays as it was once the worker knows.
    audited = await world.factory.fetchrow(
        "SELECT details FROM audit_log WHERE programme_id = :p AND action = 'gallery.media_uploaded'",
        p=world.programme_id,
    )
    assert audited.details == {"name": "console.png", "bytes": len(data), "duplicate": None}


async def test_a_console_that_cannot_reach_s3_sends_through_the_server_instead(
    world: World, visitor: Visitor, s3: Any
) -> None:
    """The console's fallback: no `direct` in its request. The file still ends up on S3."""
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    admin = await world.admin()
    answer = await _console_upload(admin, _picture(1, salt=85), "through.png")
    assert answer.status_code == 201, answer.text
    await _settle(world)
    assert (await visitor.page(person))["person"]["photos"] == 1
    assert len(_keys(s3, "people/Asha/")) == 1
    row = await world.factory.fetchrow(
        "SELECT location, source_on_s3, uploader_admin_id FROM gallery_media WHERE id = :m",
        m=answer.json()["id"],
    )
    assert tuple(row) == ("s3", False, admin.info.id)


# ---- an upload that goes wrong near the end -------------------------------------------------


def _waiting_files(world: World) -> Any:
    return world.factory.fetchval(
        "SELECT (SELECT count(*) FROM gallery_media) + (SELECT count(*) FROM gallery_s3_uploads)"
    )


@pytest.mark.parametrize("then", ["delete", "nobody-comes-back"])
async def test_a_complete_that_fails_after_s3_joined_the_parts_leaves_nothing_behind(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch, then: str
) -> None:
    await visitor.enrolled()
    await _settle(world)
    upload_id = (await _send_directly(visitor, s3, _picture(1, salt=40), "a.png"))["upload_id"]
    path = f"{GALLERY}/uploads/{upload_id}"

    def network_down(key: str, count: int = 64) -> bytes:
        raise OSError("the network went away")

    with monkeypatch.context() as patch:
        patch.setattr(gallery_store, "first_bytes", network_down)
        assert_error(await visitor.http.post(f"{path}/complete"), 500, "INTERNAL_ERROR")
    assert _keys(s3, "incoming/") == [f"incoming/{upload_id}"]  # joined, and nobody refers to it any more
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []
    if then == "delete":
        assert (await visitor.http.delete(path)).status_code == 204
    else:
        await world.factory.execute(
            "UPDATE gallery_s3_uploads SET created_at = now() - interval '4 days' WHERE id = :i", i=upload_id
        )
    await _settle(world)
    assert _keys(s3) == []
    assert await _waiting_files(world) == 0
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_storage_trash") == 0


async def test_an_abort_that_lands_while_complete_runs_wins_and_nothing_is_queued(
    world: World, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await world.admin()
    upload_id = await _console_sends_directly(admin, s3, _picture(1, salt=41), "a.png")
    path = f"{ORGANISER}/uploads/{upload_id}"
    loop = asyncio.get_running_loop()
    real = gallery_store.first_bytes
    aborts: list[int] = []

    def aborted_meanwhile(key: str, count: int = 64) -> bytes:
        # S3 has joined the parts, and complete has not yet written anything down.
        aborts.append(asyncio.run_coroutine_threadsafe(admin.delete(path), loop).result(30).status_code)
        return real(key, count)

    monkeypatch.setattr(gallery_store, "first_bytes", aborted_meanwhile)
    assert_error(await admin.post(f"{path}/complete"), 404, "NOT_FOUND")
    assert aborts == [204]
    assert await _waiting_files(world) == 0
    assert not await world.factory.fetch(
        "SELECT id FROM audit_log WHERE programme_id = :p AND action = 'gallery.media_uploaded'",
        p=world.programme_id,
    )
    await _settle(world)
    assert _keys(s3) == []


async def test_an_abort_that_comes_after_complete_leaves_the_file_alone(world: World, s3: Any) -> None:
    admin = await world.admin()
    data = _picture(1, salt=42)
    upload_id = await _console_sends_directly(admin, s3, data, "a.png")
    path = f"{ORGANISER}/uploads/{upload_id}"
    done = await admin.post(f"{path}/complete")
    assert done.status_code == 201, done.text
    assert_error(await admin.delete(path), 404, "NOT_FOUND")
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_storage_trash") == 0
    await _settle(world)
    row = await world.factory.fetchrow("SELECT * FROM gallery_media WHERE id = :m", m=done.json()["id"])
    assert row.status == "ready" and row.location == "s3"
    assert _body(s3, row.storage_key) == data


async def test_the_disk_reserve_stops_direct_uploads_too(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    admin = await world.admin()
    start = {"name": "a.png", "size": 10, "content_type": "image/png", "direct": True}
    monkeypatch.setattr(get_settings(), "gallery_free_space_reserve_bytes", 1 << 62)
    assert_error(await admin.post(f"{ORGANISER}/uploads", start), 507, "INSUFFICIENT_STORAGE")
    assert_error(await visitor.http.post(f"{GALLERY}/uploads", json=start), 507, "INSUFFICIENT_STORAGE")
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []
    monkeypatch.setattr(get_settings(), "gallery_free_space_reserve_bytes", 0)
    assert (await admin.post(f"{ORGANISER}/uploads", start)).status_code == 201
    assert (await visitor.http.post(f"{GALLERY}/uploads", json=start)).status_code == 201


async def test_a_size_no_column_could_hold_never_reaches_s3(
    world: World, visitor: Visitor, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await visitor.enrolled()
    admin = await world.admin()
    monkeypatch.setattr(gallery, "_reject_if_disk_is_low", lambda size: None)
    start = {"name": "a.mp4", "size": 2**63, "content_type": "video/mp4", "direct": True}
    assert_error(await admin.post(f"{ORGANISER}/uploads", start), 422, "VALIDATION_FAILED")
    assert_error(await visitor.http.post(f"{GALLERY}/uploads", json=start), 422, "VALIDATION_FAILED")
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []

    # The largest it can hold still has its row, so the worker could clear it up.
    largest = await admin.post(f"{ORGANISER}/uploads", {**start, "size": 2**63 - 1})
    assert largest.status_code == 201, largest.text
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_s3_uploads") == 1


@pytest.mark.parametrize("bad", ["%00", "abc%00def"])
async def test_an_id_with_a_nul_in_it_is_not_found_with_s3_on(
    world: World, visitor: Visitor, s3: Any, bad: str
) -> None:
    await visitor.enrolled()
    admin = await world.admin()
    assert_error(await admin.post(f"{ORGANISER}/uploads/{bad}/parts", {"numbers": [1]}), 404, "NOT_FOUND")
    assert_error(await admin.post(f"{ORGANISER}/uploads/{bad}/complete"), 404, "NOT_FOUND")
    assert_error(await admin.delete(f"{ORGANISER}/uploads/{bad}"), 404, "NOT_FOUND")
    path = f"{GALLERY}/uploads/{bad}"
    assert_error(await visitor.http.post(f"{path}/parts", json={"numbers": [1]}), 404, "NOT_FOUND")
    assert_error(await visitor.http.post(f"{path}/complete"), 404, "NOT_FOUND")
    assert_error(await visitor.http.delete(path), 404, "NOT_FOUND")


async def test_a_location_in_xmp_reaches_neither_the_file_nor_the_folder_copies(
    world: World, visitor: Visitor, s3: Any
) -> None:
    person = await visitor.enrolled("Asha", person=1)
    await _settle(world)
    sent = _png_with_xmp(_picture(1, salt=43), XMP_PLACE)
    upload_id = (await _send_directly(visitor, s3, sent, "stage.png"))["upload_id"]
    assert (await visitor.http.post(f"{GALLERY}/uploads/{upload_id}/complete")).status_code == 201
    await _settle(world)
    assert (await visitor.page(person))["person"]["photos"] == 1
    copies = [*_keys(s3, "files/"), *_keys(s3, "people/")]
    assert len(copies) == 2 and _keys(s3, "incoming/") == []
    for key in copies:
        assert b"GPSLatitude" not in _body(s3, key)


# ---- scaling ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("waiting", "voting", "load", "expected"),
    [
        (0, False, 1.0, 2),
        (4, False, 1.0, 2),
        (5, False, 1.0, 3),
        (15, False, 1.0, 5),
        (30, False, 1.0, 8),
        (5000, False, 1.0, 8),  # never past the ceiling
        (5000, True, 1.0, 2),  # a round is open: the voting comes first
        (5000, False, 17.0, 2),  # the machine is busy with other work
        (5000, False, 16.9, 8),
    ],
)
def test_the_worker_reads_more_at_once_as_the_queue_grows(
    waiting: int, voting: bool, load: float, expected: int
) -> None:
    assert (
        wanted_concurrency(waiting=waiting, voting=voting, machine_load=load, cpus=20, low=2, high=8)
        == expected
    )


async def test_the_worker_scales_up_and_back_down_by_itself(world: World, visitor: Visitor, s3: Any) -> None:
    await visitor.enrolled("Asha", person=1)
    await _settle(world)
    worker = GalleryWorker(get_settings())
    assert worker.concurrency == 2
    for salt in range(50, 75):
        await visitor.upload(_picture(1, salt=salt))
    await worker._scale()
    assert worker.concurrency >= 7
    busiest = 0
    for _ in range(400):
        await worker.tick()
        busiest = max(busiest, len(worker.in_flight))
        if worker.in_flight:
            await asyncio.wait(worker.in_flight, return_when=asyncio.FIRST_COMPLETED)
        elif not await world.factory.fetchval("SELECT count(*) FROM gallery_media WHERE status <> 'ready'"):
            break
    assert busiest >= 5
    assert await world.factory.fetchval("SELECT count(*) FROM gallery_media WHERE status = 'ready'") == 25
    await worker._scale()
    assert worker.concurrency == 2
