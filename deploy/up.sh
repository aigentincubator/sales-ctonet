#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   DOMAIN=finder.peplink.com EMAIL=your@peplink.com ./deploy/up.sh

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

export DOMAIN=${DOMAIN:-finder.peplink.com}
export EMAIL=${EMAIL:-admin@peplink.com}

echo "[deploy] Using DOMAIN=$DOMAIN EMAIL=$EMAIL"
cd "$REPO_ROOT"

docker compose build
docker compose up -d

echo "[deploy] Waiting for app health..."
for i in {1..30}; do
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then
    echo "[deploy] Healthy at https://$DOMAIN/health"
    exit 0
  fi
  sleep 2
done

echo "[deploy] App started but /health did not respond over HTTPS yet. It may take up to a minute for the certificate to be issued."
exit 0

