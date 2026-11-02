#!/usr/bin/env sh
# A private preview of this checkout on http://localhost:3100, beside the live site.
#
#   scripts/preview.sh              build and start it
#   scripts/preview.sh --no-build   start it from the images already built
#   scripts/preview.sh --down       stop it and delete its data
#
# Its own Compose project: its own database and media, and email goes to its own inbox at
# http://localhost:8026, whatever .env says. The live site is not touched. The vision model is on,
# because it runs on this machine and sees only small crops of faces.
set -eu
cd "$(dirname "$0")/.."
PROJECT=stagevote-preview
export STAGEVOTE_HTTP_PORT=3100 DB_HOST_PORT=55433 MAILPIT_UI_PORT=8026 MAILPIT_SMTP_PORT=1026 STAGEVOTE_BIND=127.0.0.1
export APP_ENV=development COMPOSE_PROFILES= APP_BASE_URL="http://localhost:3100" MAILPIT_UI_URL="http://localhost:8026"
export EMAIL_PROVIDER=mailpit SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_TLS_MODE=none SMTP_USERNAME= SMTP_PASSWORD= \
  GOOGLE_SERVICE_ACCOUNT_FILE= GOOGLE_WORKSPACE_SENDER= MAIL_FROM="Dhanvarsha preview <noreply@example.com>"
export GALLERY_MODEL_URL="${GALLERY_MODEL_URL:-http://host.docker.internal:8000/v1}"
# Same bucket as the live site if .env sets one, under its own prefix, so the two never meet.
export GALLERY_S3_PREFIX=preview/
COMPOSE="docker compose -p $PROJECT -f compose.yaml"
BUILD="--build"
case "${1:-}" in
  --down) $COMPOSE down -v; exit 0 ;;
  --no-build) BUILD="" ;;
esac
$COMPOSE up -d $BUILD db mailpit migrate api worker gallery-worker web proxy
$COMPOSE restart proxy # Caddy reads infra/caddy once, and "up" leaves a running proxy alone
i=0
until curl -fsS http://localhost:3100/api/v1/ready >/dev/null 2>&1; do
  i=$((i + 1))
  [ $i -gt 90 ] && { echo "The preview did not become ready. Try: $COMPOSE logs --tail 50 api" >&2; exit 1; }
  sleep 2
done
echo "Preview: http://localhost:3100/gallery  (inbox: http://localhost:8026)"
