"""Amazon S3 for the gallery's full-size files. Without a bucket set, they stay on the media volume.

A file has one real copy, at files/<media id>/<name>, which never moves. The database says who is in it.
people/<Name>/ is for browsing the bucket by person: services/gallery_folders.py keeps a copy there of
each file a named person appears in. Unnamed groups of faces get no folder: most are strangers in the
background, and a group photo would otherwise be stored once for each of them.

Uploads go straight from the browser to S3 in parts, each through its own signed link, so a phone's upload
never waits on this machine's internet line. They wait under incoming/ until the worker has read them.

Nothing here is public. The bucket blocks public access, and browsers get signed links that expire. Small
pictures (thumbnails, faces, frames) stay on the media volume: they can be made again from the files, and
Cloudflare caches them, so the People page loads fast and costs nothing to view.

boto3 blocks, so callers run these in a thread. Its clients are safe to share between threads.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

from ..config import get_settings

NAME_MAX = 80
DELETE_BATCH = 1000
CACHE_CONTROL = "private, max-age=31536000, immutable"
CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
}
# boto3 raises the part size by itself for a file too large for this one, up to S3's own 5 TB per file.
_TRANSFER = TransferConfig(
    multipart_threshold=64 * 1024 * 1024, multipart_chunksize=64 * 1024 * 1024, max_concurrency=4
)
_ALLOWED_PUNCTUATION = " -_.,'()&+"
# An upload in parts: S3 allows 10,000 parts of at least 5 MiB, for a file of up to 5 TB.
MIN_PART_SIZE = 8 * 1024 * 1024
MAX_PARTS = 10_000


def enabled() -> bool:
    return bool(get_settings().gallery_s3_bucket)


@lru_cache
def _client(bucket: str, region: str) -> Any:
    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
        retries={"max_attempts": 5, "mode": "standard"},
    )
    return boto3.client("s3", region_name=region, config=config)


def client() -> Any:
    settings = get_settings()
    return _client(settings.gallery_s3_bucket, settings.gallery_s3_region)


def reset_client() -> None:
    _client.cache_clear()


# ---- names ---------------------------------------------------------------------------------


def safe_name(value: str, fallback: str) -> str:
    """A name for a key, from text anyone can type: letters of any script stay, and no "/" or ".." can."""
    normal = unicodedata.normalize("NFC", value)
    kept = "".join(
        ch if unicodedata.category(ch)[0] in "LMN" or ch in _ALLOWED_PUNCTUATION else " " for ch in normal
    )
    while ".." in kept:
        kept = kept.replace("..", ".")
    clean = " ".join(kept.split()).strip(" .")
    return clean[:NAME_MAX].strip(" .") or fallback


def _stem(original_name: str) -> str:
    return original_name.rsplit(".", 1)[0] if "." in original_name else original_name


def extension_of(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _prefixed(key: str) -> str:
    return f"{get_settings().gallery_s3_prefix}{key}"


def file_key(media_id: uuid.UUID, original_name: str, extension: str) -> str:
    """Where a file's real copy lives. The same file always gets the same key, so sending it again is safe."""
    return _prefixed(f"files/{media_id}/{safe_name(_stem(original_name), 'file')}.{extension}")


def playable_key(media_id: uuid.UUID, extension: str) -> str:
    return _prefixed(f"files/{media_id}/playable.{extension}")


def person_key(folder: str, media_id: uuid.UUID, original_name: str, source_key: str) -> str:
    """A file in a person's folder. Two people's IMG_0001.jpg stay apart by the file's id."""
    name = f"{safe_name(_stem(original_name), 'file')} {media_id.hex[:6]}.{extension_of(source_key)}"
    return _prefixed(f"people/{folder}/{name}")


# ---- files ---------------------------------------------------------------------------------


def _object_args(key: str) -> dict[str, str]:
    content_type = CONTENT_TYPES.get(extension_of(key), "application/octet-stream")
    return {"ContentType": content_type, "CacheControl": CACHE_CONTROL}


