"""FastAPI application."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import (
    admin_core,
    admin_email,
    admin_events,
    admin_live,
    admin_participants,
    admin_results,
    opening,
    participant,
)
from .config import get_settings
from .db import dispose_engine, init_engine
from .errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .live import routes as live_routes
from .live.hub import LiveHub, set_hub
from .logging import configure_logging, get_logger, request_id_var

log = get_logger("stagevote.http")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_JSON_BYTES = 2_000_000
MAX_UPLOAD_BYTES_SLACK = 64 * 1024


def create_app(*, start_hub: bool = True) -> FastAPI:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        hub = LiveHub(settings.database_url) if start_hub else None
        set_hub(hub)
        if hub:
            await hub.start()
        try:
            yield
        finally:
            if hub:
                await hub.stop()
            set_hub(None)
            await dispose_engine()

    app = FastAPI(
        title=f"{settings.product_name} API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/v1/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/v1/openapi.json",
        # FastAPI's native OpenTelemetry hooks stay off: they could record validation
        # failures that include submitted passwords.
        telemetry={"tracing": False, "metrics": False, "logs": False, "operation_spans": False},
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            length = int(request.headers.get("content-length") or 0)
            limit = (
                settings.max_upload_bytes + MAX_UPLOAD_BYTES_SLACK
                if request.url.path.endswith("/assets")
                else MAX_JSON_BYTES
            )
            if length > limit:
                response: Response = await app_error_handler(
                    request, AppError("PAYLOAD_TOO_LARGE", "The request is too large.", 413)
                )
            else:
                try:
                    response = await call_next(request)
                except Exception as exc:
                    response = await unhandled_error_handler(request, exc)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,  # query strings are never logged
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "request_id": request_id,
            },
        )
        return response

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(admin_core.router)
    app.include_router(admin_core.admin)
    app.include_router(participant.router)
    app.include_router(admin_events.router)
    app.include_router(admin_live.router)
    app.include_router(admin_participants.router)
    app.include_router(admin_results.router)
    app.include_router(admin_email.router)
    app.include_router(opening.router)
    app.include_router(opening.admin)
    app.include_router(live_routes.router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """`stagevote.main:app` is created on first access, after the environment is read."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
