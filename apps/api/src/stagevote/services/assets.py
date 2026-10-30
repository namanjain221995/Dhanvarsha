"""Image uploads: validated, decoded, re-encoded to WebP under a random name.

SVG and every non-raster format are rejected: re-encoding a raster image drops any
embedded scripts or metadata, and no sanitiser for SVG is relied upon.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..deps import AdminContext
from ..errors import AppError
from ..models import Asset
from ..state import audit

MAX_PIXELS = 40_000_000
# A picture for the big screen may come straight from a camera or a print file. It is
# scaled down to MAX_EDGE before it is stored.
MAX_MEDIA_PIXELS = 250_000_000
# Pillow only raises above twice this value; the explicit check below enforces each limit.
Image.MAX_IMAGE_PIXELS = MAX_MEDIA_PIXELS
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
# opening_media is shown full screen on a projector or LED wall, so it keeps up to 4K.
MAX_EDGE = {
    "programme_logo": 512,
    "event_logo": 512,
    "nominee_photo": 640,
    "banner": 1600,
    "opening_media": 3840,
}


def _trim_transparent(img: Image.Image) -> Image.Image:
    """Cut away fully transparent margins so a logo is shown at its real size. The page
    and the emails show the programme logo without a frame, so empty margins would only
    make it look small."""
    if img.mode != "RGBA":
        return img
    box = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box is None or box == (0, 0, img.width, img.height):
        return img
    pad = max(1, round(max(box[2] - box[0], box[3] - box[1]) * 0.02))
    left, top = max(box[0] - pad, 0), max(box[1] - pad, 0)
    right, bottom = min(box[2] + pad, img.width), min(box[3] + pad, img.height)
    return img.crop((left, top, right, bottom))


def process_image(data: bytes | Path, kind: str) -> tuple[bytes, int, int]:
    """Check, decode and re-encode an image (its bytes or its file) to WebP sized for `kind`:
    (webp, width, height)."""
    limit = MAX_MEDIA_PIXELS if kind == "opening_media" else MAX_PIXELS
    edge = MAX_EDGE.get(kind, 800)

    def source() -> Image.Image:
        return Image.open(data if isinstance(data, Path) else io.BytesIO(data))

    try:
        with source() as probe:
            fmt = probe.format
            if probe.width * probe.height > limit:
                raise AppError(
                    "IMAGE_TOO_LARGE", f"Images must be at most {limit // 1_000_000} megapixels.", 422
                )
            probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise AppError("INVALID_IMAGE", "Upload a JPEG, PNG, WebP or GIF image.", 422) from exc
    if fmt not in ALLOWED_FORMATS:
        raise AppError("INVALID_IMAGE", "Upload a JPEG, PNG, WebP or GIF image.", 422)
    with source() as opened:
        opened.seek(0)
        if kind == "opening_media":
            # A large JPEG decodes straight at a half, a quarter or an eighth of its size, never
            # below the size it is stored at: far less memory and time.
            opened.draft("RGB", (edge, edge))
        img: Image.Image = ImageOps.exif_transpose(opened) or opened
        img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
        if kind == "programme_logo":
            img = _trim_transparent(img)
        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=84, method=4)
        return out.getvalue(), img.width, img.height


async def store_image(db: AsyncSession, upload: UploadFile, kind: str, ctx: AdminContext) -> Asset:
    settings = get_settings()
    data = await upload.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            f"Images must be {settings.max_upload_bytes // (1024 * 1024)} MB or smaller.",
            413,
        )
    if not data:
        raise AppError("INVALID_IMAGE", "The file is empty.", 422)
    encoded, width, height = await asyncio.to_thread(process_image, data, kind)
    storage_key = f"{uuid.uuid4().hex}.webp"
    root = Path(settings.media_root)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    tmp = root / f".{storage_key}.tmp"
    await asyncio.to_thread(tmp.write_bytes, encoded)
    os.replace(tmp, root / storage_key)
    asset = Asset(
        kind=kind,
        storage_key=storage_key,
        content_type="image/webp",
        width=width,
        height=height,
        byte_size=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
        created_by_admin_id=ctx.admin_id,
    )
    async with db.begin():
        db.add(asset)
        await db.flush()
        await audit(
            db,
            ctx.actor,
            "asset.uploaded",
            target_type="asset",
            target_id=asset.id,
            details={"kind": kind, "bytes": len(encoded)},
        )
    return asset


def store_generated_image(img: Image.Image, kind: str) -> tuple[str, int, int, bytes]:
    """Used by the demo seed to store locally generated placeholder artwork."""
    settings = get_settings()
    edge = MAX_EDGE.get(kind, 800)
    img = img.copy()
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=88, method=4)
    data = out.getvalue()
    storage_key = f"{uuid.uuid4().hex}.webp"
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / storage_key).write_bytes(data)
    return storage_key, img.width, img.height, data
