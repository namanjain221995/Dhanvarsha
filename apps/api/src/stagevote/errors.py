"""Machine-readable API errors.

Every error response has the shape::

    {"error": {"code": "VOTING_CLOSED", "message": "...", "request_id": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import get_logger, request_id_var

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}
        self.headers = headers or {}


def unauthenticated(message: str = "Sign in to continue.") -> AppError:
    return AppError("UNAUTHENTICATED", message, 401)


def forbidden(message: str = "You do not have access to this.") -> AppError:
    return AppError("FORBIDDEN", message, 403)


def not_found(what: str = "Resource") -> AppError:
    return AppError("NOT_FOUND", f"{what} not found.", 404)


def conflict(code: str, message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(code, message, 409, details)


def validation(
    message: str, details: dict[str, Any] | None = None, code: str = "VALIDATION_FAILED"
) -> AppError:
    return AppError(code, message, 422, details)


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
            "details": details or {},
        }
    }


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = {"Cache-Control": "no-store", **exc.headers}
    return JSONResponse(
        _body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=headers
    )


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Never echo submitted values back: they may contain passwords.
    fields = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        fields.append({"field": ".".join(loc), "message": err.get("msg", "Invalid value")})
    return JSONResponse(
        _body("VALIDATION_FAILED", "Some fields need attention.", {"fields": fields}),
        status_code=422,
        headers={"Cache-Control": "no-store"},
    )


async def http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    message = {404: "Not found.", 405: "Method not allowed."}.get(exc.status_code, str(exc.detail))
    return JSONResponse(
        _body(code, message), status_code=exc.status_code, headers={"Cache-Control": "no-store"}
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error", extra={"error_type": type(exc).__name__})
    return JSONResponse(
        _body("INTERNAL_ERROR", "Something went wrong on our side. Nothing was saved by this request."),
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )
