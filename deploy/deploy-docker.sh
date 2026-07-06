#!/bin/bash
# Legacy wrapper — use deploy-worker.sh for production (worker-only on OVH).
exec "$(dirname "$0")/deploy-worker.sh" "$@"
