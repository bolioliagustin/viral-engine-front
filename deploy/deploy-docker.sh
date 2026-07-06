#!/bin/bash
# Docker-based deploy — run on the VPS after every push to main.
# Replaces the legacy systemd deploy (deploy.sh).
#
# Usage:
#   bash deploy/deploy-docker.sh
#   # or triggered by GitHub Actions / webhook

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/viralengine}"
LOG_FILE="/var/log/viralengine-deploy.log"
BRANCH="${DEPLOY_BRANCH:-main}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "Docker deploy started (branch: $BRANCH)"

cd "$APP_DIR"

# ── 1. Pull latest code ─────────────────────────────────────────────────────
log "Pulling latest code..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
log "At commit: $(git log --oneline -1)"

# ── 2. Rebuild and restart containers ─────────────────────────────────────────
log "Building and restarting containers..."
docker compose build --pull
docker compose up -d --remove-orphans

# ── 3. Wait for backend health ────────────────────────────────────────────────
log "Waiting for backend health check..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:3000/health/live > /dev/null 2>&1; then
        log "Backend healthy"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log "Backend health check failed — showing logs:"
        docker compose logs --tail=50 backend
        exit 1
    fi
    sleep 2
done

# ── 4. Prune old images ───────────────────────────────────────────────────────
log "Pruning dangling images..."
docker image prune -f > /dev/null 2>&1 || true

log "Deploy completed successfully"
docker compose ps | tee -a "$LOG_FILE"
log "========================================="
