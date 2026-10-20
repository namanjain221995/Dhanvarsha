"""Email providers.

Outcomes are classified honestly:

* accepted         the provider confirmed it took responsibility for the message. This is
                   not proof of inbox delivery.
* transient_error  safe to retry later (connection refused, 4xx, rate limits).
* permanent_error  retrying without an admin correction is pointless (bad credentials,
                   rejected recipient, missing delegation).
* unknown          the connection failed after the message body was handed over, so the
                   provider may or may not have accepted it. Never retried automatically.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
from typing import Any, Literal, Protocol

import aiosmtplib

from ..config import Settings

Outcome = Literal["accepted", "transient_error", "permanent_error", "unknown"]
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


@dataclass
class InlineImage:
    """An image shown inside the HTML part, referenced as `cid:<cid>`."""

    cid: str
    data: bytes
    subtype: str = "png"


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    message_id: str
    headers: dict[str, str] = field(default_factory=dict)
    inline_images: list[InlineImage] = field(default_factory=list)


@dataclass
class SendResult:
    outcome: Outcome
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    config_error: bool = False


@dataclass
class Check:
    name: str
    status: Literal["ok", "warn", "error", "skipped"]
    detail: str

    def public(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


class Provider(Protocol):
    name: str

    async def send(self, message: OutgoingEmail) -> SendResult: ...

    async def diagnose(self) -> list[Check]: ...


def build_mime(settings: Settings, message: OutgoingEmail) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = settings.mail_from
    mime["To"] = message.to  # one recipient per message: the list is never exposed
    mime["Subject"] = message.subject
    mime["Message-ID"] = message.message_id  # stable across retries of the same job
    mime["Date"] = formatdate(localtime=False, usegmt=True)
    mime["Auto-Submitted"] = "auto-generated"
    if settings.mail_reply_to:
        mime["Reply-To"] = settings.mail_reply_to
    for key, value in message.headers.items():
        mime[key] = value
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    html_part = mime.get_body(preferencelist=("html",))
    if message.inline_images and html_part is not None:
        # multipart/alternative [text/plain, multipart/related [text/html, image...]]
        for image in message.inline_images:
            html_part.add_related(
                image.data,
                maintype="image",
                subtype=image.subtype,
                cid=f"<{image.cid}>",
                disposition="inline",
                filename=f"logo.{image.subtype}",
            )
    return mime


def _truncate(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class SmtpProvider:
    """SMTP with explicit stages so a failure after DATA is reported as `unknown`."""

    def __init__(self, settings: Settings, *, name: str = "smtp") -> None:
        self.settings = settings
        self.name = name

    def _client(self) -> aiosmtplib.SMTP:
        s = self.settings
        return aiosmtplib.SMTP(
            hostname=s.smtp_host,
            port=s.smtp_port,
            use_tls=s.smtp_tls_mode == "tls",
            start_tls=False,
            timeout=s.smtp_timeout_seconds,
            validate_certs=True,
        )

    async def _open(self, client: aiosmtplib.SMTP) -> None:
        s = self.settings
        await client.connect()
        if s.smtp_tls_mode == "starttls":
            await client.starttls()
        if s.smtp_username and s.smtp_password:
            username, password = s.smtp_username, s.smtp_password.get_secret_value()
            if not client.supports_extension("auth"):
                await client.ehlo()
            # Use one explicit mechanism. Falling back through several (login()) lets a
            # server such as Gmail answer 535 and then drop the connection, which would hide
            # the real "username and password not accepted" error behind a disconnect.
            methods = client.supported_auth_methods
            if "plain" in methods:
                await client.auth_plain(username, password)
            elif "login" in methods:
                await client.auth_login(username, password)
            else:
                await client.login(username, password)

    async def send(self, message: OutgoingEmail) -> SendResult:
        mime = build_mime(self.settings, message)
        sender = parseaddr(self.settings.mail_from)[1]
        client = self._client()
        stage = "connect"
        try:
            await self._open(client)
            stage = "mail"
            await client.mail(sender)
            stage = "rcpt"
            await client.rcpt(message.to)
            stage = "data"
            response = await client.data(mime.as_bytes())
            return SendResult("accepted", provider_message_id=_truncate(response.message or "", 200) or None)
        except aiosmtplib.SMTPAuthenticationError as exc:
            return SendResult(
                "permanent_error",
                error_code="smtp_auth_failed",
                error_detail=_truncate(f"{exc.code} authentication failed"),
                config_error=True,
            )
        except aiosmtplib.SMTPResponseException as exc:
            code = int(exc.code)
            detail = _truncate(f"{stage}: {code} {exc.message}")
            if 400 <= code < 500:
                return SendResult("transient_error", error_code=f"smtp_{code}", error_detail=detail)
            config = stage in ("connect", "mail")
            return SendResult(
                "permanent_error", error_code=f"smtp_{code}", error_detail=detail, config_error=config
            )
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            # Connection errors carry the reason (DNS, refused, unreachable, timeout) and never
            # the credentials, which only travel inside AUTH (handled above).
            reason = " ".join(str(exc).split())
            detail = _truncate(f"{stage}: {type(exc).__name__}" + (f": {reason}" if reason else ""))
            if stage == "data":
                return SendResult("unknown", error_code="smtp_data_interrupted", error_detail=detail)
            return SendResult("transient_error", error_code="smtp_connection", error_detail=detail)
        finally:
            if client.is_connected:
                try:
                    await client.quit()
                except Exception:
                    client.close()

    async def diagnose(self) -> list[Check]:
        s = self.settings
        checks = [
            Check(
                "provider", "ok", f"{self.name} via {s.smtp_host}:{s.smtp_port} (TLS mode: {s.smtp_tls_mode})"
            )
        ]
        if s.smtp_tls_mode == "none":
            checks.append(
                Check(
                    "transport security",
                    "warn" if not s.is_production else "error",
                    "Unencrypted SMTP. Acceptable only for the local Mailpit inbox.",
                )
            )
        client = self._client()
        try:
            await self._open(client)
            auth = "authenticated" if s.smtp_username else "no authentication configured"
            checks.append(Check("connection", "ok", f"Connected and {auth}. No message was sent."))
        except aiosmtplib.SMTPAuthenticationError as exc:
            hint = ""
            if "gmail" in s.smtp_host or "google" in s.smtp_host:
                hint = (
                    " Google accepts only a 16-letter App Password here (myaccount.google.com/apppasswords, "
                    "requires 2-Step Verification), not the normal account password."
                )
            reply = " ".join(str(exc.message).split())[:160]
            checks.append(
                Check(
                    "connection",
                    "error",
                    f"The server rejected the SMTP username or password ({exc.code} {reply}).{hint}",
                )
            )
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            checks.append(Check("connection", "error", f"Could not connect: {type(exc).__name__}"))
        finally:
            if client.is_connected:
                try:
                    await client.quit()
                except Exception:
                    client.close()
        return checks


class GmailWorkspaceProvider:
    """Gmail API with a service account and domain-wide delegation (gmail.send only)."""

    name = "gmail_workspace"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._creds: Any = None
        self._creds_lock = threading.Lock()

    def _credentials(self) -> Any:
        from google.oauth2 import service_account

        path = self.settings.google_service_account_file
        if not path:
            raise FileNotFoundError("GOOGLE_SERVICE_ACCOUNT_FILE is not set")
        return service_account.Credentials.from_service_account_file(
            path, scopes=[GMAIL_SEND_SCOPE], subject=self.settings.google_workspace_sender
        )

    def _shared_credentials(self) -> Any:
        """One credentials object; token refreshes are serialised by the lock."""
        with self._creds_lock:
            if self._creds is None:
                self._creds = self._credentials()
            if not self._creds.valid:
                from google.auth.transport.requests import Request

                self._creds.refresh(Request())
            return self._creds

    def _send_sync(self, raw: str) -> dict[str, Any]:
        # httplib2 connections are not thread-safe: every send gets its own HTTP object, so
        # concurrent sends can never read each other's responses.
        import google_auth_httplib2
        import httplib2
        from googleapiclient.discovery import build

        http = google_auth_httplib2.AuthorizedHttp(self._shared_credentials(), http=httplib2.Http(timeout=30))
        service = build("gmail", "v1", http=http, cache_discovery=False, static_discovery=True)
        result: dict[str, Any] = (
            service.users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=0)
        )
        return result

    async def send(self, message: OutgoingEmail) -> SendResult:
        from google.auth.exceptions import RefreshError, TransportError
        from googleapiclient.errors import HttpError

        raw = base64.urlsafe_b64encode(build_mime(self.settings, message).as_bytes()).decode()
        try:
            result = await asyncio.to_thread(self._send_sync, raw)
            return SendResult("accepted", provider_message_id=str(result.get("id") or "") or None)
        except RefreshError as exc:
            # unauthorized_client: delegation missing for this client ID/scope;
            # invalid_grant: bad sender mailbox, disabled or revoked key.
            return SendResult(
                "permanent_error",
                error_code="google_auth",
                config_error=True,
                error_detail=_truncate(f"Token exchange failed: {exc.args[0] if exc.args else exc}"),
            )
        except FileNotFoundError as exc:
            return SendResult(
                "permanent_error",
                error_code="google_credentials_missing",
                config_error=True,
                error_detail=_truncate(str(exc)),
            )
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            reason = ""
            try:
                payload = json.loads(exc.content.decode())
                reason = payload.get("error", {}).get("errors", [{}])[0].get("reason", "")
            except (ValueError, AttributeError, IndexError):
                pass
            detail = _truncate(f"HTTP {status} {reason}".strip())
            if status == 429 or reason in {
                "rateLimitExceeded",
                "userRateLimitExceeded",
                "dailyLimitExceeded",
            }:
                return SendResult("transient_error", error_code="google_rate_limited", error_detail=detail)
            if status >= 500:
                return SendResult("transient_error", error_code=f"google_{status}", error_detail=detail)
            if status in (401, 403):
                return SendResult(
                    "permanent_error", error_code=f"google_{status}", error_detail=detail, config_error=True
                )
            return SendResult("permanent_error", error_code=f"google_{status}", error_detail=detail)
        except (TimeoutError, ConnectionError, TransportError, OSError) as exc:
            # The request may have reached Google before the connection failed.
            return SendResult(
                "unknown", error_code="google_connection", error_detail=_truncate(type(exc).__name__)
            )

    async def diagnose(self) -> list[Check]:
        s = self.settings
        checks: list[Check] = []
        path = s.google_service_account_file
        if not path:
            return [Check("credentials file", "error", "GOOGLE_SERVICE_ACCOUNT_FILE is not set.")]
        loaded = await asyncio.to_thread(_read_key_file, path)
        if isinstance(loaded, str):
            return [Check("credentials file", "error", loaded)]
        info, loose_permissions = loaded
        if info.get("type") != "service_account":
            return [Check("credentials file", "error", "The JSON is not a service-account key.")]
        client_email = str(info.get("client_email", ""))
        checks.append(
            Check(
                "credentials file",
                "ok",
                f"Service-account key for {client_email} (client ID {info.get('client_id', '?')}).",
            )
        )
        if loose_permissions:
            checks.append(
                Check(
                    "file permissions",
                    "warn",
                    "The key file is readable by other users; mount it read-only, mode 0400.",
                )
            )
        sender = s.google_workspace_sender
        if not sender:
            checks.append(Check("sender mailbox", "error", "GOOGLE_WORKSPACE_SENDER is not set."))
            return checks
        if sender.endswith("gserviceaccount.com"):
            checks.append(
                Check(
                    "sender mailbox",
                    "error",
                    "The sender must be a Workspace mailbox, not the service account.",
                )
            )
            return checks
        checks.append(Check("sender mailbox", "ok", f"Impersonating {sender}."))
        from_address = parseaddr(s.mail_from)[1]
        if from_address.lower() != sender.lower():
            checks.append(
                Check(
                    "from address",
                    "warn",
                    f"MAIL_FROM uses {from_address}; it must be {sender} or a verified send-as alias of it.",
                )
            )

        def exchange() -> None:
            from google.auth.transport.requests import Request

            creds = self._credentials()
            creds.refresh(Request())

        try:
            await asyncio.to_thread(exchange)
            checks.append(
                Check(
                    "delegated token",
                    "ok",
                    "Google issued a gmail.send token for the sender. Delivery is not verified until a test "
                    "message is accepted.",
                )
            )
        except Exception as exc:
            message = str(exc.args[0]) if getattr(exc, "args", None) else type(exc).__name__
            hint = ""
            if "unauthorized_client" in message:
                hint = (
                    " Authorise the service account's client ID for scope "
                    f"{GMAIL_SEND_SCOPE} in Admin console > Security > API controls > Domain-wide delegation."
                )
            checks.append(
                Check("delegated token", "error", _truncate(f"Token exchange failed: {message}.{hint}", 400))
            )
        return checks


def _read_key_file(path: str) -> tuple[dict[str, Any], bool] | str:
    """Return (parsed JSON, permissions too open) or an error message. Never logs content."""
    if not os.path.isfile(path):
        return f"No file at {path} inside the container."
    try:
        with open(path, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return "The file is not readable JSON."
    if not isinstance(info, dict):
        return "The file is not a service-account key."
    return info, bool(os.stat(path).st_mode & 0o077)


class DisabledProvider:
    name = "disabled"

    async def send(self, message: OutgoingEmail) -> SendResult:
        return SendResult(
            "permanent_error",
            error_code="email_disabled",
            config_error=True,
            error_detail="EMAIL_PROVIDER=disabled",
        )

    async def diagnose(self) -> list[Check]:
        return [
            Check(
                "provider",
                "warn",
                "Email is disabled. Invitations stay queued until a provider is configured.",
            )
        ]


class RecordingProvider:
    """In-memory provider for automated tests; can be scripted with outcomes."""

    name = "recording"

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.script: list[SendResult] = []

    async def send(self, message: OutgoingEmail) -> SendResult:
        result = (
            self.script.pop(0)
            if self.script
            else SendResult("accepted", provider_message_id=f"rec-{len(self.sent)}")
        )
        if result.outcome in ("accepted", "unknown"):
            self.sent.append(message)
        return result

    async def diagnose(self) -> list[Check]:
        return [Check("provider", "ok", "Recording provider (tests).")]


def make_provider(settings: Settings) -> Provider:
    if settings.email_provider in ("mailpit", "smtp"):
        return SmtpProvider(settings, name=settings.email_provider)
    if settings.email_provider == "gmail_workspace":
        return GmailWorkspaceProvider(settings)
    return DisabledProvider()
