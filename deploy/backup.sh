#!/usr/bin/env bash
# /opt/health/backup.sh — health-backup.timer, daily 04:00.
# Uses SQLite's online backup API from inside the api container (no host sqlite3),
# writes /data/backups/health-YYYY-MM-DD.db.gz, keeps 30 days, verifies the result.
set -euo pipefail
cd /opt/health
docker compose exec -T api python -m app.backup | logger -t health-backup
