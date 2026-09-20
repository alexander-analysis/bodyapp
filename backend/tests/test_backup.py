import gzip
import os
import time
from pathlib import Path

import pytest

from app import backup, db, migrate


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    path = tmp_path / "health.db"
    migrate.upgrade(f"sqlite:///{path.as_posix()}")
    conn = db.connect(path)
    conn.execute("INSERT INTO users (id, name, sex, birth_date, height_cm) VALUES (1, 'a', 'm', '2002-03-15', 180)")
    conn.execute("INSERT INTO weight_logs (user_id, logged_on, weight_kg) VALUES (1, '2026-06-01', 80.0)")
    # leave the connection open: a live WAL database with an active reader
    yield path
    conn.close()


def test_backup_restore_round_trip(live_db: Path, tmp_path: Path):
    out = backup.backup(live_db, tmp_path / "backups", stamp="2026-09-20")
    assert out.name == "health-2026-09-20.db.gz" and out.exists()
    assert not (tmp_path / "backups" / "health-2026-09-20.db").exists()  # raw copy removed
    with gzip.open(out, "rb") as f:
        assert f.read(16) == b"SQLite format 3\x00"

    restored = backup.restore(out, tmp_path / "restored" / "health.db")
    conn = db.connect(restored)
    assert conn.execute("SELECT weight_kg FROM weight_logs").fetchone()[0] == 80.0
    assert db.schema_version(conn) == "0003"
    conn.close()

    info = backup.verify(out)
    assert info["integrity"] == "ok" and info["users"] == 1 and info["weight_logs"] == 1 and info["schema"] == "0003"


def test_restore_refuses_to_overwrite(live_db: Path, tmp_path: Path):
    out = backup.backup(live_db, tmp_path / "backups")
    with pytest.raises(FileExistsError):
        backup.restore(out, live_db)


def test_prune_keeps_thirty_days(tmp_path: Path):
    d = tmp_path / "backups"
    d.mkdir()
    old = d / "health-2026-01-01.db.gz"
    fresh = d / "health-2026-09-19.db.gz"
    other = d / "notes.txt"
    for f in (old, fresh, other):
        f.write_bytes(b"x")
    stale = time.time() - 40 * 86400
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    removed = backup.prune(d, keep_days=30)
    assert removed == [old]
    assert fresh.exists() and other.exists()


def test_cli_backup_and_verify(live_db: Path, tmp_path: Path, capsys):
    assert backup.main(["--db", str(live_db), "--dir", str(tmp_path / "b")]) == 0
    out = next((tmp_path / "b").glob("health-*.db.gz"))
    assert backup.main(["--verify", str(out)]) == 0
    assert "integrity: ok" in capsys.readouterr().out
