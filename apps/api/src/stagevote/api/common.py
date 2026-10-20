"""Shared helpers for routers."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from ..config import get_settings

NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


# Names and titles end up in email headers and single-line UI: no control characters.
SingleLine = Annotated[str, StringConstraints(pattern=r"^[^\x00-\x1f\x7f]*$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def no_store(response: Response) -> None:
    for key, value in NO_STORE.items():
        response.headers[key] = value


def set_session_cookie(response: Response, name: str, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    response.set_cookie(
        name,
        token,
        expires=expires_at,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, name: str) -> None:
    settings = get_settings()
    response.delete_cookie(name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
