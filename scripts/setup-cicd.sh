#!/usr/bin/env sh
# One-time: connect GitHub Actions to the production server, so that every push to main that
# passes the tests is deployed automatically (.github/workflows/ci-cd.yml).
#
#   scripts/setup-cicd.sh --host 52.91.241.136 --user ec2-user --key Dhanvarsha.pem \
#                         [--repo namanjain221995/Dhanvarsha]
#
# What it does:
#   1. creates a NEW SSH key that only GitHub Actions uses (your .pem key never leaves this
#      computer);
#   2. adds it to the server's ~/.ssh/authorized_keys with a forced command, so that key can
#      do one thing only: fetch main from GitHub and run scripts/deploy.sh (no shell, no
#      port forwarding);
#   3. stores DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY and DEPLOY_KNOWN_HOSTS as GitHub
#      Actions secrets (needs the gh CLI, signed in to an account that can edit the repo).
# Run it again to replace the key (the old one is removed from the server).
set -eu

HOST=""; LOGIN=""; KEY=""; REPO="namanjain221995/Dhanvarsha"
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift ;;
    --user) LOGIN="$2"; shift ;;
    --key) KEY="$2"; shift ;;
    --repo) REPO="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$HOST" ] && [ -n "$LOGIN" ] && [ -n "$KEY" ] || { echo "Give --host, --user and --key." >&2; exit 2; }
[ -f "$KEY" ] || { echo "Key file $KEY not found." >&2; exit 1; }
command -v gh >/dev/null || { echo "Install the GitHub CLI (gh) and run 'gh auth login' first." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LABEL="github-actions-deploy@$(basename "$REPO")"
ssh-keygen -q -t ed25519 -N "" -C "$LABEL" -f "$TMP/key"
ssh-keyscan -t ed25519 "$HOST" 2>/dev/null > "$TMP/known_hosts"
[ -s "$TMP/known_hosts" ] || { echo "Could not read the server's host key." >&2; exit 1; }
echo "Server host key: $(ssh-keygen -lf "$TMP/known_hosts" | awk '{print $2}')"

FORCED='command="cd ~/Dhanvarsha && git fetch -q origin main && git reset -q --hard origin/main && exec scripts/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty'
echo "== allowing the deploy key on $LOGIN@$HOST (deploy command only)"
printf '%s %s\n' "$FORCED" "$(cat "$TMP/key.pub")" | ssh -i "$KEY" -o BatchMode=yes "$LOGIN@$HOST" \
  "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -v ' $LABEL\$' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.new || true; cat >> ~/.ssh/authorized_keys.new; mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys"

echo "== storing GitHub Actions secrets in $REPO"
gh secret set DEPLOY_HOST -R "$REPO" --body "$HOST"
gh secret set DEPLOY_USER -R "$REPO" --body "$LOGIN"
gh secret set DEPLOY_SSH_KEY -R "$REPO" < "$TMP/key"
gh secret set DEPLOY_KNOWN_HOSTS -R "$REPO" < "$TMP/known_hosts"

echo
echo "Done. Every push to main now runs the tests and, if they pass, deploys."
echo "Run it by hand: gh workflow run ci-cd.yml -R $REPO   (or GitHub › Actions › CI/CD › Run workflow)"
