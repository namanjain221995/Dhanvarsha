#!/usr/bin/env sh
# Deploy or update StageVote in production on this server (HTTPS by Caddy on ports 80/443).
#
# First time (after sudo scripts/server-setup.sh):
#   scripts/deploy.sh --domain vote.example.org --acme-email you@example.org
#     creates .env with generated secrets (if missing) and writes the production settings for
#     that domain. --domain also accepts the server's public IPv4 address (no domain yet): the
#     site is then https://<ip> with a short-lived Let's Encrypt IP certificate. Then edit .env once for the email settings (SMTP_* and MAIL_FROM) and run:
#   scripts/deploy.sh --check          # validate .env and DNS only, change nothing
#   scripts/deploy.sh                  # build and start the site
#   scripts/deploy.sh --load-test      # ... then run the three capacity tests on this server
#
# An update never restarts the site while a voting round is open (the CI/CD pipeline relies
# on this): close the round first, or pass --force.
#
# Updates: git pull, then scripts/deploy.sh. Data (database, uploads, certificates) lives in
# Docker volumes and survives updates. Never run --load-test during the event.
set -eu
cd "$(dirname "$0")/.."

CHECK=""; LOAD=""; QUICK=""; DOMAIN=""; ACME=""; FORCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --load-test) LOAD=1 ;;
    --quick-load-test) LOAD=1; QUICK="--quick" ;;
    --domain) DOMAIN="$2"; shift ;;
    --acme-email) ACME="$2"; shift ;;
    --force) FORCE=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
COMPOSE="docker compose -f compose.prod.yaml"

if [ ! -f .env ]; then
  python3 scripts/bootstrap.py >/dev/null
  echo "Created .env with generated secrets."
fi
chmod 600 .env

if [ -n "$DOMAIN" ] || [ -n "$ACME" ]; then
  [ -n "$DOMAIN" ] && [ -n "$ACME" ] || { echo "Give both --domain and --acme-email." >&2; exit 2; }
  python3 - "$DOMAIN" "$ACME" "$(nproc)" <<'PY'
import re, sys
from pathlib import Path
domain, acme, cpus = sys.argv[1].strip().lower(), sys.argv[2].strip(), int(sys.argv[3])
is_ip = re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", domain) is not None
if not is_ip and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
    sys.exit(f"--domain must be a host name such as vote.example.org or an IPv4 address (got {domain!r})")
