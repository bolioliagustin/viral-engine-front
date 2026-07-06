#!/bin/bash
# Deploy worker on OVH VPS (production setup: API on Render, worker here).
# Called by GitHub Actions or manually on the VPS.
#
# Usage on VPS:
#   bash deploy/deploy-worker.sh
#
# Env:
#   DEPLOY_BRANCH=main   (default)

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/viralengine}"
COMPOSE_FILE="docker-compose.worker.yml"
BRANCH="${DEPLOY_BRANCH:-main}"
LOG_FILE="${HOME}/viralengine-deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

dc() {
    if docker info > /dev/null 2>&1; then
        docker compose "$@"
    else
        sudo docker compose "$@"
    fi
}

log "========================================="
log "Worker deploy started (branch: $BRANCH)"

cd "$APP_DIR"

log "Pulling latest code..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
log "At commit: $(git log --oneline -1)"

if [[ ! -f proxies.txt ]]; then
    log "WARNING: proxies.txt not found — YouTube downloads may fail"
fi

log "Building and restarting worker..."
dc -f "$COMPOSE_FILE" build --pull
dc -f "$COMPOSE_FILE" up -d --remove-orphans

log "Worker status:"
dc -f "$COMPOSE_FILE" ps | tee -a "$LOG_FILE"

(docker image prune -f > /dev/null 2>&1 || sudo docker image prune -f > /dev/null 2>&1) || true

log "Deploy completed"
log "========================================="
