"""Chunked uploads of the image or video an opening-page card shows full screen.

POST /media/uploads starts one, PUT /media/uploads/{id}?offset=n sends each chunk as the raw
request body, POST /media/uploads/{id}/complete turns it into an asset, and DELETE
/media/uploads/{id} abandons it. Only the organiser who started an upload can see it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from ..db import get_db
from ..deps import AdminContext, current_admin
from ..errors import AppError
from ..services import media
from .common import NO_STORE, Model, SingleLine, no_store

admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(current_admin)])


class UploadIn(Model):
    name: SingleLine = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    # What the browser says the file is. It only picks the size limit; the file's own first
    # bytes decide what it is.
    content_type: str = Field(default="", max_length=255)


async def _chunk(request: Request) -> bytearray:
    """The raw body, refused once it passes CHUNK_SIZE (Content-Length may be absent).

    Nothing is written before the whole body has arrived, so a chunk the browser cancels
    halfway leaves the stored part as it was.
    """
    body = bytearray()
    try:
        async for piece in request.stream():
            body += piece
            if len(body) > media.CHUNK_SIZE:
                raise AppError(
                    "PAYLOAD_TOO_LARGE",
                    f"Send at most {media.CHUNK_SIZE // (1024 * 1024)} MB per request.",
                    413,
                )
    except ClientDisconnect:
        raise AppError(
            "CLIENT_DISCONNECTED", "The connection closed before the chunk arrived.", 400
        ) from None
    return body


@admin.post("/media/uploads", status_code=201)
async def start_upload(
    body: UploadIn, response: Response, ctx: AdminContext = Depends(current_admin)
) -> dict[str, Any]:
    no_store(response)
    upload_id = await media.start_upload(ctx, body.name, body.size, body.content_type)
    return {"upload_id": upload_id, "chunk_size": media.CHUNK_SIZE}


@admin.put("/media/uploads/{upload_id}")
async def put_chunk(
    upload_id: str,
    request: Request,
    response: Response,
    offset: int = Query(ge=0),
    ctx: AdminContext = Depends(current_admin),
) -> dict[str, Any]:
    no_store(response)
    upload = await media.load_upload(upload_id, ctx)
    received = await media.write_chunk(upload, offset, await _chunk(request))
    return {"received": received}


@admin.post("/media/uploads/{upload_id}/complete", status_code=201)
async def complete_upload(
    upload_id: str,
    response: Response,
    ctx: AdminContext = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    no_store(response)
    upload = await media.load_upload(upload_id, ctx)
    asset = await media.finish_upload(db, upload, ctx)
    return {
        "id": str(asset.id),
        "url": f"/media/{asset.storage_key}",
        "type": media.media_type(asset.content_type),
        "name": asset.original_name,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
    }


@admin.delete("/media/uploads/{upload_id}", status_code=204)
async def abort_upload(upload_id: str, ctx: AdminContext = Depends(current_admin)) -> Response:
    upload = await media.load_upload(upload_id, ctx)
    await media.abort_upload(upload)
    return Response(status_code=204, headers=NO_STORE)