def upload_file(path: Path, key: str) -> None:
    client().upload_file(
        str(path), get_settings().gallery_s3_bucket, key, ExtraArgs=_object_args(key), Config=_TRANSFER
    )


def copy_object(source_key: str, target_key: str) -> None:
    """A copy inside the bucket, of any size. Nothing goes through this machine."""
    bucket = get_settings().gallery_s3_bucket
    client().copy(
        {"Bucket": bucket, "Key": source_key},
        bucket,
        target_key,
        ExtraArgs={**_object_args(target_key), "MetadataDirective": "REPLACE"},
        Config=_TRANSFER,
    )


def delete_keys(keys: Iterable[str]) -> None:
    bucket = get_settings().gallery_s3_bucket
    pending = list(dict.fromkeys(keys))
    for start in range(0, len(pending), DELETE_BATCH):
        batch = pending[start : start + DELETE_BATCH]
        answer = client().delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
        )
        if answer.get("Errors"):
            raise OSError(
                f"S3 refused to delete {len(answer['Errors'])} objects: {answer['Errors'][0].get('Code')}"
            )


def incoming_key(upload_id: str) -> str:
    return _prefixed(f"incoming/{upload_id}")


def part_size_for(size: int) -> int:
    return max(MIN_PART_SIZE, (size + MAX_PARTS - 1) // MAX_PARTS)


def start_multipart(key: str) -> str:
    return str(client().create_multipart_upload(Bucket=get_settings().gallery_s3_bucket, Key=key)["UploadId"])


def part_url(key: str, s3_upload_id: str, number: int) -> str:
    settings = get_settings()
    return str(
        client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": settings.gallery_s3_bucket,
                "Key": key,
                "UploadId": s3_upload_id,
                "PartNumber": number,
            },
            ExpiresIn=settings.gallery_s3_url_hours * 3600,
        )
    )


def uploaded_parts(key: str, s3_upload_id: str) -> list[dict[str, Any]]:
    """What S3 holds of an upload. The browser need not report ETags, so CORS need not expose them."""
    pages = (
        client()
        .get_paginator("list_parts")
        .paginate(Bucket=get_settings().gallery_s3_bucket, Key=key, UploadId=s3_upload_id)
    )
    return [part for page in pages for part in page.get("Parts", [])]


def finish_multipart(key: str, s3_upload_id: str, parts: list[dict[str, Any]]) -> None:
    client().complete_multipart_upload(
        Bucket=get_settings().gallery_s3_bucket,
        Key=key,
        UploadId=s3_upload_id,
        MultipartUpload={
            "Parts": [{"PartNumber": part["PartNumber"], "ETag": part["ETag"]} for part in parts]
        },
    )


def abort_multipart(key: str, s3_upload_id: str) -> None:
    try:
        client().abort_multipart_upload(
            Bucket=get_settings().gallery_s3_bucket, Key=key, UploadId=s3_upload_id
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchUpload":
            raise


def first_bytes(key: str, count: int = 64) -> bytes:
    answer = client().get_object(
        Bucket=get_settings().gallery_s3_bucket, Key=key, Range=f"bytes=0-{count - 1}"
    )
    return bytes(answer["Body"].read())


def download_file(key: str, path: Path) -> None:
    client().download_file(get_settings().gallery_s3_bucket, key, str(path), Config=_TRANSFER)


def signed_url(key: str, download_name: str | None = None) -> str:
    """A link only for whoever the API gives it to, which stops working after a while. Browsers ignore
    the download attribute on another site's link, so S3 itself is asked to send the file to save."""
    settings = get_settings()
    params: dict[str, str] = {"Bucket": settings.gallery_s3_bucket, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quote(download_name, safe='')}"
    return str(
        client().generate_presigned_url(
            "get_object", Params=params, ExpiresIn=settings.gallery_s3_url_hours * 3600
        )
    )
