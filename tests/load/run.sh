#!/usr/bin/env sh
# Reproducible StageVote load test against an ISOLATED Compose project with synthetic
# accounts. It never targets the main stack or any remote host.
#
#   tests/load/run.sh --yes-synthetic-load [--voters 500] [--rounds 3] [--burst 10]
#                     [--round-seconds 40] [--login-ramp 30] [--api-workers 2] [--no-build] [--down]
#
# Steps: start project "stagevote-load" (own volumes, ports 3190/55490), seed a fresh
# load-test programme, run k6 (grafana/k6) inside the project network, sample container
# CPU/RAM, then reconcile stored votes against acknowledged receipts.
set -eu
cd "$(dirname "$0")/../.."
ROOT="$PWD"

CONFIRM=""; BUILD="--build"; VOTERS=500; ROUNDS=3; BURST=10; ROUND_SECONDS=40; LOGIN_RAMP=30; API_WORKERS=6; DOWN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes-synthetic-load) CONFIRM=1 ;;
    --voters) VOTERS="$2"; shift ;;
    --rounds) ROUNDS="$2"; shift ;;
    --burst) BURST="$2"; shift ;;
    --round-seconds) ROUND_SECONDS="$2"; shift ;;
    --login-ramp) LOGIN_RAMP="$2"; shift ;;
    --api-workers) API_WORKERS="$6"; shift ;;
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
[ -f .env ] || { echo "Run scripts/bootstrap.sh first" >&2; exit 1; }

PROJECT=stagevote-load
export STAGEVOTE_HTTP_PORT=3190 DB_HOST_PORT=55490 MAILPIT_UI_PORT=8095 MAILPIT_SMTP_PORT=1095 STAGEVOTE_BIND=127.0.0.1
export APP_BASE_URL="http://localhost:${STAGEVOTE_HTTP_PORT}"
# Isolated test stacks always deliver to their own Mailpit, whatever .env configures for
# the main stack (a real provider must never receive synthetic test addresses).
export EMAIL_PROVIDER=mailpit SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_TLS_MODE=none SMTP_USERNAME= SMTP_PASSWORD= \
  GOOGLE_SERVICE_ACCOUNT_FILE= GOOGLE_WORKSPACE_SENDER= MAIL_FROM="StageVote Tests <noreply@example.com>"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS="$ROOT/tests/load/results/$STAMP"
mkdir -p "$RESULTS"
chmod 777 "$RESULTS"
COMPOSE="docker compose -p $PROJECT -f compose.yaml"
export API_WORKERS

echo "== starting isolated stack ($PROJECT on $APP_BASE_URL)"
$COMPOSE up -d $BUILD db mailpit migrate api worker web proxy >/dev/null
i=0
until curl -fsS "$APP_BASE_URL/api/v1/ready" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -gt 90 ] && { echo "stack did not become ready" >&2; exit 1; }
  sleep 2
done

LOADTEST_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
export LOADTEST_PASSWORD
echo "== seeding $VOTERS synthetic participants and $((ROUNDS + 1)) rounds"
$COMPOSE run --rm --no-deps --user 0:0 -e LOADTEST_PASSWORD -e LOADTEST_ACCOUNTS_FILE=/out/accounts.txt \
  -v "$RESULTS:/out" migrate stagevote seed-loadtest --participants "$VOTERS" --events "$((ROUNDS + 1))" --yes-synthetic

ADMIN_BOOTSTRAP_EMAIL="$(grep '^ADMIN_BOOTSTRAP_EMAIL=' .env | cut -d= -f2-)"
ADMIN_BOOTSTRAP_PASSWORD="$(grep '^ADMIN_BOOTSTRAP_PASSWORD=' .env | cut -d= -f2-)"

echo "== sampling container CPU/RAM every 5s"
( while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep "^$PROJECT" | sed "s/^/$(date -u +%H:%M:%S),/"
    sleep 5
  done ) > "$RESULTS/docker-stats.csv" 2>/dev/null &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

echo "== running k6 ($VOTERS voters, $ROUNDS rounds, ${BURST}s vote burst)"
STARTED=$(date -u +%s)
set +e
docker run --rm --network "${PROJECT}_default" --ulimit nofile=65536:65536 \
  -v "$ROOT/tests/load:/scripts:ro" -v "$RESULTS:/out" \
  -e TARGET=http://proxy -e ORIGIN="$APP_BASE_URL" -e LOADTEST_PASSWORD -e ACCOUNTS_FILE=/out/accounts.txt \
  -e ADMIN_EMAIL="$ADMIN_BOOTSTRAP_EMAIL" -e ADMIN_PASSWORD="$ADMIN_BOOTSTRAP_PASSWORD" \
  -e VOTERS="$VOTERS" -e ROUNDS="$ROUNDS" -e BURST_SECONDS="$BURST" -e ROUND_SECONDS="$ROUND_SECONDS" \
  -e LOGIN_RAMP_SECONDS="$LOGIN_RAMP" \
  grafana/k6:1.8.2 run --quiet /scripts/stagevote.js 2>&1 | tee "$RESULTS/k6.log"
K6_STATUS=$?
set -e
ENDED=$(date -u +%s)
kill $SAMPLER 2>/dev/null || true

echo "== reconciling the database"
$COMPOSE run --rm --no-deps migrate stagevote reconcile | tee "$RESULTS/reconcile.txt"
STORED=$(grep '^votes stored' "$RESULTS/reconcile.txt" | awk '{print $NF}')
CONFIRMED=$(python3 -c "import json;d=json.load(open('$RESULTS/summary.json'));print(int(d['metrics'].get('ballots_confirmed',{}).get('values',{}).get('count',0)))")
{
  echo "duration_seconds=$((ENDED - STARTED))"
  echo "acknowledged_ballots=$CONFIRMED"
  echo "stored_votes=$STORED"
  if [ "$CONFIRMED" = "$STORED" ]; then echo "acknowledged_vs_stored=MATCH"; else echo "acknowledged_vs_stored=MISMATCH"; fi
  echo "api_worker_processes=$API_WORKERS"
  echo "host_cpus=$(nproc) host_mem=$(free -h | awk '/Mem:/{print $2}')"
  echo "k6_exit_status=$K6_STATUS"
} | tee "$RESULTS/outcome.txt"
python3 - "$RESULTS/docker-stats.csv" <<'PY' | tee "$RESULTS/resources.txt"
import csv, sys, re, collections
peak_cpu = collections.defaultdict(float); peak_mem = collections.defaultdict(float)
units = {"KiB": 1/1024, "MiB": 1, "GiB": 1024, "B": 1/1048576}
for row in csv.reader(open(sys.argv[1])):
    if len(row) < 4: continue
    _, name, cpu, mem = row[:4]
    peak_cpu[name] = max(peak_cpu[name], float(cpu.rstrip('%') or 0))
    m = re.match(r"([\d.]+)(\w+)", mem.split('/')[0].strip())
    if m: peak_mem[name] = max(peak_mem[name], float(m.group(1)) * units.get(m.group(2), 1))
for name in sorted(peak_cpu):
    print(f"{name}: peak_cpu={peak_cpu[name]:.0f}% peak_mem={peak_mem[name]:.0f}MiB")
PY
echo "Results in $RESULTS"
if [ -n "$DOWN" ]; then $COMPOSE down -v >/dev/null; echo "isolated stack removed"; fi
