#!/usr/bin/env sh
# Back up the database (custom-format pg_dump) and uploaded media.
# Usage: scripts/backup.sh [compose files...]   e.g. scripts/backup.sh -f compose.prod.yaml
# Output: backups/stagevote-<UTC timestamp>.dump and backups/media-<timestamp>.tgz
# The dump contains personal data (emails) and hashed credentials: store it encrypted.
set -eu
cd "$(dirname "$0")/.."
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups
chmod 700 backups
COMPOSE="docker compose $*"
echo "Dumping database..."
$COMPOSE exec -T db pg_dump -U postgres --format=custom --no-owner --no-privileges stagevote > "backups/stagevote-$STAMP.dump"
echo "Archiving media..."
PROJECT="$($COMPOSE config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' 2>/dev/null || echo stagevote)"
docker run --rm -v "${PROJECT}_media:/media:ro" -v "$PWD/backups:/backup" alpine:3.22 \
  sh -c "tar czf /backup/media-$STAMP.tgz --exclude=./.uploads -C /media . && chown $(id -u):$(id -g) /backup/media-$STAMP.tgz"
chmod 600 "backups/stagevote-$STAMP.dump" "backups/media-$STAMP.tgz"
echo "Backup written:"
ls -l "backups/stagevote-$STAMP.dump" "backups/media-$STAMP.tgz"
echo "Verify with: docker compose exec -T db pg_restore --list < backups/stagevote-$STAMP.dump | head"
