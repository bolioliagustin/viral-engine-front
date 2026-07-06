#!/bin/bash
# Switch OVH VPS to worker-only mode (API stays on Render).
# Stops the local backend container and runs only the worker.

set -euo pipefail

cd "${APP_DIR:-$HOME/viralengine}"

echo "[worker-only] Stopping full stack..."
docker compose down 2>/dev/null || sudo docker compose down 2>/dev/null || true

echo "[worker-only] Starting worker..."
if docker info > /dev/null 2>&1; then
    docker compose -f docker-compose.worker.yml up -d --build
    docker compose -f docker-compose.worker.yml ps
    docker compose -f docker-compose.worker.yml logs --tail=20
else
    sudo docker compose -f docker-compose.worker.yml up -d --build
    sudo docker compose -f docker-compose.worker.yml ps
    sudo docker compose -f docker-compose.worker.yml logs --tail=20
fi

echo ""
echo "Worker-only mode active. API remains on Render."
echo "Logs: docker compose -f docker-compose.worker.yml logs -f"
