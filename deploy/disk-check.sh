#!/usr/bin/env bash
# /opt/health/disk-check.sh — health-disk.timer, weekly. Warn under 5 GB free.
set -euo pipefail
data="$(grep -E '^HEALTH_DATA_DIR=' /opt/health/.env | cut -d= -f2- | tr -d '[:space:]' || true)"
data="${data:-/mnt/storage/health}"
avail_gb=$(df --output=avail -BG "$data" | tail -1 | tr -dc '0-9')
photos_mb=$(du -sm "$data/photos" 2>/dev/null | cut -f1 || echo 0)
if (( avail_gb < 5 )); then
  logger -p user.warning -t health-disk "LOW DISK: ${avail_gb} GB free on $data (photos: ${photos_mb} MB)"
else
  logger -t health-disk "${avail_gb} GB free on $data (photos: ${photos_mb} MB)"
fi
