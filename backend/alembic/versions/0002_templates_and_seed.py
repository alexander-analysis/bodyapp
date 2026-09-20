"""session templates table + default exercise catalog

``templates`` is additive to the spec schema: ``/workout/next`` needs to know
which exercises make up a session. Exercises and templates are editable data,
seeded here so the app is usable on first run.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""
from __future__ import annotations

import json

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# id, name, muscle_group, secondary_groups, tier, increment_kg, rep_min, rep_max
EXERCISES: list[tuple[int, str, str, str, int, float, int, int]] = [
    (1, "Back squat", "quads", "glutes,hamstrings", 1, 5.0, 5, 8),
    (2, "Bench press", "chest", "triceps,front_delts", 1, 2.5, 6, 10),
    (3, "Deadlift", "hamstrings", "glutes,back", 1, 5.0, 3, 6),
    (4, "Overhead press", "shoulders", "triceps", 1, 2.5, 6, 10),
    (5, "Barbell row", "back", "biceps,rear_delts", 1, 2.5, 6, 10),
    (6, "Pull-up", "back", "biceps", 2, 2.5, 6, 10),
    (7, "Romanian deadlift", "hamstrings", "glutes,back", 2, 5.0, 8, 12),
    (8, "Incline dumbbell press", "chest", "triceps,front_delts", 2, 2.0, 8, 12),
    (9, "Lat pulldown", "back", "biceps", 2, 5.0, 8, 12),
    (10, "Seated cable row", "back", "biceps,rear_delts", 2, 5.0, 8, 12),
    (11, "Leg press", "quads", "glutes", 2, 10.0, 10, 15),
    (12, "Bulgarian split squat", "quads", "glutes", 2, 2.0, 8, 12),
    (13, "Leg curl", "hamstrings", "", 2, 2.5, 10, 15),
    (14, "Leg extension", "quads", "", 3, 2.5, 10, 15),
    (15, "Lateral raise", "side_delts", "", 3, 1.0, 12, 20),
    (16, "Face pull", "rear_delts", "", 3, 2.5, 12, 20),
    (17, "Dumbbell curl", "biceps", "", 3, 1.0, 10, 15),
    (18, "Triceps pushdown", "triceps", "", 3, 2.5, 10, 15),
    (19, "Cable crunch", "abs", "", 3, 2.5, 10, 15),
    (20, "Standing calf raise", "calves", "", 3, 5.0, 10, 15),
    (21, "Hip thrust", "glutes", "hamstrings", 2, 5.0, 8, 12),
    (22, "Dumbbell bench press", "chest", "triceps,front_delts", 2, 2.0, 8, 12),
]

# name, slot, [(exercise_id, sets)]
TEMPLATES: list[tuple[str, int, list[tuple[int, int]]]] = [
    ("Upper A", 1, [(2, 3), (5, 3), (4, 3), (9, 3), (15, 3), (17, 2), (18, 2)]),
    ("Lower A", 2, [(1, 3), (7, 3), (11, 3), (13, 3), (20, 3), (19, 3)]),
    ("Upper B", 3, [(8, 3), (6, 3), (22, 3), (10, 3), (16, 3), (17, 2), (18, 2)]),
    ("Lower B", 4, [(3, 3), (11, 3), (12, 3), (14, 3), (21, 3), (20, 3)]),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE templates (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          slot INTEGER NOT NULL,
          exercises_json TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    for eid, name, group, secondary, tier, inc, lo, hi in EXERCISES:
        op.execute(
            "INSERT OR IGNORE INTO exercises (id, name, muscle_group, secondary_groups, tier, increment_kg, rep_min, rep_max) "
            f"VALUES ({eid}, '{name}', '{group}', {'NULL' if not secondary else repr(secondary)}, {tier}, {inc}, {lo}, {hi})"
        )
    for name, slot, items in TEMPLATES:
        payload = json.dumps([{"exercise_id": eid, "sets": sets} for eid, sets in items])
        op.execute(f"INSERT OR IGNORE INTO templates (name, slot, exercises_json) VALUES ('{name}', {slot}, '{payload}')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS templates")
    op.execute(f"DELETE FROM exercises WHERE id IN ({', '.join(str(e[0]) for e in EXERCISES)})")
