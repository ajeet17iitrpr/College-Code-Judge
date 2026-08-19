#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this script as root or with sudo." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  build-essential \
  gcc \
  g++ \
  bubblewrap \
  redis-server \
  postgresql \
  postgresql-contrib \
  nginx \
  supervisor \
  sqlite3 \
  curl \
  ca-certificates \
  unzip \
  zip \
  procps \
  psmisc \
  lsof \
  jq \
  openssl \
  tzdata

apt-get clean
rm -rf /var/lib/apt/lists/*

echo
echo "System packages installed."
echo "Important: bubblewrap requires Linux user namespaces to be allowed by the outer host/container runtime."
echo "Because many containers do not run systemd, start Redis, PostgreSQL, Nginx and application processes using Supervisor or explicit commands."