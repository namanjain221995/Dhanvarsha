#!/usr/bin/env sh
# Reproducible StageVote load test against an ISOLATED Compose project with synthetic
# accounts. It never targets the main stack or any remote host.
#
# API stress test (how much can it handle?):
#   tests/load/run.sh --yes-synthetic-load --users 500 --requests 10000 [--think-ms 0]
#     USERS signed-in voters share REQUESTS API calls, sent as fast as the server answers.
#
# Live event simulation (realistic voting rounds with WebSockets):
#   tests/load/run.sh --yes-synthetic-load [--voters 500] [--rounds 3] [--burst 10]
#                     [--round-seconds 40] [--login-ramp 30]
#
# Common options: [--api-workers 6] [--no-build] [--down]
#
# Steps: start project "stagevote-load" (own volumes, ports 3190/55490), seed a fresh
# load-test programme, run k6 (grafana/k6) inside the project network, sample container
# CPU/RAM, reconcile stored votes against acknowledged ones, and write an HTML report:
#   tests/load/results/<run>/report.html   (all runs: tests/load/results/index.html)
# View reports in a browser with tests/load/serve.sh.
set -eu
cd "$(dirname "$0")/../.."
ROOT="$PWD"

CONFIRM=""; BUILD="--build"; VOTERS=500; ROUNDS=3; BURST=10; ROUND_SECONDS=40; LOGIN_RAMP=30; API_WORKERS=6; DOWN=""
MODE=event; REQUESTS=""; THINK_MS=0; MAX_MINUTES=30
while [ $# -gt 0 ]; do
  case "$1" in
    --yes-synthetic-load) CONFIRM=1 ;;
    --voters|--users) VOTERS="$2"; shift ;;
    --requests) REQUESTS="$2"; MODE=api; shift ;;
    --think-ms) THINK_MS="$2"; shift ;;
    --max-minutes) MAX_MINUTES="$2"; shift ;;
    --rounds) ROUNDS="$2"; shift ;;
    --burst) BURST="$2"; shift ;;
    --round-seconds) ROUND_SECONDS="$2"; shift ;;
    --login-ramp) LOGIN_RAMP="$2"; shift ;;
    --api-workers) API_WORKERS="$2"; shift ;;
    --down) DOWN=1 ;;
    --no-build) BUILD="" ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -z "$CONFIRM" ]; then
  echo "Refusing to run: pass --yes-synthetic-load to confirm an isolated synthetic load test." >&2
  exit 2
fi
case "$VOTERS$REQUESTS$THINK_MS$API_WORKERS" in *[!0-9]*) echo "numbers only for --users, --requests, --think-ms, --api-workers" >&2; exit 2 ;; esac
if [ "$MODE" = api ] && [ "$REQUESTS" -lt "$VOTERS" ]; then
  echo "--requests must be at least --users (every user signs in once)" >&2; exit 2
fi
[ -f .env ] || { echo "Run scripts/bootstrap.sh first" >&2; exit 1; }

PROJECT=stagevote-load
export STAGEVOTE_HTTP_PORT=3190 DB_HOST_PORT=55490 MAILPIT_UI_PORT=8095 MAILPIT_SMTP_PORT=1095 STAGEVOTE_BIND=127.0.0.1
export APP_BASE_URL="http://localhost:${STAGEVOTE_HTTP_PORT}"
# Isolated test stacks always deliver to their own Mailpit, whatever .env configures for
# the main stack (a real provider must never receive synthetic test addresses).
export EMAIL_PROVIDER=mailpit SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_TLS_MODE=none SMTP_USERNAME= SMTP_PASSWORD= \
  GOOGLE_SERVICE_ACCOUNT_FILE= GOOGLE_WORKSPACE_SENDER= MAIL_FROM="StageVote Tests <noreply@example.com>"
# 500 users behind one test machine share one IP address; failed sign-ins are still limited per account.
export LOGIN_FAILURES_PER_IP=100000
# The test copy is plain HTTP on localhost, even on a production server whose .env says production.
export APP_ENV=development
# Its own organiser account, independent of the real one (whose bootstrap password is removed
# from .env after the first sign-in on a production server).
LOAD_ADMIN_EMAIL=loadtest-admin@example.com
LOAD_ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
export ADMIN_BOOTSTRAP_EMAIL="$LOAD_ADMIN_EMAIL" ADMIN_BOOTSTRAP_PASSWORD="$LOAD_ADMIN_PASSWORD"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="$ROOT/tests/load/results/$STAMP"
mkdir -p "$RESULTS"
chmod 777 "$RESULTS"
COMPOSE="docker compose -p $PROJECT -f compose.yaml"
export API_WORKERS

echo "== starting isolated stack ($PROJECT on $APP_BASE_URL, $API_WORKERS API workers)"
$COMPOSE up -d $BUILD db mailpit migrate api worker web proxy >/dev/null
i=0
until curl -fsS "$APP_BASE_URL/api/v1/ready" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -gt 90 ] && { echo "stack did not become ready" >&2; exit 1; }
  sleep 2
done

