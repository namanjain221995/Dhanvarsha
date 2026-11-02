#!/usr/bin/env sh
# The photo gallery under many users at once, against an ISOLATED Compose project with made-up data. It never
# targets the live site or S3.
#
#   tests/load/gallery-run.sh --yes-synthetic-load [--users 200] [--photos 5] [--no-build] [--down]
#
# Prints how long each step took, how fast the worker emptied the queue and how far it scaled.
set -eu
cd "$(dirname "$0")/../.."
ROOT="$PWD"
CONFIRM=""; USERS=200; PHOTOS=5; BUILD="--build"; DOWN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes-synthetic-load) CONFIRM=1 ;;
    --users) USERS="$2"; shift ;;
    --photos) PHOTOS="$2"; shift ;;
    --no-build) BUILD="" ;;
    --down) DOWN=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$CONFIRM" ] || { echo "Refusing to run: pass --yes-synthetic-load." >&2; exit 2; }
PROJECT=stagevote-load
export STAGEVOTE_HTTP_PORT=3190 DB_HOST_PORT=55490 MAILPIT_UI_PORT=8095 MAILPIT_SMTP_PORT=1095 STAGEVOTE_BIND=127.0.0.1
export APP_BASE_URL="http://localhost:3190" APP_ENV=development COMPOSE_PROFILES= API_WORKERS=8
export EMAIL_PROVIDER=mailpit SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_TLS_MODE=none SMTP_USERNAME= SMTP_PASSWORD= \
  GOOGLE_SERVICE_ACCOUNT_FILE= GOOGLE_WORKSPACE_SENDER= MAIL_FROM="StageVote Tests <noreply@example.com>"
export GALLERY_S3_BUCKET= AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY= GALLERY_MODEL_URL=
COMPOSE="docker compose -p $PROJECT -f compose.yaml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="$ROOT/tests/load/results/gallery-$STAMP"
DATA="$RESULTS/data"
mkdir -p "$DATA"; chmod -R 777 "$RESULTS"

$COMPOSE down -v >/dev/null 2>&1 || true
$COMPOSE up -d $BUILD db mailpit migrate api worker gallery-worker web proxy >/dev/null
$COMPOSE restart proxy >/dev/null
i=0
until curl -fsS "$APP_BASE_URL/api/v1/ready" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -gt 90 ] && { echo "stack did not become ready" >&2; exit 1; }
  sleep 2
done

# A 12 megapixel photo of about 3.5 MB, like a phone's, and a selfie, from the public-domain test portrait.
$COMPOSE run --rm --no-deps -T --user 0:0 -v "$ROOT/apps/api/tests/fixtures:/in:ro" -v "$DATA:/data" migrate python - <<'PY'
import numpy as np
from PIL import Image
base = Image.open('/in/portrait-wide.jpg').convert('RGB').resize((4000, 3000), Image.Resampling.BICUBIC)
noise = np.random.default_rng(1).normal(0, 7, (3000, 4000, 3))
Image.fromarray(np.clip(np.asarray(base, dtype=np.float32) + noise, 0, 255).astype('uint8')).save('/data/photo.jpg', quality=85)
Image.open('/in/portrait-selfie.jpg').save('/data/selfie.jpg', quality=90)
PY
ls -la "$DATA" | awk 'NR>3{print "  "$9, $5, "bytes"}'

$COMPOSE run --rm --no-deps -T migrate python - 300 3000 5000 30000 < tests/load/gallery-seed.py

( while :; do
    Q=$($COMPOSE exec -T db psql -U postgres -d stagevote -Atc "select count(*) filter (where status in ('queued','processing')), count(*) filter (where status='ready'), count(*) filter (where status='failed') from gallery_media" 2>/dev/null)
    S=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep -E "^$PROJECT-(gallery-worker|api|db)-1" | awk '{print $1"="$2"/"$3}' | sed "s/$PROJECT-//;s/-1=/=/" | tr '\n' ' ')
    echo "$(date -u +%H:%M:%S) $Q $S"
    sleep 5
  done ) > "$RESULTS/queue.log" 2>/dev/null &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

STARTED=$(date +%s)
docker run --rm --network "${PROJECT}_default" --ulimit nofile=65536:65536 \
  -v "$ROOT/tests/load:/scripts:ro" -v "$RESULTS:/out" -v "$DATA:/data:ro" \
  -e TARGET=http://proxy -e ORIGIN="$APP_BASE_URL" -e USERS="$USERS" -e PHOTOS="$PHOTOS" \
  grafana/k6:1.8.2 run --quiet /scripts/gallery.js 2>&1 | tee "$RESULTS/k6.log" | grep -v -E '^time=.*level=(info)' | tail -24
USERS_DONE=$(date +%s)
echo "== users finished in $((USERS_DONE - STARTED)) s; waiting for the worker to empty its queue"
i=0
while :; do
  LEFT=$($COMPOSE exec -T db psql -U postgres -d stagevote -Atc "select count(*) from gallery_media where status in ('queued','processing')")
  [ "$LEFT" = "0" ] && break
  i=$((i + 1)); [ $i -gt 720 ] && { echo "queue still has $LEFT after an hour"; break; }
  sleep 5
done
DRAINED=$(date +%s)
kill $SAMPLER 2>/dev/null || true
$COMPOSE exec -T db psql -U postgres -d stagevote -At <<'SQL'
select 'files read by the worker', count(*) filter (where status = 'ready'), 'failed', count(*) filter (where status = 'failed') from gallery_media where original_name like 'user%';
select 'seconds per file (reading alone)', round(avg((analysis->>'took_ms')::numeric)/1000, 2), 'slowest', round(max((analysis->>'took_ms')::numeric)/1000, 2) from gallery_media where analysis ? 'took_ms' and original_name like 'user%';
SQL
READ=$($COMPOSE exec -T db psql -U postgres -d stagevote -Atc "select count(*) from gallery_media where status='ready' and original_name like 'user%'")
echo "queue empty $((DRAINED - USERS_DONE)) s after the users; $READ files in $((DRAINED - STARTED)) s = $(python3 -c "print(round($READ/max($DRAINED-$STARTED,1),2))") files/s overall"
echo "== how the worker scaled"
$COMPOSE logs gallery-worker 2>&1 | grep -E 'gallery worker scaled' | sed -E 's/.*"reading_at_once": ([0-9]+), "waiting": ([0-9]+).*/reading at once \1 (queue \2)/' | awk '!seen[$0]++' | head -12
echo "== busiest moments (from $RESULTS/queue.log)"
sort -t= -k2 -rn "$RESULTS/queue.log" | head -1 | cut -c1-200
awk 'NR%12==1' "$RESULTS/queue.log" | cut -c1-170 | head -14
[ -z "$DOWN" ] || { $COMPOSE down -v >/dev/null; echo "isolated stack removed"; }
