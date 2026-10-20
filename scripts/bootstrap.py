#!/usr/bin/env python3
"""Create or complete the local `.env` file. Safe to run repeatedly.

* Copies `.env.example` to `.env` when `.env` does not exist.
* Replaces placeholder values (`<...>` or `change-me`) with generated secrets.
* Never changes a value that is already set, so reruns do not rotate passwords or keys
  and never reset the admin account or the programme.

Standard library only: it runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import socket
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
PLACEHOLDER = re.compile(r"<[^>]*>|^change-me$")
PASSWORD_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0OolI1")


def password(length: int = 20) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def choose_port(preferred: int) -> int:
    for candidate in [preferred, *range(3100, 3200)]:
        if port_free(candidate):
            return candidate
    raise SystemExit("no free HTTP port found between 3100 and 3199")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("STAGEVOTE_HTTP_PORT", "3000")))
    parser.add_argument("--admin-email", default=os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "admin@example.com"))
    args = parser.parse_args()

    if not EXAMPLE_PATH.exists():
        print(".env.example is missing", file=sys.stderr)
        return 1

    created = not ENV_PATH.exists()
    text = ENV_PATH.read_text() if not created else EXAMPLE_PATH.read_text()
    current = parse_env(text)

    db_owner_pw = current.get("DB_OWNER_PASSWORD", "")
    db_app_pw = current.get("DB_APP_PASSWORD", "")
    if PLACEHOLDER.search(db_owner_pw) or not db_owner_pw:
        db_owner_pw = password(28)
    if PLACEHOLDER.search(db_app_pw) or not db_app_pw:
        db_app_pw = password(28)

    http_port = current.get("STAGEVOTE_HTTP_PORT", "")
    if not http_port or PLACEHOLDER.search(http_port) or created:
        http_port = str(choose_port(args.http_port))

    generated = {
        "POSTGRES_SUPERUSER_PASSWORD": password(28),
        "DB_OWNER_PASSWORD": db_owner_pw,
        "DB_APP_PASSWORD": db_app_pw,
        "DATABASE_URL": f"postgresql+asyncpg://stagevote_app:{db_app_pw}@db:5432/stagevote",
        "DATABASE_MIGRATION_URL": f"postgresql+asyncpg://stagevote_owner:{db_owner_pw}@db:5432/stagevote",
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        "INVITATION_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "ADMIN_BOOTSTRAP_EMAIL": args.admin_email,
        "ADMIN_BOOTSTRAP_PASSWORD": password(20),
        "STAGEVOTE_HTTP_PORT": http_port,
        "APP_BASE_URL": f"http://localhost:{http_port}",
    }

    changed: list[str] = []
    out_lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            key = key.strip()
            seen.add(key)
            value = value.strip()
            needs_value = (not value or PLACEHOLDER.search(value)) and key in generated
            # APP_BASE_URL follows the chosen port only when the file is first created.
            if created and key in {"APP_BASE_URL", "STAGEVOTE_HTTP_PORT"}:
                needs_value = True
            if needs_value:
                line = f"{key}={generated[key]}"
                changed.append(key)
        out_lines.append(line)
    for key in ("DB_OWNER_PASSWORD", "DB_APP_PASSWORD", "POSTGRES_SUPERUSER_PASSWORD"):
        if key not in seen:
            out_lines.append(f"{key}={generated[key]}")
            changed.append(key)

    ENV_PATH.write_text("\n".join(out_lines) + "\n")
    os.chmod(ENV_PATH, 0o600)

    final = parse_env(ENV_PATH.read_text())
    if created:
        print(f"Created {ENV_PATH.relative_to(ROOT)} with generated local secrets.")
    elif changed:
        print(f"Filled missing values in .env: {', '.join(sorted(set(changed)))}")
    else:
        print(".env already complete; nothing changed.")
    print(f"App URL:            {final.get('APP_BASE_URL')}")
    print(f"Admin sign-in:      {final.get('APP_BASE_URL')}/admin/login")
    print(f"Admin email:        {final.get('ADMIN_BOOTSTRAP_EMAIL')}")
    print("Admin password:     stored in .env as ADMIN_BOOTSTRAP_PASSWORD (used only to create the")
    print("                    first admin; later changes to it never reset an existing admin)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