if [ "$MODE" = api ]; then EVENTS=2; else EVENTS=$((ROUNDS + 1)); fi
LOADTEST_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
export LOADTEST_PASSWORD
echo "== seeding $VOTERS synthetic participants and $EVENTS rounds"
$COMPOSE run --rm --no-deps --user 0:0 -e LOADTEST_PASSWORD -e LOADTEST_ACCOUNTS_FILE=/out/accounts.txt \
  -v "$RESULTS:/out" migrate stagevote seed-loadtest --participants "$VOTERS" --events "$EVENTS" --yes-synthetic

# Set this run's password on the test organiser (creates it the first time).
$COMPOSE run --rm --no-deps -e STAGEVOTE_NEW_ADMIN_PASSWORD="$LOAD_ADMIN_PASSWORD" migrate \
  stagevote create-admin --email "$LOAD_ADMIN_EMAIL" --reset-password >/dev/null

echo "== sampling container CPU/RAM"
( while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep "^$PROJECT" | sed "s/^/$(date -u +%H:%M:%S),/"
    sleep 1
  done ) > "$RESULTS/docker-stats.csv" 2>/dev/null &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

if [ "$MODE" = api ]; then
  SCRIPT=api.js; COUNTER=votes_accepted
  echo "== running k6 API stress test: $VOTERS users, $REQUESTS requests, ${THINK_MS} ms pause"
else
  SCRIPT=stagevote.js; COUNTER=ballots_confirmed
  echo "== running k6 live event simulation: $VOTERS voters, $ROUNDS rounds, ${BURST}s vote burst"
fi
STARTED=$(date -u +%s)
set +e
{ docker run --rm --network "${PROJECT}_default" --ulimit nofile=65536:65536 \
  -v "$ROOT/tests/load:/scripts:ro" -v "$RESULTS:/out" \
  -e TARGET=http://proxy -e ORIGIN="$APP_BASE_URL" -e LOADTEST_PASSWORD -e ACCOUNTS_FILE=/out/accounts.txt \
  -e ADMIN_EMAIL="$LOAD_ADMIN_EMAIL" -e ADMIN_PASSWORD="$LOAD_ADMIN_PASSWORD" \
  -e VOTERS="$VOTERS" -e ROUNDS="$ROUNDS" -e BURST_SECONDS="$BURST" -e ROUND_SECONDS="$ROUND_SECONDS" \
  -e LOGIN_RAMP_SECONDS="$LOGIN_RAMP" -e USERS="$VOTERS" -e REQUESTS="${REQUESTS:-0}" -e THINK_MS="$THINK_MS" \
  -e MAX_MINUTES="$MAX_MINUTES" -e K6_CSV_TIME_FORMAT=unix_milli \
  grafana/k6:1.8.2 run --quiet --out csv=/out/samples.csv.gz "/scripts/$SCRIPT" 2>&1; echo $? > "$RESULTS/k6.status"; } \
  | tee "$RESULTS/k6.log"
K6_STATUS=$(cat "$RESULTS/k6.status" 2>/dev/null || echo 1)  # k6's own exit code (99 = a threshold failed), not tee's
set -e
ENDED=$(date -u +%s)
kill $SAMPLER 2>/dev/null || true

echo "== reconciling the database"
$COMPOSE run --rm --no-deps migrate stagevote reconcile | tee "$RESULTS/reconcile.txt"
STORED=$(grep '^votes stored' "$RESULTS/reconcile.txt" | awk '{print $NF}')
CONFIRMED=$(python3 -c "import json;d=json.load(open('$RESULTS/summary.json'));print(int(d['metrics'].get('$COUNTER',{}).get('values',{}).get('count',0)))" 2>/dev/null || echo 0)
HOST_MEM="$(free -h | awk '/Mem:/{print $2}')"
{
  echo "duration_seconds=$((ENDED - STARTED))"
  echo "acknowledged_votes=$CONFIRMED"
  echo "stored_votes=$STORED"
  if [ "$CONFIRMED" = "$STORED" ]; then echo "acknowledged_vs_stored=MATCH"; else echo "acknowledged_vs_stored=MISMATCH"; fi
  echo "api_worker_processes=$API_WORKERS"
  echo "host_cpus=$(nproc) host_mem=$HOST_MEM"
  echo "k6_exit_status=$K6_STATUS"
} | tee "$RESULTS/outcome.txt"
python3 - "$RESULTS/meta.json" <<PY
import json, sys
json.dump({
    "mode": "$MODE", "users": $VOTERS, "voters": $VOTERS, "rounds": $ROUNDS,
    "expected_requests": ${REQUESTS:-None}, "think_ms": $THINK_MS, "api_workers": $API_WORKERS,
    "host_cpus": $(nproc), "host_mem": "$HOST_MEM", "started_epoch": $STARTED, "ended_epoch": $ENDED,
    "acknowledged_votes": $CONFIRMED, "stored_votes": ${STORED:-None}, "k6_exit_status": $K6_STATUS,
}, open(sys.argv[1], "w"), indent=2)
PY

echo "== writing the HTML report"
python3 "$ROOT/tests/load/report.py" "$RESULTS"
echo "Results in $RESULTS"
echo "View: tests/load/serve.sh, then open http://localhost:8099/$STAMP/report.html"
if [ -n "$DOWN" ]; then $COMPOSE down -v >/dev/null; echo "isolated stack removed"; fi
