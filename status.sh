#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for name in redis worker backend frontend; do
  file="$ROOT/run/$name.pid"
  if [[ -f "$file" ]] && kill -0 "$(cat "$file")" 2>/dev/null; then
    echo "$name: running (PID $(cat "$file"))"
  else
    echo "$name: stopped"
  fi
done
