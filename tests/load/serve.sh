#!/usr/bin/env sh
# Serve the load-test reports on this machine only:  tests/load/serve.sh [port]
# Then open http://localhost:8099/ (in VS Code Remote, the port is forwarded for you).
set -eu
cd "$(dirname "$0")/results"
PORT="${1:-8099}"
[ -f index.html ] || echo "No reports yet: run tests/load/run.sh --yes-synthetic-load --users 500 --requests 10000"
echo "Load-test reports: http://localhost:$PORT/   (Ctrl+C to stop)"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
