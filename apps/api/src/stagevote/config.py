"""Server configuration.

Every value comes from the environment (or an `.env` file in development). Secrets are
never given defaults that would work in production; `Settings.validate_for_runtime()`
refuses unsafe combinations before the API or worker starts.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
EmailProvider = Literal["mailpit", "smtp", "gmail_workspace", "disabled"]
SmtpTlsMode = Literal["none", "starttls", "tls"]


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: AppEnv = "development"
    app_base_url: str = "http://localhost:3000"
    # Extra browser origins allowed to call the API (comma separated). Leave empty: the
    # proxy keeps everything same-origin.
    extra_allowed_origins: str = ""
    app_secret_key: SecretStr = Field(default=SecretStr(""))
    programme_timezone: str = "Asia/Kolkata"
    product_name: str = "StageVote"

    # Runtime connection uses the restricted application role. Migrations and the
    # bootstrap command use the owner role.
    database_url: str = "postgresql+asyncpg://stagevote_app:stagevote_app@localhost:55432/stagevote"
    database_migration_url: str | None = None
    db_app_role: str = "stagevote_app"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15000
    vote_lock_timeout_ms: int = 5000

    admin_bootstrap_email: str | None = None
    admin_bootstrap_password: SecretStr | None = None

    invitation_encryption_key: SecretStr = Field(default=SecretStr(""))

    session_ttl_hours: float = 12
    admin_session_ttl_hours: float = 12
    # How long the encrypted copy of an emailed password is kept so a failed delivery can be
    # retried with the same password. Afterwards, re-send the invitation (new password).
    invitation_retry_hours: float = 72

    # Rate limits. Only failed attempts count towards the per-IP limit because a
    # whole venue may share one public IP address.
    login_failures_per_account: int = 8
    login_failure_window_seconds: int = 900
    login_failures_per_ip: int = 1000
    login_ip_window_seconds: int = 600

    email_provider: EmailProvider = "mailpit"
    mail_from: str = "StageVote <noreply@example.com>"
    mail_reply_to: str | None = None
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_tls_mode: SmtpTlsMode = "none"
    smtp_timeout_seconds: float = 30
    google_service_account_file: str | None = None
    google_workspace_sender: str | None = None
    email_max_per_minute: int = 120
    email_concurrency: int = 4
    email_max_attempts: int = 6
    # Mailpit's HTTP API, only used by the admin "open test inbox" link in development.
    mailpit_ui_url: str | None = "http://localhost:8025"

    media_root: str = "/data/media"
    max_upload_bytes: int = 5 * 1024 * 1024

    password_hash_concurrency: int = 8
    log_level: str = "INFO"
    worker_id: str | None = None

    @field_validator("programme_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @field_validator("app_base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    # ---- derived values -------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def base_origin(self) -> str:
        parts = urlsplit(self.app_base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def allowed_origins(self) -> set[str]:
        origins = {self.base_origin}
        for item in self.extra_allowed_origins.split(","):
            item = item.strip().rstrip("/")
            if item:
                origins.add(item)
        return origins

    @property
    def cookie_secure(self) -> bool:
        return urlsplit(self.app_base_url).scheme == "https"

    @property
    def participant_cookie_name(self) -> str:
        # The __Host- prefix makes browsers refuse the cookie unless it is Secure,
        # host-only and Path=/. It is only usable over HTTPS.
        return "__Host-sv_session" if self.cookie_secure else "sv_session"

    @property
    def admin_cookie_name(self) -> str:
        return "__Host-sv_admin" if self.cookie_secure else "sv_admin"

    @property
    def migration_url(self) -> str:
        return self.database_migration_url or self.database_url

    @property
    def mail_from_domain(self) -> str:
        address = self.mail_from
        if "<" in address:
            address = address.split("<", 1)[1].rstrip(">")
        return address.rsplit("@", 1)[-1].strip() or "localhost"

    def secret_key_bytes(self) -> bytes:
        return self.app_secret_key.get_secret_value().encode()

    def validate_for_runtime(self) -> None:
        """Refuse configurations that are unsafe for the selected environment."""
        problems: list[str] = []
        secret = self.app_secret_key.get_secret_value()
        if len(secret) < 32:
            problems.append("APP_SECRET_KEY must be at least 32 characters (run scripts/bootstrap.sh)")
        key = self.invitation_encryption_key.get_secret_value()
        try:
            if len(base64.urlsafe_b64decode(key.encode())) != 32:
                raise ValueError
        except ValueError:
            problems.append("INVITATION_ENCRYPTION_KEY must be a Fernet key (32 url-safe base64 bytes)")

        if self.is_production:
            if not self.cookie_secure:
                problems.append("APP_BASE_URL must use https in production")
            if self.email_provider == "mailpit":
                problems.append("EMAIL_PROVIDER=mailpit is for local development only")
            if self.email_provider == "smtp":
                if self.smtp_tls_mode == "none":
                    problems.append("SMTP_TLS_MODE=none is not allowed in production")
                if not self.smtp_username or not self.smtp_password:
                    problems.append("SMTP_USERNAME and SMTP_PASSWORD are required in production")
            if self.email_provider == "gmail_workspace" and (
                not self.google_service_account_file or not self.google_workspace_sender
            ):
                problems.append(
                    "GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_WORKSPACE_SENDER are required "
                    "for EMAIL_PROVIDER=gmail_workspace"
                )
            if "@example.com" in self.mail_from:
                problems.append("MAIL_FROM still uses the example.com placeholder")
        if self.email_provider == "smtp" and self.smtp_tls_mode == "tls" and self.smtp_port == 587:
            problems.append("SMTP_TLS_MODE=tls (implicit TLS) normally uses port 465; port 587 uses starttls")
        if self.email_provider == "smtp" and self.smtp_tls_mode == "starttls" and self.smtp_port == 465:
            problems.append("SMTP port 465 uses implicit TLS: set SMTP_TLS_MODE=tls")
        if problems:
            raise ConfigError("; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
