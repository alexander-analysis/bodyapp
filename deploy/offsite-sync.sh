#!/usr/bin/env bash
# /opt/health/offsite-sync.sh — health-offsite.timer, weekly.
# A backup that only exists on the Pi is not a backup. Set OFFSITE_REMOTE in .env
# to an rclone remote path (e.g. "b2:my-bucket/health" or "othermachine:backups/health")
# after `rclone config`; until then this only logs that it is not configured.
set -euo pipefail
cd /opt/health
remote="$(grep -E '^OFFSITE_REMOTE=' .env | cut -d= -f2- | tr -d '[:space:]' || true)"
data="$(grep -E '^HEALTH_DATA_DIR=' .env | cut -d= -f2- | tr -d '[:space:]' || true)"
data="${data:-/mnt/storage/health}"
if [[ -z "$remote" ]]; then
  logger -t health-offsite "OFFSITE_REMOTE not set in /opt/health/.env; skipping"
  exit 0
fi
command -v rclone >/dev/null || { logger -t health-offsite "rclone not installed"; exit 1; }
rclone sync "$data/backups" "$remote" --include 'health-*.db.gz' 2>&1 | logger -t health-offsite
logger -t health-offsite "synced $data/backups -> $remote"
