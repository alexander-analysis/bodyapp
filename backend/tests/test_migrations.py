import sqlite3
from pathlib import Path

import pytest

from app import db, migrate

EXPECTED_TABLES = {
    "users", "health_events", "weight_logs", "foods", "food_entries", "workouts", "exercises",
    "exercise_sets", "targets", "daily_rollup", "tdee_estimates", "llm_calls", "favorites", "idempotency_keys",
}
LOG_TABLES_WITH_EVENT_FK = {"weight_logs", "food_entries", "workouts", "daily_rollup"}


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    path = tmp_path / "health.db"
    migrate.upgrade(f"sqlite:///{path.as_posix()}")
    return path


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    return {r["name"]: dict(r) for r in conn.execute(f"PRAGMA table_info({table})")}


def test_upgrade_creates_every_table_and_records_the_revision(migrated):
    conn = db.connect(migrated)
    assert EXPECTED_TABLES <= tables(conn)
    assert db.schema_version(conn) == "0001"
    conn.close()


def test_every_log_table_carries_the_health_event_fk(migrated):
    conn = db.connect(migrated)
    for table in LOG_TABLES_WITH_EVENT_FK:
        cols = columns(conn, table)
        assert "health_event_id" in cols, table
        assert cols["health_event_id"]["notnull"] == 0, f"{table}.health_event_id must be nullable"
        fks = {r["from"]: r["table"] for r in conn.execute(f"PRAGMA foreign_key_list({table})")}
        assert fks.get("health_event_id") == "health_events", table
    assert "goal_weight_kg" in columns(conn, "users")
    conn.close()


def test_pragmas_and_fk_enforcement(migrated):
    conn = db.connect(migrated)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("INSERT INTO users (id, name, sex, birth_date, height_cm) VALUES (1, 'a', 'm', '2002-03-15', 180)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO weight_logs (user_id, logged_on, weight_kg, health_event_id) VALUES (1, '2026-06-01', 80, 999)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # CHECK constraints from the spec
        conn.execute("INSERT INTO weight_logs (user_id, logged_on, weight_kg) VALUES (1, '2026-06-01', 20)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO targets (user_id, effective_from, kcal, protein_g, fat_g_min, phase, reason, set_by) "
            "VALUES (1, '2026-06-01', 2000, 150, 80, 'bulk', 'x', 'engine')"
        )
    conn.close()


def test_set_cascade_on_workout_delete(migrated):
    conn = db.connect(migrated)
    conn.execute("INSERT INTO users (id, name, sex, birth_date, height_cm) VALUES (1, 'a', 'm', '2002-03-15', 180)")
    conn.execute("INSERT INTO exercises (id, name, muscle_group) VALUES (1, 'Squat', 'quads')")
    conn.execute("INSERT INTO workouts (id, user_id, performed_on) VALUES (1, 1, '2026-06-01')")
    conn.execute("INSERT INTO exercise_sets (workout_id, exercise_id, set_index, weight_kg, reps) VALUES (1, 1, 1, 100, 8)")
    conn.execute("DELETE FROM workouts WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM exercise_sets").fetchone()[0] == 0
    conn.close()


def test_upgrade_is_idempotent_and_downgrade_is_clean(migrated):
    url = f"sqlite:///{migrated.as_posix()}"
    migrate.upgrade(url)  # second run: no-op
    migrate.downgrade(url)
    conn = db.connect(migrated)
    assert not (EXPECTED_TABLES & tables(conn))
    conn.close()
    migrate.upgrade(url)
    conn = db.connect(migrated)
    assert EXPECTED_TABLES <= tables(conn)
    conn.close()


def test_clean_where_helper():
    assert db.clean_where() == " AND health_event_id IS NULL"
    assert db.clean_where("r") == " AND r.health_event_id IS NULL"


def test_transaction_rolls_back_on_error(migrated):
    conn = db.connect(migrated)
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            conn.execute("INSERT INTO users (id, name, sex, birth_date, height_cm) VALUES (1, 'a', 'm', '2002-03-15', 180)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    conn.close()