values = {
    # A domain gets a normal certificate; an IP address gets a short-lived IP certificate.
    "CADDY_CONFIG": "Caddyfile.ip" if is_ip else "Caddyfile.prod",
    "APP_ENV": "production",
    "APP_BASE_URL": f"https://{domain}",
    "SITE_DOMAIN": domain,
    "ACME_EMAIL": acme,
    # Half the cores (2 to 8): the other half serves the database, proxy and password hashing.
    "API_WORKERS": str(max(2, min(8, cpus // 2))),
}
path = Path(".env")
lines = path.read_text().splitlines()
seen = set()
for i, line in enumerate(lines):
    key = line.split("=", 1)[0]
    if key in values and "=" in line:
        lines[i] = f"{key}={values[key]}"
        seen.add(key)
lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
path.write_text("\n".join(lines) + "\n")
print("Production settings written to .env: " + ", ".join(f"{k}={v}" for k, v in values.items()))
PY
fi

echo "== checking .env"
python3 - <<'PY'
import re, sys
from pathlib import Path
env = {}
for line in Path(".env").read_text().splitlines():
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
problems, warnings = [], []
def need(key, why):
    value = env.get(key, "")
    if not value or re.search(r"<[^>]*>|change-me", value):
        problems.append(f"{key}: {why}")
    return value
if env.get("APP_ENV") != "production":
    problems.append("APP_ENV: must be production (run with --domain ... --acme-email ...)")
domain = need("SITE_DOMAIN", "your site's host name, for example vote.example.org")
url = need("APP_BASE_URL", "https:// plus SITE_DOMAIN")
if domain and url and url.rstrip("/") != f"https://{domain}":
    problems.append(f"APP_BASE_URL: must be https://{domain} (found {url})")
if domain and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", domain) and env.get("CADDY_CONFIG") != "Caddyfile.ip":
    problems.append("CADDY_CONFIG: must be Caddyfile.ip when SITE_DOMAIN is an IP address")
acme = need("ACME_EMAIL", "an email address for certificate notices")
if acme and "@" not in acme:
    problems.append("ACME_EMAIL: must be an email address")
for key in ("APP_SECRET_KEY", "INVITATION_ENCRYPTION_KEY", "POSTGRES_SUPERUSER_PASSWORD", "DB_OWNER_PASSWORD", "DB_APP_PASSWORD"):
    need(key, "generated secret missing (run python3 scripts/bootstrap.py)")
for key in ("DATABASE_URL", "DATABASE_MIGRATION_URL"):
    if "@db:" not in need(key, "database address"):
        problems.append(f"{key}: must point at the 'db' service (…@db:5432/stagevote)")
provider = env.get("EMAIL_PROVIDER", "")
if provider not in ("smtp", "gmail_workspace"):
    problems.append("EMAIL_PROVIDER: must be smtp (for example Gmail with an App Password) or gmail_workspace")
if provider == "smtp":
    for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
        need(key, "required for SMTP email")
    if env.get("SMTP_TLS_MODE") not in ("starttls", "tls"):
        problems.append("SMTP_TLS_MODE: must be starttls (port 587) or tls (port 465)")
    if " " in env.get("SMTP_PASSWORD", ""):
        problems.append("SMTP_PASSWORD: remove the spaces from the App Password")
mail_from = env.get("MAIL_FROM", "")
if "@" not in mail_from or re.search(r"@example\.(com|org)>?$", mail_from):
    problems.append('MAIL_FROM: your real sender, for example "Dhanvarsha <name@your-domain>" (same address as SMTP_USERNAME for Gmail)')
admin_pw = env.get("ADMIN_BOOTSTRAP_PASSWORD", "")
if admin_pw and len(admin_pw) < 12:
    problems.append("ADMIN_BOOTSTRAP_PASSWORD: use at least 12 characters")
if admin_pw:
    warnings.append("ADMIN_BOOTSTRAP_PASSWORD is still in .env: after your first sign-in, delete its value.")
if not env.get("API_WORKERS", "").isdigit():
    problems.append("API_WORKERS: a number, for example 8 on a 16-core server")
for w in warnings:
    print("note:", w)
if problems:
    print("Fix these in .env, then run scripts/deploy.sh again:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print(f".env looks complete for https://{domain}")
PY

DOMAIN="$(grep '^SITE_DOMAIN=' .env | cut -d= -f2- | tr -d '"')"
IS_IP=""
case "$DOMAIN" in *[!0-9.]*) ;; *) IS_IP=1 ;; esac
PUBLIC="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null || true)"
if [ -n "$IS_IP" ]; then
  echo "== site address is the IP $DOMAIN"
  if [ -n "$PUBLIC" ] && [ "$PUBLIC" != "$DOMAIN" ]; then
    echo "warning: this server's public IP is $PUBLIC, not $DOMAIN. Run: scripts/deploy.sh --domain $PUBLIC --acme-email <email>"
  fi
  RESOLVED="$DOMAIN"
else
  echo "== checking DNS for $DOMAIN"
  RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
fi
if [ -z "$IS_IP" ]; then
  if [ -z "$RESOLVED" ]; then
    echo "warning: $DOMAIN does not resolve yet. Create a DNS A record pointing to ${PUBLIC:-this server's public IP}."
  elif [ -n "$PUBLIC" ] && [ "$RESOLVED" != "$PUBLIC" ]; then
    echo "warning: $DOMAIN points to $RESOLVED but this server is $PUBLIC. HTTPS certificates will fail until DNS matches."
  else
    echo "$DOMAIN -> $RESOLVED (this server)"
  fi
fi
[ -n "$CHECK" ] && { echo "Check only: nothing was started."; exit 0; }

command -v docker >/dev/null || { echo "Docker is missing: run sudo scripts/server-setup.sh first." >&2; exit 1; }
if [ -z "$FORCE" ] && [ -n "$($COMPOSE ps -q db 2>/dev/null)" ]; then
  OPEN="$($COMPOSE exec -T db psql -U postgres -d stagevote -Atc \
    "SELECT string_agg(name, ', ') FROM events WHERE status = 'open' AND archived_at IS NULL" 2>/dev/null || true)"
  if [ -n "$OPEN" ]; then
    echo "Not deploying: voting is open for $OPEN. Restarting now would interrupt voters." >&2
    echo "Close the round in Live control (or wait until it ends), then deploy again. --force skips this check." >&2
    exit 3
  fi
fi
STAGEVOTE_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
export STAGEVOTE_VERSION
echo "== building and starting version $STAGEVOTE_VERSION"
$COMPOSE up -d --build

echo "== waiting for https://$DOMAIN (the first certificate can take a minute)"
i=0
# A domain is checked through this machine; an IP address is checked as visitors reach it.
RESOLVE="--resolve $DOMAIN:443:127.0.0.1"
[ -n "$IS_IP" ] && RESOLVE=""
# shellcheck disable=SC2086 # RESOLVE is empty or one option with its value
until curl -fsS --max-time 5 $RESOLVE "https://$DOMAIN/api/v1/ready" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ $i -gt 60 ]; then
    echo "The site did not answer over HTTPS within 5 minutes. Check DNS, ports 80/443 and:" >&2
    echo "  $COMPOSE logs --tail 50 proxy api migrate" >&2
    exit 1
  fi
  sleep 5
done
echo "== database permissions"
$COMPOSE exec -T api stagevote check-privileges
$COMPOSE ps --format '{{.Service}}: {{.Status}}'
echo
echo "Live: https://$DOMAIN  (organisers: https://$DOMAIN/admin/login)"
if grep -q '^ADMIN_BOOTSTRAP_PASSWORD=..*' .env; then
  echo "After your first sign-in, delete the value of ADMIN_BOOTSTRAP_PASSWORD in .env."
fi

if [ -n "$LOAD" ]; then
  echo
  echo "== capacity tests on this server (isolated test copy; the live site is not touched)"
  WORKERS="$(grep '^API_WORKERS=' .env | cut -d= -f2-)"
  # shellcheck disable=SC2086 # QUICK is empty or one flag
  tests/load/server-check.sh --yes-synthetic-load $QUICK --api-workers "$WORKERS"
fi
