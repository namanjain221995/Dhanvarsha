#!/usr/bin/env sh
# Source this from anywhere inside the repository to run the API, worker or tests directly
# on the host against the Compose database (published on 127.0.0.1:${DB_HOST_PORT}):
#   . scripts/host-env.sh && cd apps/api && uv run stagevote bootstrap
_sv_dir="$PWD"
while [ "$_sv_dir" != "/" ] && [ ! -f "$_sv_dir/.env" ]; do _sv_dir="$(dirname "$_sv_dir")"; done
if [ ! -f "$_sv_dir/.env" ]; then echo "No .env found; run scripts/bootstrap.sh first" >&2; return 1 2>/dev/null || exit 1; fi
set -a
. "$_sv_dir/.env"
set +a
STAGEVOTE_ROOT="$_sv_dir"
DB_HOST_PORT="${DB_HOST_PORT:-55432}"
export STAGEVOTE_ROOT
export DATABASE_URL="postgresql+asyncpg://stagevote_app:${DB_APP_PASSWORD}@127.0.0.1:${DB_HOST_PORT}/stagevote"
export DATABASE_MIGRATION_URL="postgresql+asyncpg://stagevote_owner:${DB_OWNER_PASSWORD}@127.0.0.1:${DB_HOST_PORT}/stagevote"
export TEST_SUPERUSER_URL="postgresql+asyncpg://postgres:${POSTGRES_SUPERUSER_PASSWORD}@127.0.0.1:${DB_HOST_PORT}/postgres"
export SMTP_HOST=127.0.0.1
export SMTP_PORT="${MAILPIT_SMTP_PORT:-1025}"
export MEDIA_ROOT="${MEDIA_ROOT_HOST:-$_sv_dir/media}"
unset _sv_dir
