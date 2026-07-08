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
touch "$LOG_FILE" 2>/dev/null || LOG_FILE="/tmp/viralengine-deploy.log"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
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

if [[ -f .env ]] && grep -qE '^GROQ_API_KEY=.+' .env; then
    log "GROQ_API_KEY: configured (Whisper via Groq)"
else
    log "WARNING: GROQ_API_KEY missing in .env — Whisper fallback to OpenAI"
fi

if [[ -f .env ]] && grep -qE '^YOUTUBE_COOKIES=.+' .env; then
    log "YOUTUBE_COOKIES: configured (yt-dlp anti-bot)"
else
    log "WARNING: YOUTUBE_COOKIES missing in .env — yt-dlp fallará con 'Sign in to confirm you're not a bot'"
    log "         Render env vars NO llegan al worker Docker. Agregala en ~/viralengine/.env"
fi

log "Building and restarting worker (no cache)..."
dc -f "$COMPOSE_FILE" build --pull --no-cache
dc -f "$COMPOSE_FILE" up -d --remove-orphans --force-recreate

log "Worker status:"
dc -f "$COMPOSE_FILE" ps

(docker image prune -f > /dev/null 2>&1 || sudo docker image prune -f > /dev/null 2>&1) || true

log "Deploy completed"
log "Logs: bash scripts/worker-logs.sh tail   (or: tail -f worker-logs/worker.log)"
log "========================================="
