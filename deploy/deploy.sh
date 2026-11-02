#!/usr/bin/env bash
# Build locally, ship to the box, restart. Usage: deploy/deploy.sh [user@host]
# Box layout: ~/opspilot/backend (API + venv + SQLite in data/), /srv/opspilot (static SPA), Caddy in front.
set -euo pipefail
HOST="${1:-ubuntu@92.4.93.133}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh -o ServerAliveInterval=15 -o BatchMode=yes"

echo "==> building frontend"
(cd "$ROOT/frontend" && npm run build --silent)

echo "==> syncing code"
$SSH "$HOST" 'mkdir -p ~/opspilot/backend ~/opspilot/deploy && sudo mkdir -p /srv/opspilot && sudo chown ubuntu:ubuntu /srv/opspilot'
rsync -az --delete -e "$SSH" --exclude .venv --exclude data --exclude .env --exclude __pycache__ --exclude .pytest_cache \
  "$ROOT/backend/" "$HOST:~/opspilot/backend/"
rsync -az --delete -e "$SSH" "$ROOT/frontend/dist/" "$HOST:/srv/opspilot/"
rsync -az -e "$SSH" "$ROOT/deploy/opspilot-api.service" "$ROOT/deploy/Caddyfile" "$HOST:~/opspilot/deploy/"
if [ -f "$ROOT/deploy/.env.production" ]; then
  rsync -az -e "$SSH" "$ROOT/deploy/.env.production" "$HOST:~/opspilot/backend/.env"
fi

echo "==> installing and restarting"
$SSH "$HOST" bash -s <<'REMOTE'
set -e
cd ~/opspilot/backend
~/.local/bin/uv sync --frozen --no-dev --python 3.12 -q
sudo cp ~/opspilot/deploy/opspilot-api.service /etc/systemd/system/opspilot-api.service
sudo cp ~/opspilot/deploy/Caddyfile /etc/caddy/Caddyfile
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo systemctl daemon-reload
sudo systemctl enable opspilot-api >/dev/null 2>&1
sudo systemctl restart opspilot-api
sudo systemctl reload caddy || sudo systemctl restart caddy
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8100/api/healthz >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8100/api/healthz && echo " api up"
REMOTE
echo "==> done: https://92-4-93-133.sslip.io"
