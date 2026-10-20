"""Password hashing, opaque tokens, CSRF tokens and restricted-payload encryption."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

# Argon2id with OWASP's second recommended profile (19 MiB, t=2, p=1). Memory per hash is
# bounded and hashing runs in worker threads behind a semaphore, so a login burst from a
# full venue cannot exhaust memory or block the event loop.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
_hash_slots: asyncio.Semaphore | None = None

# Unambiguous alphabet: no 0/O, 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _slots() -> asyncio.Semaphore:
    global _hash_slots
    if _hash_slots is None:
        _hash_slots = asyncio.Semaphore(get_settings().password_hash_concurrency)
    return _hash_slots


def hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def verify_password_sync(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    async with _slots():
        return await asyncio.to_thread(hash_password_sync, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification: an unknown account still costs one Argon2 run."""
    async with _slots():
        return await asyncio.to_thread(verify_password_sync, password_hash, password)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def csrf_token_for(session_id: uuid.UUID, scope: str) -> str:
    key = get_settings().secret_key_bytes()
    return hmac.new(key, f"csrf:{scope}:{session_id}".encode(), hashlib.sha256).hexdigest()


def csrf_matches(session_id: uuid.UUID, scope: str, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token_for(session_id, scope), presented)


def random_code(length: int) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def new_user_code() -> str:
    # 31^7 ≈ 2.75e10 combinations; uniqueness is enforced by the database.
    return f"SV-{random_code(7)}"


def new_receipt_code() -> str:
    return f"R-{random_code(4)}-{random_code(4)}"


def new_invitation_password() -> str:
    # Three groups of four from a 31-symbol alphabet: about 59 bits of entropy.
    return "-".join(random_code(4) for _ in range(3))


def request_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _fernet() -> Fernet:
    return Fernet(get_settings().invitation_encryption_key.get_secret_value().encode())


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(payload).encode())


def decrypt_payload(ciphertext: bytes) -> dict[str, Any]:
    try:
        data = json.loads(_fernet().decrypt(ciphertext))
    except InvalidToken as exc:
        raise ValueError("restricted payload cannot be decrypted with the configured key") from exc
    if not isinstance(data, dict):
        raise ValueError("restricted payload is malformed")
    return data


COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "1234567890",
    "12345678910",
    "qwertyuiop",
    "qwerty12345",
    "iloveyou12",
    "letmein123",
    "welcome123",
    "admin12345",
    "stagevote1",
    "stagevote123",
    "changeme123",
}


def password_problem(password: str, *, user_code: str = "", email: str = "") -> str | None:
    """Return a user-facing reason the password is unacceptable, or None."""
    if len(password) < 10:
        return "Use at least 10 characters."
    if len(password) > 128:
        return "Use at most 128 characters."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(password)) < 4:
        return "This password is too easy to guess. Try a short phrase."
    if user_code and user_code.lower() in lowered:
        return "Do not include your user ID in the password."
    local = email.split("@", 1)[0].lower()
    if len(local) >= 4 and local in lowered:
        return "Do not include your email address in the password."
    return None
