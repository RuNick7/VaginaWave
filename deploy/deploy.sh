#!/usr/bin/env bash
# Выкатить текущую версию сервиса на сервер и перезапустить его.
#   VOLNA_SERVER=root@host ./deploy/deploy.sh
# или один раз записать адрес в deploy/.server (он не попадает в git) и дальше просто
#   ./deploy/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${VOLNA_SERVER:-$(cat "$ROOT/deploy/.server" 2>/dev/null || true)}"
if [ -z "$SERVER" ]; then
  echo "Не знаю, куда выкатывать: VOLNA_SERVER=root@host ./deploy/deploy.sh или echo root@host > deploy/.server" >&2
  exit 1
fi

echo "Копирую сервис на $SERVER…"
COPYFILE_DISABLE=1 tar -C "$ROOT" -cz --no-xattrs --no-mac-metadata \
  --exclude .venv --exclude data --exclude .env --exclude .pytest_cache --exclude __pycache__ --exclude '*.egg-info' \
  wave-service -C "$ROOT/deploy" docker-compose.yml |
  ssh "$SERVER" 'set -e; mkdir -p /opt/volna && cd /opt/volna && rm -rf wave-service && tar -xzf - --no-same-owner'

echo "Собираю и перезапускаю…"
ssh "$SERVER" 'set -e; cd /opt/volna && mkdir -p data && chown 1000:1000 data && docker compose up -d --build && docker image prune -f >/dev/null'
echo "Готово: https://${SERVER#*@}/"
