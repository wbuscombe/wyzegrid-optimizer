#!/usr/bin/env bash
# Deploy wyzegrid-optimizer to the NAS.
#
# rsync with --delete is intentional (so removed files in the repo also leave
# the NAS), but NAS-only files (.env, docker-compose.override.yml, data/) MUST
# be preserved. The exclude list below is the source of truth — extend it if
# you add another NAS-only artifact.
set -euo pipefail

NAS_HOST="${NAS_HOST:-cargo@192.168.50.3}"
NAS_DIR="${NAS_DIR:-/home/cargo/docker/wyzegrid-optimizer}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

EXCLUDES=(
  --exclude='.git/'
  --exclude='venv/'
  --exclude='.venv/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='data/'
  # NAS-only — must not be clobbered by --delete
  # Keep the tracked example deployable, then protect every runtime .env variant
  # (the live file plus dated/operator backup siblings) from transfer and delete.
  --include='.env.example'
  --exclude='.env*'
  --exclude='docker-compose.override.yml'
)

echo "→ rsync $REPO_ROOT/ → $NAS_HOST:$NAS_DIR/"
rsync -av --delete "${EXCLUDES[@]}" "$REPO_ROOT/" "$NAS_HOST:$NAS_DIR/"

echo ""
echo "→ build + recreate on NAS"
ssh "$NAS_HOST" "cd $NAS_DIR && docker compose build 2>&1 | tail -3 && docker compose up -d --force-recreate 2>&1 | tail -4"

echo ""
echo "→ wait for healthchecks"
sleep 12
ssh "$NAS_HOST" "cd $NAS_DIR && docker compose ps"

echo ""
echo "Dashboard: http://192.168.50.11:5004"
