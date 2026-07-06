#!/bin/bash
# One-shot migration script for OVHcloud VPS (ubuntu user)
# Run ON THE VPS after copying the project + .env
#
# Prerequisites (from your local machine):
#   1. Push latest code to GitHub
#   2. scp .env ubuntu@51.79.50.95:~/viralengine/.env
#   3. ssh ubuntu@51.79.50.95
#   4. bash deploy/migrate-ovh.sh

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/viralengine}"
VPS_IP="${VPS_IP:-51.79.50.95}"

log() { echo "[migrate] $(date '+%H:%M:%S') $*"; }

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "ERROR: $APP_DIR/.env not found."
    echo "Copy it from your machine:"
    echo "  scp .env ubuntu@${VPS_IP}:~/viralengine/.env"
    exit 1
fi

cd "$APP_DIR"

# ── Strip Windows-only vars from .env ─────────────────────────────────────────
if grep -q 'FFMPEG_PATH=' .env 2>/dev/null; then
    log "Removing FFMPEG_PATH (not needed in Docker)..."
    sed -i '/^FFMPEG_PATH=/d' .env
fi

# ── Ensure production vars ────────────────────────────────────────────────────
ensure_var() {
    local key="$1" val="$2"
    if grep -q "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${val}|" .env
    else
        echo "${key}=${val}" >> .env
    fi
}

ensure_var "NODE_ENV" "production"
ensure_var "ENVIRONMENT" "production"
ensure_var "MAX_WORKERS" "3"

if grep -q 'tu-frontend.com' .env 2>/dev/null; then
    log "WARNING: FRONTEND_URL still has placeholder (tu-frontend.com)."
    log "         Update .env with your real Vercel URL for CORS/billing redirects."
fi

# ── System setup (Docker, UFW, Caddy) ─────────────────────────────────────────
log "Running VPS setup (requires sudo)..."
sudo bash deploy/setup-vps.sh

# Add ubuntu to docker group (setup-vps runs as root)
sudo usermod -aG docker ubuntu 2>/dev/null || true

# ── Caddy — HTTP via IP ───────────────────────────────────────────────────────
log "Configuring Caddy (HTTP on port 80)..."
sudo cp deploy/Caddyfile.ip-only /etc/caddy/Caddyfile
sudo systemctl reload caddy

# ── Build and start ───────────────────────────────────────────────────────────
log "Building and starting Docker containers..."
# Use sg to apply docker group if we just added it
if groups | grep -q docker; then
    docker compose up -d --build
else
    sudo docker compose up -d --build
fi

# ── Health check ──────────────────────────────────────────────────────────────
log "Waiting for backend..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:3000/health > /dev/null 2>&1; then
        log "Backend healthy on localhost"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log "Health check failed. Logs:"
        docker compose logs --tail=40 2>/dev/null || sudo docker compose logs --tail=40
        exit 1
    fi
    sleep 3
done

if curl -sf "http://${VPS_IP}/health" > /dev/null 2>&1; then
    log "API reachable at http://${VPS_IP}/health"
else
    log "WARNING: http://${VPS_IP}/health not reachable externally."
    log "         Check OVH firewall allows ports 80 and 443."
fi

echo ""
echo "========================================="
echo " Migration complete!"
echo "========================================="
echo ""
echo " API URL:  http://${VPS_IP}"
echo " Health:   http://${VPS_IP}/health"
echo ""
echo " Next steps:"
echo "  1. Update Vercel: NEXT_PUBLIC_API_URL=http://${VPS_IP}"
echo "  2. Update .env FRONTEND_URL with your Vercel URL"
echo "  3. Test: submit a YouTube URL from the dashboard"
echo "  4. When you have a domain, switch to deploy/Caddyfile.example + HTTPS"
echo ""
echo " Logs:  docker compose logs -f"
echo "========================================="
