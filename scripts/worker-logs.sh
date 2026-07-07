#!/usr/bin/env bash
# Worker log viewer — fast tail/grep without slow `docker compose logs`.
#
# Usage (on VPS from repo root):
#   bash scripts/worker-logs.sh              # follow live log file
#   bash scripts/worker-logs.sh tail 200     # last 200 lines
#   bash scripts/worker-logs.sh job <uuid>     # filter by job id
#   bash scripts/worker-logs.sh edit <uuid>    # filter by clip_edit id
#   bash scripts/worker-logs.sh errors         # recent warnings/errors
#   bash scripts/worker-logs.sh docker         # fallback: docker compose logs
#   bash scripts/worker-logs.sh path           # print log file location

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${WORKER_LOG_DIR:-$ROOT/worker-logs}"
LOG_FILE="$LOG_DIR/worker.log"
ERR_FILE="$LOG_DIR/worker-error.log"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.worker.yml}"

cmd="${1:-tail}"
arg="${2:-}"

mkdir -p "$LOG_DIR"

case "$cmd" in
  path)
    echo "$LOG_FILE"
    ;;
  tail|"")
    if [[ ! -f "$LOG_FILE" ]]; then
      echo "Log file not found yet: $LOG_FILE"
      echo "Start the worker or run: bash scripts/worker-logs.sh docker"
      exit 1
    fi
    if [[ -n "$arg" && "$arg" =~ ^[0-9]+$ ]]; then
      tail -n "$arg" "$LOG_FILE"
    else
      tail -f "$LOG_FILE"
    fi
    ;;
  job)
    if [[ -z "$arg" ]]; then
      echo "Usage: bash scripts/worker-logs.sh job <job-id-prefix>"
      exit 1
    fi
    grep -F "job=$arg" "$LOG_FILE" 2>/dev/null | tail -n 200 || echo "No matches for job=$arg"
    ;;
  edit)
    if [[ -z "$arg" ]]; then
      echo "Usage: bash scripts/worker-logs.sh edit <edit-id-prefix>"
      exit 1
    fi
    grep -F "edit=$arg" "$LOG_FILE" 2>/dev/null | tail -n 200 || echo "No matches for edit=$arg"
    ;;
  errors)
    n="${arg:-80}"
    if [[ -f "$ERR_FILE" ]]; then
      tail -n "$n" "$ERR_FILE"
    else
      grep -iE 'ERROR|WARNING|❌|falló|failed' "$LOG_FILE" 2>/dev/null | tail -n "$n" \
        || echo "No log file at $LOG_FILE"
    fi
    ;;
  grep)
    if [[ -z "$arg" ]]; then
      echo "Usage: bash scripts/worker-logs.sh grep <pattern>"
      exit 1
    fi
    grep -iE "$arg" "$LOG_FILE" 2>/dev/null | tail -n 150 || echo "No matches"
    ;;
  docker)
    shift || true
    if docker info > /dev/null 2>&1; then
      docker compose -f "$ROOT/$COMPOSE_FILE" logs -f --tail="${1:-100}" worker
    else
      sudo docker compose -f "$ROOT/$COMPOSE_FILE" logs -f --tail="${1:-100}" worker
    fi
    ;;
  *)
    echo "Unknown command: $cmd"
    echo "Commands: tail, job, edit, errors, grep, docker, path"
    exit 1
    ;;
esac
