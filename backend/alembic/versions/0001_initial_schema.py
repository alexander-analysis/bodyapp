"""initial schema (spec section 4)

Every log table carries a nullable ``health_event_id`` from the first row
written — retrofitting it later cannot recover contaminated rows.

Additions beyond the spec DDL, both nullable / additive:
  * ``users.goal_weight_kg`` — needed by the "phase complete" rule in 5.3.
  * ``idempotency_keys`` — the API contract in section 10 dedupes mutating
    requests on an ``Idempotency-Key`` header for 24 hours.

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES: list[tuple[str, str]] = [
    (
        "users",
        """
        CREATE TABLE users (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          sex TEXT CHECK(sex IN ('m','f')) NOT NULL,
          birth_date TEXT NOT NULL,
          height_cm REAL NOT NULL,
          goal_weight_kg REAL,
          timezone TEXT NOT NULL DEFAULT 'Europe/Madrid',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ),
    (
        "health_events",
        """
        -- Illness, injury, travel, exam. Rows here exclude data from analytics.
        CREATE TABLE health_events (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          type TEXT CHECK(type IN ('illness','injury','travel','exam')) NOT NULL,
          severity TEXT CHECK(severity IN ('mild','moderate','gi','none')) NOT NULL DEFAULT 'none',
          fever_flag INTEGER NOT NULL DEFAULT 0,
          symptoms_json TEXT,
          started_at TEXT NOT NULL,
          ended_at TEXT,
          ramp_until TEXT,
          created_by TEXT CHECK(created_by IN ('user','suggested')) NOT NULL DEFAULT 'user',
          notes TEXT
        )
        """,
    ),
    (
        "weight_logs",
        """
        CREATE TABLE weight_logs (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          logged_on TEXT NOT NULL,
          weight_kg REAL NOT NULL CHECK(weight_kg BETWEEN 30 AND 300),
          waist_cm REAL,
          health_event_id INTEGER REFERENCES health_events(id),
          source TEXT NOT NULL DEFAULT 'manual',
          UNIQUE(user_id, logged_on)
        )
        """,
    ),
    (
        "foods",
        """
        -- Canonical nutrition per 100 g. Populated from Open Food Facts or Gemini.
        CREATE TABLE foods (
          id INTEGER PRIMARY KEY,
          barcode TEXT UNIQUE,
          name TEXT NOT NULL,
          brand TEXT,
          kcal_100g REAL NOT NULL,
          protein_100g REAL NOT NULL,
          carbs_100g REAL NOT NULL,
          fat_100g REAL NOT NULL,
          fibre_100g REAL DEFAULT 0,
          source TEXT CHECK(source IN ('off','gemini','manual','user')) NOT NULL,
          verified INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ),
    (
        "food_entries",
        """
        CREATE TABLE food_entries (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          food_id INTEGER REFERENCES foods(id),
          logged_at TEXT NOT NULL,
          logged_on TEXT NOT NULL,
          meal TEXT CHECK(meal IN ('breakfast','lunch','dinner','snack')),
          grams REAL NOT NULL,
          kcal REAL NOT NULL,
          protein_g REAL NOT NULL,
          carbs_g REAL NOT NULL,
          fat_g REAL NOT NULL,
          fibre_g REAL DEFAULT 0,
          alcohol_g REAL DEFAULT 0,
          input_method TEXT CHECK(input_method IN ('barcode','photo','text','favorite','manual')) NOT NULL,
          confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
          photo_path TEXT,
          health_event_id INTEGER REFERENCES health_events(id)
        )
        """,
    ),
    (
        "workouts",
        """
        CREATE TABLE workouts (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          performed_on TEXT NOT NULL,
          template TEXT,
          duration_min INTEGER,
          rpe INTEGER CHECK(rpe BETWEEN 1 AND 10),
          health_event_id INTEGER REFERENCES health_events(id),
          notes TEXT
        )
        """,
    ),
    (
        "exercises",
        """
        CREATE TABLE exercises (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          muscle_group TEXT NOT NULL,
          secondary_groups TEXT,
          tier INTEGER NOT NULL DEFAULT 2,
          increment_kg REAL NOT NULL DEFAULT 2.5,
          rep_min INTEGER NOT NULL DEFAULT 8,
          rep_max INTEGER NOT NULL DEFAULT 12
        )
        """,
    ),
    (
        "exercise_sets",
        """
        CREATE TABLE exercise_sets (
          id INTEGER PRIMARY KEY,
          workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
          exercise_id INTEGER NOT NULL REFERENCES exercises(id),
          set_index INTEGER NOT NULL,
          weight_kg REAL NOT NULL,
          reps INTEGER NOT NULL,
          rir INTEGER,
          is_warmup INTEGER NOT NULL DEFAULT 0
        )
        """,
    ),
    (
        "targets",
        """
        -- Versioned. Never UPDATE a row; always insert a new one with a reason.
        CREATE TABLE targets (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          effective_from TEXT NOT NULL,
          kcal INTEGER NOT NULL,
          protein_g INTEGER NOT NULL,
          fat_g_min INTEGER NOT NULL,
          fibre_g INTEGER NOT NULL DEFAULT 30,
          steps INTEGER NOT NULL DEFAULT 9000,
          phase TEXT CHECK(phase IN ('cut','maintain','gain')) NOT NULL,
          reason TEXT NOT NULL,
          set_by TEXT CHECK(set_by IN ('engine','user')) NOT NULL
        )
        """,
    ),
    (
        "daily_rollup",
        """
        CREATE TABLE daily_rollup (
          user_id INTEGER NOT NULL REFERENCES users(id),
          day TEXT NOT NULL,
          kcal REAL, protein_g REAL, carbs_g REAL, fat_g REAL, fibre_g REAL,
          alcohol_g REAL, water_ml INTEGER, steps INTEGER, sleep_h REAL,
          trend_weight_kg REAL,
          mean_confidence REAL,
          logged_complete INTEGER NOT NULL DEFAULT 0,
          health_event_id INTEGER REFERENCES health_events(id),
          PRIMARY KEY (user_id, day)
        )
        """,
    ),
    (
        "tdee_estimates",
        """
        CREATE TABLE tdee_estimates (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          computed_on TEXT NOT NULL,
          window_days INTEGER NOT NULL,
          tdee_kcal REAL NOT NULL,
          confidence REAL NOT NULL,
          method TEXT CHECK(method IN ('formula','adaptive')) NOT NULL
        )
        """,
    ),
    (
        "llm_calls",
        """
        -- Audit every LLM call. Non-negotiable for debugging and cost control.
        CREATE TABLE llm_calls (
          id INTEGER PRIMARY KEY,
          called_at TEXT NOT NULL DEFAULT (datetime('now')),
          purpose TEXT NOT NULL,
          model TEXT NOT NULL,
          input_tokens INTEGER,
          output_tokens INTEGER,
          latency_ms INTEGER,
          ok INTEGER NOT NULL,
          error TEXT,
          request_hash TEXT,
          response_json TEXT
        )
        """,
    ),
    (
        "favorites",
        """
        CREATE TABLE favorites (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          label TEXT NOT NULL,
          items_json TEXT NOT NULL,
          use_count INTEGER NOT NULL DEFAULT 0,
          last_used TEXT
        )
        """,
    ),
    (
        "idempotency_keys",
        """
        -- Mutating requests dedupe on an Idempotency-Key header for 24 hours (spec 10).
        CREATE TABLE idempotency_keys (
          key TEXT PRIMARY KEY,
          route TEXT NOT NULL,
          status_code INTEGER NOT NULL,
          response_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ),
]

INDEXES: list[str] = [
    "CREATE INDEX idx_health_events_active ON health_events(user_id, ended_at)",
    "CREATE INDEX idx_foods_name ON foods(name)",
    "CREATE INDEX idx_food_entries_day ON food_entries(user_id, logged_on)",
    "CREATE INDEX idx_sets_exercise ON exercise_sets(exercise_id)",
    "CREATE INDEX idx_sets_workout ON exercise_sets(workout_id)",
    "CREATE INDEX idx_workouts_day ON workouts(user_id, performed_on)",
    "CREATE INDEX idx_targets_effective ON targets(user_id, effective_from)",
    "CREATE INDEX idx_tdee_computed ON tdee_estimates(user_id, computed_on)",
    "CREATE INDEX idx_llm_calls_called_at ON llm_calls(called_at)",
    "CREATE INDEX idx_idempotency_created ON idempotency_keys(created_at)",
]


def upgrade() -> None:
    for _name, ddl in TABLES:
        op.execute(ddl)
    for ddl in INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
