#!/bin/bash
# Auto-deploy script — runs on every push to main
# Called by webhook_server.py

set -euo pipefail

REPO_DIR="/opt/viralengine"
WORKER_DIR="$REPO_DIR/worker"
LOG_FILE="/var/log/viralengine-deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "Deploy started"

# ── 1. Pull latest code ───────────────────────────────────────────────────────
log "Pulling latest code from main..."
cd "$REPO_DIR"
git fetch origin main
git reset --hard origin/main
log "Git pull complete — $(git log --oneline -1)"

# ── 2. Install/update Python dependencies ────────────────────────────────────
log "Installing Python dependencies..."
source "$WORKER_DIR/venv/bin/activate"
pip install -r "$WORKER_DIR/requirements.txt" -q --no-cache-dir
deactivate
log "Dependencies updated"

# ── 3. Restart worker service ─────────────────────────────────────────────────
log "Restarting viralengine-worker..."
systemctl restart viralengine-worker
sleep 2

# ── 4. Verify service is running ──────────────────────────────────────────────
if systemctl is-active --quiet viralengine-worker; then
    log "✅ Worker restarted successfully"
else
    log "❌ Worker failed to start — check: journalctl -u viralengine-worker -n 50"
    exit 1
fi

log "Deploy completed"
log "========================================="
