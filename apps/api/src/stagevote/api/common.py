"""Shared helpers for routers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from ..config import get_settings

NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


# Names and titles end up in email headers and single-line UI: no control characters.
SingleLine = Annotated[str, StringConstraints(pattern=r"^[^\x00-\x1f\x7f]*$")]


def _newlines(value: Any) -> Any:
    return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value


# Paragraph text: line breaks and tabs allowed, other control characters refused.
MultiLine = Annotated[
    str, BeforeValidator(_newlines), StringConstraints(pattern=r"^[^\x00-\x08\x0b-\x1f\x7f]*$")
]


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


def set_device_cookie(response: Response, token: str) -> None:
    """The long-lived cookie naming this browser for sign-in links. Same flags as the
    session cookie; nothing ever clears it, so signing out keeps the browser's link."""
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(days=settings.device_cookie_days)
    set_session_cookie(response, settings.participant_device_cookie_name, token, expires)


def clear_session_cookie(response: Response, name: str) -> None:
    settings = get_settings()
    response.delete_cookie(name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
