#!/usr/bin/env bash
set -a
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/.env" ]]; then
  source "$(dirname "${BASH_SOURCE[0]}")/.env"
fi
set +a
