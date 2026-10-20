#!/usr/bin/env sh
# Run the standard StageVote capacity tests on this machine, one after another, and collect
# the passed reports in reports-test/ (the same three tests as the development baseline):
#
#   1. 500 users, 10,000 requests, 1 s pause per user   (busy but human pace)
#   2. 500 users, 100,000 requests, no pause            (stress)
#   3. 500 users, 1,000,000 requests, no pause          (endurance, about 10 minutes)
#
#   tests/load/server-check.sh --yes-synthetic-load [--quick] [--api-workers N] [--reports DIR]
#
# --quick runs only tests 1 and 2. Every test uses the isolated "stagevote-load" copy with
# synthetic accounts; the real site, its database and its email are never touched. The tests
# use most of the CPU for 10-15 minutes: run them before the event, never during it.
# Afterwards the test copy and its data are deleted. View the reports with:
#   python3 -m http.server 8100 --bind 127.0.0.1 --directory reports-test
set -eu
cd "$(dirname "$0")/../.."

CONFIRM=""; QUICK=""; EXTRA=""; REPORTS=reports-test
while [ $# -gt 0 ]; do
  case "$1" in
    --yes-synthetic-load) CONFIRM=1 ;;
    --quick) QUICK=1 ;;
    --api-workers) EXTRA="--api-workers $2"; shift ;;
    --reports) REPORTS="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -z "$CONFIRM" ]; then
  echo "Refusing to run: pass --yes-synthetic-load to confirm synthetic load tests on this machine." >&2
  exit 2
fi
command -v docker >/dev/null || { echo "Docker is not installed (see scripts/server-setup.sh)" >&2; exit 1; }

# Newest run folder before this check (index.html and other files are not runs).
BEFORE="$(find tests/load/results -mindepth 1 -maxdepth 1 -type d -name '20*' 2>/dev/null | sed 's#.*/##' | sort | tail -1)"
run() {
  echo
  echo "################ $1"
  shift
  # shellcheck disable=SC2086 # EXTRA holds two words on purpose
  tests/load/run.sh --yes-synthetic-load "$@" $EXTRA || echo "(this test reported a problem; see its report)"
}

run "Test 1 of 3: 500 users, 10,000 requests, 1 s pause" --users 500 --requests 10000 --think-ms 1000
run "Test 2 of 3: 500 users, 100,000 requests, no pause" --users 500 --requests 100000 --no-build
if [ -z "$QUICK" ]; then
  run "Test 3 of 3: 500 users, 1,000,000 requests, no pause" --users 500 --requests 1000000 --max-minutes 60 --no-build
fi
docker compose -p stagevote-load -f compose.yaml down -v >/dev/null 2>&1 || true
echo
echo "== test copy removed"

python3 tests/load/report.py --export-passed "$REPORTS"
python3 - "$BEFORE" <<'PY'
import json, sys
from pathlib import Path
before = sys.argv[1]
rows = []
for folder in sorted(Path("tests/load/results").iterdir()):
    data = folder / "report-data.json"
    if folder.is_dir() and folder.name > before and data.exists():
        rows.append(json.loads(data.read_text()))
print()
print(f"{'Requests':>10}  {'Pause':>9}  {'Speed':>8}  {'p95':>8}  {'Failed':>7}  Result")
for r in rows:
    p95 = f"{r['p95'] / 1000:.2f} s" if r["p95"] >= 1000 else f"{r['p95']:.0f} ms"
    print(f"{r['total']:>10,}  {r.get('pause', ''):>9}  {r['rps_avg']:>6,.0f}/s  {p95:>8}  {r['fail_pct']:>6.2f}%  "
          f"{'passed' if r['passed'] else 'PROBLEMS'}")
PY
echo
echo "Passed reports are in $REPORTS/ (open $REPORTS/index.html)."
