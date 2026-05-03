#!/usr/bin/env bash
# PACER · operator-side one-shot bootstrap.
#
# Run from your dev machine (WSL or any Linux/macOS shell). Drives a
# fresh Ubuntu 22/24 VPS end-to-end:
#
#   1. SSH preflight + key-based login check
#   2. Build /opt/pacer/.env from your local dev .env + secrets-staging/
#   3. scp install.sh to the VPS, run it (Docker + UFW + swap)
#   4. clone the repo into /opt/pacer
#   5. install .env, bring the stack up with the Caddy overlay
#   6. run alembic upgrade head
#   7. smoke-test https://<hostname>/
#
# Usage:
#   bash deploy/remote_bootstrap.sh <vps-host-or-ip> [ssh-user] [ssh-key]
#
# Defaults: ssh-user=root, ssh-key=$HOME/.ssh/pacer_ed25519 (or
# /mnt/c/Users/keith/.ssh/pacer_ed25519 from WSL).
#
# Env that controls behavior:
#   PACER_DOMAIN          (required, taken from local .env if unset)
#   LETSENCRYPT_EMAIL     (required, taken from local .env if unset)
#   PACER_REPO_URL        (default: https://github.com/ksksrbiz-arch/PACER.git)
#   PACER_BRANCH          (default: main)
#
set -euo pipefail

VPS="${1:-}"
SSH_USER="${2:-root}"
DEFAULT_KEY="${HOME}/.ssh/pacer_ed25519"
[[ ! -f "$DEFAULT_KEY" && -f /mnt/c/Users/keith/.ssh/pacer_ed25519 ]] && DEFAULT_KEY=/mnt/c/Users/keith/.ssh/pacer_ed25519
SSH_KEY="${3:-$DEFAULT_KEY}"

if [[ -z "$VPS" ]]; then
  echo "Usage: $0 <vps-host-or-ip> [ssh-user=root] [ssh-key=~/.ssh/pacer_ed25519]" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$REPO_DIR/deploy/.secrets-staging/secrets.env"
LOCAL_DEV_ENV="$REPO_DIR/.env"

c_cyan='\033[36m'; c_yellow='\033[33m'; c_red='\033[31m'; c_off='\033[0m'
log()  { echo -e "${c_cyan}[bootstrap]${c_off} $*"; }
warn() { echo -e "${c_yellow}[warn]${c_off} $*" >&2; }
die()  { echo -e "${c_red}[error]${c_off} $*" >&2; exit 1; }

[[ -f "$SECRETS" ]] || die "missing $SECRETS - run deploy/.secrets-staging/ generator first"
[[ -f "$LOCAL_DEV_ENV" ]] || die "missing local $LOCAL_DEV_ENV (your dev secrets baseline)"
[[ -f "$SSH_KEY" ]] || die "missing ssh key $SSH_KEY"

# shellcheck disable=SC1090
. "$SECRETS"
PACER_DOMAIN="${PACER_DOMAIN:-$(grep -E '^PACER_DOMAIN=' "$LOCAL_DEV_ENV" | tail -1 | cut -d= -f2-)}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-$(grep -E '^LETSENCRYPT_EMAIL=' "$LOCAL_DEV_ENV" | tail -1 | cut -d= -f2-)}"
PACER_DOMAIN="${PACER_DOMAIN:-pacer.1commercesolutions.com}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-keith@1commercesolutions.com}"
PACER_REPO_URL="${PACER_REPO_URL:-https://github.com/ksksrbiz-arch/PACER.git}"
PACER_BRANCH="${PACER_BRANCH:-main}"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10)
SCP_OPTS=("${SSH_OPTS[@]}")

log "target: $SSH_USER@$VPS  domain: $PACER_DOMAIN  email: $LETSENCRYPT_EMAIL"
log "key:    $SSH_KEY"

# ---- 1. SSH preflight ---------------------------------------------------
log "1/7 ssh preflight"
if ! ssh "${SSH_OPTS[@]}" "$SSH_USER@$VPS" "echo ok && uname -a && cat /etc/os-release | head -2" </dev/null; then
  die "ssh failed. Verify the public key is installed at $SSH_USER@$VPS:~/.ssh/authorized_keys"
fi

