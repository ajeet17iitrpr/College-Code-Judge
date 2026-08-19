#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source "$ROOT/load_env.sh"
mkdir -p "$ROOT/logs" "$ROOT/run"

start_one() {
  local name="$1"; shift
  if [[ -f "$ROOT/run/$name.pid" ]] && kill -0 "$(cat "$ROOT/run/$name.pid")" 2>/dev/null; then
    echo "$name already running"
    return
  fi
  nohup "$@" >"$ROOT/logs/$name.log" 2>&1 &
  echo $! >"$ROOT/run/$name.pid"
  echo "Started $name (PID $!)"
}

start_one redis "$ROOT/judge/start_redis.sh"
start_one worker "$ROOT/judge/start_worker.sh"
start_one backend bash -lc "cd '$ROOT/backend' && exec python run.py"
start_one frontend bash -lc "cd '$ROOT/frontend' && exec python server.py"

echo "Student URL: http://172.26.1.171:${FRONTEND_PORT:-8080}"
echo "Logs: $ROOT/logs"
