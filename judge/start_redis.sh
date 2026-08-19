#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_DIR="${REDIS_DATA_DIR:-$PROJECT_ROOT/data/redis}"
REDIS_PORT="${REDIS_PORT:-6387}"

mkdir -p "$REDIS_DIR"
exec redis-server \
  --bind 127.0.0.1 \
  --port "$REDIS_PORT" \
  --protected-mode yes \
  --dir "$REDIS_DIR" \
  --appendonly yes \
  --save "" \
  --daemonize no