# ---- 2. build /opt/pacer/.env locally first -----------------------------
log "2/7 building production .env (local + new VPS secrets)"
PROD_ENV="$REPO_DIR/deploy/.env.production"
{
  # Start from the local dev .env (carries all your API keys, LLM creds, etc.)
  cat "$LOCAL_DEV_ENV"
  echo
  echo "# === injected by remote_bootstrap.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "PACER_DOMAIN=$PACER_DOMAIN"
  echo "LETSENCRYPT_EMAIL=$LETSENCRYPT_EMAIL"
  echo "API_AUTH_TOKEN=$API_AUTH_TOKEN"
  echo "PACER_BASIC_USER=$PACER_BASIC_USER"
  echo "PACER_BASIC_HASH=$PACER_BASIC_HASH"
  echo "POSTGRES_USER=pacer"
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
  echo "POSTGRES_DB=pacer"
  # Override DB URLs to point at the compose 'postgres' service hostname
  echo "DATABASE_URL=postgresql+asyncpg://pacer:$POSTGRES_PASSWORD@postgres:5432/pacer"
  echo "SYNC_DATABASE_URL=postgresql://pacer:$POSTGRES_PASSWORD@postgres:5432/pacer"
  echo "REDIS_URL=redis://redis:6379/0"
  echo "ENVIRONMENT=production"
} > "$PROD_ENV"
chmod 600 "$PROD_ENV"
log "   -> $PROD_ENV ($(wc -c < "$PROD_ENV") bytes)"

# ---- 3. install.sh on the VPS ------------------------------------------
log "3/7 installing Docker + UFW + swap on remote"
scp "${SCP_OPTS[@]}" "$REPO_DIR/deploy/install.sh" "$SSH_USER@$VPS:/tmp/install.sh"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VPS" \
  "bash /tmp/install.sh '$PACER_DOMAIN' '$LETSENCRYPT_EMAIL'" </dev/null

# ---- 4. clone the repo into /opt/pacer ---------------------------------
log "4/7 cloning repo on remote"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VPS" \
  "set -e
   cd /opt/pacer
   if [ -d .git ]; then
     git fetch origin && git checkout '$PACER_BRANCH' && git pull --ff-only
   else
     git clone -b '$PACER_BRANCH' '$PACER_REPO_URL' .
   fi
   git rev-parse --short HEAD" </dev/null

# ---- 5. push .env over (after install.sh created /opt/pacer) -----------
log "5/7 installing /opt/pacer/.env"
scp "${SCP_OPTS[@]}" "$PROD_ENV" "$SSH_USER@$VPS:/opt/pacer/.env"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VPS" "chmod 600 /opt/pacer/.env" </dev/null

# ---- 6. compose up + migrate -------------------------------------------
log "6/7 docker compose up + alembic upgrade head (this can take ~5 min for first build)"
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VPS" \
  "set -e
   cd /opt/pacer
   docker compose -f docker-compose.yml -f deploy/docker-compose.caddy.yml pull --ignore-buildable 2>/dev/null || true
   docker compose -f docker-compose.yml -f deploy/docker-compose.caddy.yml up -d --build
   # Wait for postgres healthy (max 90s)
   for i in \$(seq 1 18); do
     state=\$(docker compose ps --format json postgres 2>/dev/null | grep -oE '\"Health\":\"healthy\"' || true)
     if [ -n \"\$state\" ]; then echo 'postgres healthy'; break; fi
     sleep 5
   done
   docker compose exec -T pacer alembic upgrade head
   docker compose ps" </dev/null

# ---- 7. smoke ----------------------------------------------------------
log "7/7 smoke test"
sleep 5
RESP_HEAD=$(curl -fsSI --max-time 15 "https://$PACER_DOMAIN/" || true)
RESP_AUTHED=$(curl -fsS --max-time 15 -u "$PACER_BASIC_USER:$PACER_BASIC_PASS" "https://$PACER_DOMAIN/" -o /dev/null -w "%{http_code}" || echo "fail")

echo
echo "===================================================================="
echo "  PACER bootstrap complete."
echo "===================================================================="
echo "  https://$PACER_DOMAIN/"
echo "  Login user: $PACER_BASIC_USER"
echo "  Login pass: see deploy/.secrets-staging/secrets.env -> PACER_BASIC_PASS"
echo
echo "  Caddy front-door:    $(echo "$RESP_HEAD" | head -1 | tr -d '\r')"
echo "  Authed GET / -> HTTP $RESP_AUTHED"
echo
echo "  SSH:  ssh -i $SSH_KEY $SSH_USER@$VPS"
echo "  Logs: ssh -i $SSH_KEY $SSH_USER@$VPS 'cd /opt/pacer && docker compose logs -f --tail 100 pacer pacer-web caddy'"
echo "===================================================================="
