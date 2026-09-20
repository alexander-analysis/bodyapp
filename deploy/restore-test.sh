#!/usr/bin/env bash
# /opt/health/restore-test.sh — prove the newest backup restores (spec 14: test a
# restore once before there is data worth losing). Read-only: restores into a temp
# file inside the container, runs integrity_check, prints row counts.
set -euo pipefail
cd /opt/health
data="$(grep -E '^HEALTH_DATA_DIR=' .env | cut -d= -f2- | tr -d '[:space:]' || true)"
data="${data:-/mnt/storage/health}"
newest=$(ls -1t "$data"/backups/health-*.db.gz 2>/dev/null | head -1 || true)
[[ -n "$newest" ]] || { echo "no backups in $data/backups yet — run ./backup.sh first"; exit 1; }
echo "verifying $(basename "$newest")"
docker compose exec -T api python -m app.backup --verify "/data/backups/$(basename "$newest")"
cat <<MSG

To actually restore:
  docker compose stop api
  mv $data/health.db $data/health.db.pre-restore   # keep the old one
  rm -f $data/health.db-wal $data/health.db-shm
  docker compose run --rm api python -m app.backup --restore /data/backups/$(basename "$newest") --to /data/health.db
  docker compose start api && curl -fsS http://localhost:8000/api/v1/health
MSG
