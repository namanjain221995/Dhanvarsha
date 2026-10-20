#!/usr/bin/env sh
# Browser checks against an ISOLATED Compose project (own database and Mailpit inbox).
#
#   tests/e2e/run.sh [--no-build] [--down] [playwright args...]
#   e.g. tests/e2e/run.sh --project=mobile-chromium specs/journey.spec.ts
#
# The stack listens on http://localhost:3180 (Mailpit API on :8085). Playwright runs in the
# official container image (Chromium, WebKit). Reports land in tests/e2e/playwright-report.
set -eu
cd "$(dirname "$0")/../.."
ROOT="$PWD"
BUILD="--build"; DOWN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD="" ;;
    --down) DOWN=1 ;;
    *) break ;;
  esac
  shift
done
[ -f .env ] || { echo "Run scripts/bootstrap.sh first" >&2; exit 1; }
PROJECT=stagevote-e2e
export STAGEVOTE_HTTP_PORT=3180 DB_HOST_PORT=55480 MAILPIT_UI_PORT=8085 MAILPIT_SMTP_PORT=1085 STAGEVOTE_BIND=127.0.0.1
export APP_BASE_URL="http://localhost:${STAGEVOTE_HTTP_PORT}" MAILPIT_UI_URL="http://localhost:8085"
# Isolated test stacks always deliver to their own Mailpit, whatever .env configures for
# the main stack (a real provider must never receive synthetic test addresses).
export EMAIL_PROVIDER=mailpit SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_TLS_MODE=none SMTP_USERNAME= SMTP_PASSWORD= \
  GOOGLE_SERVICE_ACCOUNT_FILE= GOOGLE_WORKSPACE_SENDER= MAIL_FROM="StageVote Tests <noreply@example.com>"
COMPOSE="docker compose -p $PROJECT -f compose.yaml"

$COMPOSE up -d $BUILD db mailpit migrate api worker web proxy >/dev/null
i=0
until curl -fsS "$APP_BASE_URL/api/v1/ready" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -gt 90 ] && { echo "stack did not become ready" >&2; exit 1; }
  sleep 2
done
# Demo content is only added to a pristine programme; later runs reuse it.
$COMPOSE run --rm --no-deps migrate stagevote seed-demo --participants 10 >/dev/null 2>&1 || true

ADMIN_EMAIL="$(grep '^ADMIN_BOOTSTRAP_EMAIL=' .env | cut -d= -f2-)"
ADMIN_PASSWORD="$(grep '^ADMIN_BOOTSTRAP_PASSWORD=' .env | cut -d= -f2-)"
set +e
docker run --rm --network host --ipc=host -v "$ROOT:/work" -w /work/tests/e2e \
  -e E2E_BASE_URL="$APP_BASE_URL" -e MAILPIT_URL="http://localhost:8085" \
  -e ADMIN_EMAIL="$ADMIN_EMAIL" -e ADMIN_PASSWORD="$ADMIN_PASSWORD" -e CI=1 \
  mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test "$@"
STATUS=$?
set -e
[ -n "$DOWN" ] && $COMPOSE down -v >/dev/null
exit $STATUS
