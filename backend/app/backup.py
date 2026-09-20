"""Backups via SQLite's online backup API — never ``cp`` a live WAL database.

    python -m app.backup                      # write BACKUP_DIR/health-YYYY-MM-DD.db.gz, prune > KEEP_DAYS
    python -m app.backup --restore FILE.gz --to /path/new.db
    python -m app.backup --verify FILE.gz     # integrity_check + row counts, no side effects

Runs inside the api container from the ``health-backup.timer`` on the Pi, so
the host needs no sqlite3 binary. ``/api/v1/health`` reports the age of the
newest file in BACKUP_DIR.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

KEEP_DAYS = 30
PREFIX = "health-"
TABLES_TO_COUNT = ("users", "weight_logs", "food_entries", "workouts", "targets", "health_events")


def backup(db_path: Path, backup_dir: Path, *, stamp: str | None = None, keep_days: int = KEEP_DAYS) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or date.today().isoformat()
    raw = backup_dir / f"{PREFIX}{stamp}.db"
    out = backup_dir / f"{PREFIX}{stamp}.db.gz"

    src = sqlite3.connect(db_path)
    try:
        if raw.exists():
            raw.unlink()
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    with raw.open("rb") as f_in, gzip.open(out, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw.unlink()
    prune(backup_dir, keep_days)
    return out


def prune(backup_dir: Path, keep_days: int = KEEP_DAYS) -> list[Path]:
    cutoff = time.time() - keep_days * 86400
    removed = []
    for f in backup_dir.glob(f"{PREFIX}*.db.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed.append(f)
    return removed


def restore(archive: Path, to: Path) -> Path:
    """Decompress ``archive`` to ``to``. Refuses to overwrite an existing file."""
    if to.exists():
        raise FileExistsError(f"{to} exists; restore to a new path, verify, then swap it in")
    to.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as f_in, to.open("wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return to


def verify(archive: Path) -> dict[str, int | str]:
    """Restore to a temp file, run integrity_check, count the main tables."""
    with tempfile.TemporaryDirectory() as tmp:
        path = restore(archive, Path(tmp) / "verify.db")
        conn = sqlite3.connect(path)
        try:
            result: dict[str, int | str] = {"integrity": conn.execute("PRAGMA integrity_check").fetchone()[0]}
            existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in TABLES_TO_COUNT:
                if t in existing:
                    result[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone() if "alembic_version" in existing else None
            result["schema"] = row[0] if row else "none"
        finally:
            conn.close()
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--restore", type=Path, metavar="FILE.gz")
    ap.add_argument("--to", type=Path, metavar="NEW.db")
    ap.add_argument("--verify", type=Path, metavar="FILE.gz")
    ap.add_argument("--db", type=Path, help="override DB_PATH")
    ap.add_argument("--dir", type=Path, help="override BACKUP_DIR")
    args = ap.parse_args(argv)

    if args.verify:
        for k, v in verify(args.verify).items():
            print(f"{k}: {v}")
        return 0
    if args.restore:
        if not args.to:
            ap.error("--restore needs --to")
        print(restore(args.restore, args.to))
        return 0

    if args.db and args.dir:
        db_path, backup_dir = args.db, args.dir
    else:
        from .config import get_settings

        cfg = get_settings()
        db_path, backup_dir = args.db or cfg.db_path, args.dir or cfg.backup_dir
    out = backup(db_path, backup_dir)
    print(f"{out} ({out.stat().st_size} bytes); verify: {verify(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
