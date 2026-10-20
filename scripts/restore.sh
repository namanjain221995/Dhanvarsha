#!/usr/bin/env sh
# Restore a backup made by scripts/backup.sh into the running Compose stack.
#
#   scripts/restore.sh --yes-replace-database backups/stagevote-<stamp>.dump [backups/media-<stamp>.tgz] [-- compose args]
#
# DESTRUCTIVE: the current `stagevote` database is dropped and replaced. The API and worker
# are stopped during the restore and started again afterwards. Take a fresh backup first.
set -eu
cd "$(dirname "$0")/.."
if [ "${1:-}" != "--yes-replace-database" ]; then
  echo "Refusing to run without --yes-replace-database (this replaces the live database)." >&2
  exit 2
fi
shift
DUMP="${1:?path to .dump file}"; shift
MEDIA=""
if [ "${1:-}" != "" ] && [ "${1:-}" != "--" ]; then MEDIA="$1"; shift; fi
[ "${1:-}" = "--" ] && shift
COMPOSE="docker compose $*"
[ -f "$DUMP" ] || { echo "No such dump: $DUMP" >&2; exit 1; }

echo "Stopping api and worker..."
$COMPOSE stop api worker
echo "Recreating database..."
$COMPOSE exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d postgres <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'stagevote' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS stagevote;
CREATE DATABASE stagevote OWNER stagevote_owner;
SQL
$COMPOSE exec -T db psql -U postgres -v ON_ERROR_STOP=1 -d stagevote <<'SQL'
ALTER SCHEMA public OWNER TO stagevote_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO stagevote_app;
SQL
echo "Restoring data as the owner role..."
$COMPOSE exec -T db pg_restore -U postgres --role=stagevote_owner --no-owner --no-privileges --exit-on-error -d stagevote < "$DUMP"
if [ -n "$MEDIA" ]; then
  PROJECT="$($COMPOSE config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' 2>/dev/null || echo stagevote)"
  echo "Restoring media into ${PROJECT}_media..."
  docker run --rm -v "${PROJECT}_media:/media" -v "$PWD/$(dirname "$MEDIA"):/backup:ro" alpine:3.22 \
    sh -c "tar xzf /backup/$(basename "$MEDIA") -C /media && chown -R 10001:10001 /media"
fi
echo "Re-applying migrations and runtime grants..."
$COMPOSE run --rm migrate
echo "Starting api and worker..."
$COMPOSE up -d api worker
$COMPOSE exec -T api stagevote check-privileges
echo "Restore complete. Run 'docker compose exec api stagevote reconcile' to verify vote totals."
