#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

exec celery \
  -A judge.celery_app:celery_app \
  worker \
  --beat \
  --pool=solo \
  --concurrency=1 \
  --loglevel=INFO \
  --queues="${JUDGE_QUEUE:-judge}"
