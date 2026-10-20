#!/usr/bin/env sh
# Idempotent local setup: creates/completes .env with generated secrets.
# Usage: scripts/bootstrap.sh [--http-port 3000] [--admin-email you@example.com]
set -eu
cd "$(dirname "$0")/.."
exec python3 scripts/bootstrap.py "$@"
