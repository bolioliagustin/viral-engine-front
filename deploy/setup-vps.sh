#!/bin/bash
# Initial VPS setup for OVHcloud — run once as root on a fresh Ubuntu 26.04 server.
#
# Usage:
#   curl -fsSL <raw-url>/deploy/setup-vps.sh | bash
#   # or after cloning:
#   sudo bash deploy/setup-vps.sh
#
# What it does:
#   1. System updates + basic packages
#   2. Docker Engine + Compose plugin
#   3. UFW firewall (22, 80, 443)
#   4. Caddy reverse proxy
#   5. Creates /opt/viralengine directory

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/viralengine}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"

log() { echo "[setup] $(date '+%H:%M:%S') $*"; }

if [[ $EUID -ne 0 ]]; then
  log "Re-running with sudo..."
  exec sudo bash "$0" "$@"
fi

log "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq git curl ufw ca-certificates gnupg

# ── Docker ────────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  log "Installing Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable docker
  systemctl start docker
  # Allow ubuntu (or deploy user) to run docker without sudo
  if id "$DEPLOY_USER" &>/dev/null; then
    usermod -aG docker "$DEPLOY_USER"
  fi
  log "Docker installed: $(docker --version)"
else
  log "Docker already installed: $(docker --version)"
fi

# ── Firewall ──────────────────────────────────────────────────────────────────
log "Configuring UFW firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
log "UFW active — ports 22, 80, 443 open. Port 3000 is NOT exposed (backend is localhost-only)."

# ── Caddy ─────────────────────────────────────────────────────────────────────
if ! command -v caddy &>/dev/null; then
  log "Installing Caddy..."
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
  systemctl enable caddy
  log "Caddy installed: $(caddy version)"
else
  log "Caddy already installed"
fi

# ── App directory ─────────────────────────────────────────────────────────────
mkdir -p "$REPO_DIR"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$REPO_DIR" 2>/dev/null || true
log "Created $REPO_DIR (owner: $DEPLOY_USER)"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo " VPS setup complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Clone the repo (as ubuntu):"
echo "       git clone https://github.com/bolioliagustin/viral-engine-front.git ~/viralengine"
echo "  2. Copy .env from your machine:"
echo "       scp .env ubuntu@51.79.50.95:~/viralengine/.env"
echo "  3. Run migration:"
echo "       cd ~/viralengine && bash deploy/migrate-ovh.sh"
echo ""
