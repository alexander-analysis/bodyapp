"""Row access. Every function takes a ``sqlite3.Connection`` and returns plain
dicts or engine dataclasses. No business rules live here — those are in
``app.engine`` (pure) and ``app.services`` (orchestration).

Analytics queries append ``db.clean_where()``; nothing else spells out the
exclusion predicate (see CLAUDE.md, rule 3).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from . import db
from .engine.types import (
    DayRow,
    Exercise,
    FoodEntryRow,
    HealthEvent,
    SetRow,
    Target,
    TrendPoint,
    UserProfile,
    WeightPoint,
    WorkoutRow,
)

USER_ID = 1  # single user by design (spec 1)


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


# --- profile -------------------------------------------------------------------

def get_user(conn: sqlite3.Connection) -> dict | None:
    return _row(conn.execute("SELECT * FROM users WHERE id = ?", (USER_ID,)))


def upsert_user(conn: sqlite3.Connection, *, name: str, sex: str, birth_date: date, height_cm: float,
                goal_weight_kg: float | None, timezone: str) -> dict:
    conn.execute(
        """
        INSERT INTO users (id, name, sex, birth_date, height_cm, goal_weight_kg, timezone)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, sex = excluded.sex, birth_date = excluded.birth_date,
          height_cm = excluded.height_cm, goal_weight_kg = excluded.goal_weight_kg, timezone = excluded.timezone
        """,
        (USER_ID, name, sex, birth_date.isoformat(), height_cm, goal_weight_kg, timezone),
    )
    return get_user(conn)  # type: ignore[return-value]


def user_profile(conn: sqlite3.Connection) -> UserProfile | None:
    u = get_user(conn)
    if not u:
        return None
    return UserProfile(sex=u["sex"], birth_date=date.fromisoformat(u["birth_date"]), height_cm=u["height_cm"],
                       goal_weight_kg=u["goal_weight_kg"])


# --- health events -------------------------------------------------------------

def _event(r: dict) -> HealthEvent:
    return HealthEvent(
        id=r["id"], type=r["type"], started_at=date.fromisoformat(r["started_at"]), severity=r["severity"],
        fever_flag=bool(r["fever_flag"]), ended_at=_d(r["ended_at"]), ramp_until=_d(r["ramp_until"]),
        symptoms=json.loads(r["symptoms_json"]) if r["symptoms_json"] else {},
    )


def list_events(conn: sqlite3.Connection) -> list[HealthEvent]:
    return [_event(r) for r in _rows(conn.execute("SELECT * FROM health_events WHERE user_id = ? ORDER BY started_at", (USER_ID,)))]


def list_event_rows(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM health_events WHERE user_id = ? ORDER BY started_at DESC", (USER_ID,)))


def get_event(conn: sqlite3.Connection, event_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM health_events WHERE id = ? AND user_id = ?", (event_id, USER_ID)))


def insert_event(conn: sqlite3.Connection, *, type: str, severity: str, fever_flag: bool, symptoms: dict | None,
                 started_at: date, ended_at: date | None, ramp_until: date | None, created_by: str, notes: str | None) -> dict:
    cur = conn.execute(
        """
        INSERT INTO health_events (user_id, type, severity, fever_flag, symptoms_json, started_at, ended_at, ramp_until, created_by, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (USER_ID, type, severity, int(fever_flag), json.dumps(symptoms) if symptoms else None, started_at.isoformat(),
         ended_at.isoformat() if ended_at else None, ramp_until.isoformat() if ramp_until else None, created_by, notes),
    )
    return get_event(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


def update_event(conn: sqlite3.Connection, event_id: int, **fields: Any) -> dict | None:
    allowed = {"severity", "fever_flag", "symptoms_json", "ended_at", "ramp_until", "notes"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        assignments = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE health_events SET {assignments} WHERE id = ? AND user_id = ?", (*sets.values(), event_id, USER_ID))
    return get_event(conn, event_id)


def active_event_id(conn: sqlite3.Connection, day: date) -> int | None:
    """The event that tags rows logged on ``day`` (first active one, oldest first)."""
    for e in list_events(conn):
        if e.is_active_on(day):
            return e.id
    return None


# --- targets -------------------------------------------------------------------

def _target(r: dict) -> Target:
    return Target(effective_from=date.fromisoformat(r["effective_from"]), kcal=r["kcal"], protein_g=r["protein_g"],
                  fat_g_min=r["fat_g_min"], phase=r["phase"], reason=r["reason"], set_by=r["set_by"],
                  fibre_g=r["fibre_g"], steps=r["steps"])


def current_target_row(conn: sqlite3.Connection, day: date) -> dict | None:
    return _row(conn.execute(
        "SELECT * FROM targets WHERE user_id = ? AND effective_from <= ? ORDER BY effective_from DESC, id DESC LIMIT 1",
        (USER_ID, day.isoformat()),
    ))


def current_target(conn: sqlite3.Connection, day: date) -> Target | None:
    r = current_target_row(conn, day)
    return _target(r) if r else None


def list_targets(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM targets WHERE user_id = ? ORDER BY effective_from DESC, id DESC", (USER_ID,)))


def insert_target(conn: sqlite3.Connection, t: Target) -> dict:
    cur = conn.execute(
        """
        INSERT INTO targets (user_id, effective_from, kcal, protein_g, fat_g_min, fibre_g, steps, phase, reason, set_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (USER_ID, t.effective_from.isoformat(), t.kcal, t.protein_g, t.fat_g_min, t.fibre_g, t.steps, t.phase, t.reason, t.set_by),
    )
    return _row(conn.execute("SELECT * FROM targets WHERE id = ?", (cur.lastrowid,)))  # type: ignore[return-value]


def last_change_on(conn: sqlite3.Connection) -> date | None:
    r = _row(conn.execute("SELECT MAX(effective_from) AS d FROM targets WHERE user_id = ?", (USER_ID,)))
    return _d(r["d"]) if r else None


def last_stall_action(conn: sqlite3.Connection) -> str | None:
    r = _row(conn.execute(
        "SELECT reason FROM targets WHERE user_id = ? AND set_by = 'engine' AND reason LIKE 'stalled:%' "
        "ORDER BY effective_from DESC, id DESC LIMIT 1", (USER_ID,),
    ))
    if not r:
        return None
    return "steps" if "steps" in r["reason"].split(";")[-1] else "kcal"


# --- weights -------------------------------------------------------------------

def upsert_weight(conn: sqlite3.Connection, *, day: date, weight_kg: float, waist_cm: float | None, source: str,
                  health_event_id: int | None) -> dict:
    conn.execute(
        """
        INSERT INTO weight_logs (user_id, logged_on, weight_kg, waist_cm, source, health_event_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, logged_on) DO UPDATE SET weight_kg = excluded.weight_kg,
          waist_cm = COALESCE(excluded.waist_cm, weight_logs.waist_cm), source = excluded.source,
          health_event_id = excluded.health_event_id
        """,
        (USER_ID, day.isoformat(), weight_kg, waist_cm, source, health_event_id),
    )
    return _row(conn.execute("SELECT * FROM weight_logs WHERE user_id = ? AND logged_on = ?", (USER_ID, day.isoformat())))  # type: ignore[return-value]


def delete_weight(conn: sqlite3.Connection, day: date) -> bool:
    return conn.execute("DELETE FROM weight_logs WHERE user_id = ? AND logged_on = ?", (USER_ID, day.isoformat())).rowcount > 0


def list_weight_rows(conn: sqlite3.Connection, since: date | None = None) -> list[dict]:
    if since is None:
        return _rows(conn.execute("SELECT * FROM weight_logs WHERE user_id = ? ORDER BY logged_on", (USER_ID,)))
    return _rows(conn.execute("SELECT * FROM weight_logs WHERE user_id = ? AND logged_on >= ? ORDER BY logged_on",
                              (USER_ID, since.isoformat())))


def weight_points(conn: sqlite3.Connection) -> list[WeightPoint]:
    """All weigh-ins, tagged ones included: the trend engine does its own exclusion."""
    return [WeightPoint(date.fromisoformat(r["logged_on"]), r["weight_kg"], r["health_event_id"]) for r in list_weight_rows(conn)]


def latest_weight(conn: sqlite3.Connection) -> dict | None:
    return _row(conn.execute("SELECT * FROM weight_logs WHERE user_id = ? ORDER BY logged_on DESC LIMIT 1", (USER_ID,)))


# --- foods ---------------------------------------------------------------------

def get_food(conn: sqlite3.Connection, food_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM foods WHERE id = ?", (food_id,)))


def get_food_by_barcode(conn: sqlite3.Connection, barcode: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM foods WHERE barcode = ?", (barcode,)))


def search_foods(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
    """Name/brand substring match; the user's own and verified foods first, then by recent use."""
    like = f"%{q.strip()}%"
    return _rows(conn.execute(
        """
        SELECT f.*, (SELECT COUNT(*) FROM food_entries e WHERE e.food_id = f.id) AS use_count
        FROM foods f
        WHERE f.name LIKE ? OR f.brand LIKE ?
        ORDER BY (f.source IN ('user','manual')) DESC, f.verified DESC, use_count DESC, f.name
        LIMIT ?
        """,
        (like, like, limit),
    ))


def insert_food(conn: sqlite3.Connection, *, name: str, brand: str | None, kcal_100g: float, protein_100g: float,
                carbs_100g: float, fat_100g: float, fibre_100g: float, source: str, barcode: str | None = None,
                verified: bool = False) -> dict:
    cur = conn.execute(
        """
        INSERT INTO foods (barcode, name, brand, kcal_100g, protein_100g, carbs_100g, fat_100g, fibre_100g, source, verified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (barcode, name, brand, kcal_100g, protein_100g, carbs_100g, fat_100g, fibre_100g, source, int(verified)),
    )
    return get_food(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


# --- food entries --------------------------------------------------------------

def insert_entry(conn: sqlite3.Connection, *, food_id: int | None, logged_at: datetime, logged_on: date, meal: str | None,
                 grams: float, kcal: float, protein_g: float, carbs_g: float, fat_g: float, fibre_g: float, alcohol_g: float,
                 input_method: str, confidence: float, photo_path: str | None, health_event_id: int | None) -> dict:
    cur = conn.execute(
        """
        INSERT INTO food_entries (user_id, food_id, logged_at, logged_on, meal, grams, kcal, protein_g, carbs_g, fat_g,
                                  fibre_g, alcohol_g, input_method, confidence, photo_path, health_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (USER_ID, food_id, logged_at.isoformat(timespec="seconds"), logged_on.isoformat(), meal, grams, kcal, protein_g,
         carbs_g, fat_g, fibre_g, alcohol_g, input_method, confidence, photo_path, health_event_id),
    )
    return get_entry(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


def get_entry(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    return _row(conn.execute(
        "SELECT e.*, f.name AS food_name, f.brand AS food_brand FROM food_entries e LEFT JOIN foods f ON f.id = e.food_id "
        "WHERE e.id = ? AND e.user_id = ?", (entry_id, USER_ID),
    ))


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    row = get_entry(conn, entry_id)
    if row:
        conn.execute("DELETE FROM food_entries WHERE id = ? AND user_id = ?", (entry_id, USER_ID))
    return row


def entries_for_day(conn: sqlite3.Connection, day: date) -> list[dict]:
    return _rows(conn.execute(
        "SELECT e.*, f.name AS food_name, f.brand AS food_brand FROM food_entries e LEFT JOIN foods f ON f.id = e.food_id "
        "WHERE e.user_id = ? AND e.logged_on = ? ORDER BY e.logged_at, e.id", (USER_ID, day.isoformat()),
    ))


def entry_rows_for_day(conn: sqlite3.Connection, day: date) -> list[FoodEntryRow]:
    return [
        FoodEntryRow(day=day, kcal=r["kcal"], protein_g=r["protein_g"], carbs_g=r["carbs_g"], fat_g=r["fat_g"],
                     confidence=r["confidence"], fibre_g=r["fibre_g"] or 0.0, alcohol_g=r["alcohol_g"] or 0.0,
                     meal=r["meal"], input_method=r["input_method"], health_event_id=r["health_event_id"])
        for r in entries_for_day(conn, day)
    ]


def favorite_suggestions(conn: sqlite3.Connection, *, since: date, min_count: int = 3) -> list[dict]:
    """Foods logged >= min_count times since ``since`` whose portions cluster within
    +/-15% of their median, and that are not already a favorite item."""
    rows = _rows(conn.execute(
        """
        SELECT e.food_id, f.name, f.brand, e.grams FROM food_entries e JOIN foods f ON f.id = e.food_id
        WHERE e.user_id = ? AND e.logged_on >= ? AND e.food_id IS NOT NULL ORDER BY e.food_id, e.grams
        """,
        (USER_ID, since.isoformat()),
    ))
    already = set()
    for fav in list_favorites(conn):
        for item in fav["items"]:
            if item.get("food_id"):
                already.add(item["food_id"])
    out: list[dict] = []
    by_food: dict[int, list[dict]] = {}
    for r in rows:
        by_food.setdefault(r["food_id"], []).append(r)
    for food_id, group in by_food.items():
        if food_id in already or len(group) < min_count:
            continue
        grams = sorted(g["grams"] for g in group)
        median = grams[len(grams) // 2]
        close = [g for g in grams if abs(g - median) <= 0.15 * median]
        if len(close) >= min_count:
            out.append({"food_id": food_id, "name": group[0]["name"], "brand": group[0]["brand"],
                        "grams": round(median, 1), "times": len(close)})
    return out


# --- favorites -----------------------------------------------------------------

def list_favorites(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn.execute("SELECT * FROM favorites WHERE user_id = ? ORDER BY use_count DESC, last_used DESC, label", (USER_ID,)))
    for r in rows:
        r["items"] = json.loads(r.pop("items_json"))
    return rows


def get_favorite(conn: sqlite3.Connection, fav_id: int) -> dict | None:
    r = _row(conn.execute("SELECT * FROM favorites WHERE id = ? AND user_id = ?", (fav_id, USER_ID)))
    if r:
        r["items"] = json.loads(r.pop("items_json"))
    return r


def insert_favorite(conn: sqlite3.Connection, *, label: str, items: list[dict]) -> dict:
    cur = conn.execute("INSERT INTO favorites (user_id, label, items_json) VALUES (?, ?, ?)", (USER_ID, label, json.dumps(items)))
    return get_favorite(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


def touch_favorite(conn: sqlite3.Connection, fav_id: int, when: datetime) -> None:
    conn.execute("UPDATE favorites SET use_count = use_count + 1, last_used = ? WHERE id = ? AND user_id = ?",
                 (when.isoformat(timespec="seconds"), fav_id, USER_ID))


def delete_favorite(conn: sqlite3.Connection, fav_id: int) -> bool:
    return conn.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (fav_id, USER_ID)).rowcount > 0


# --- exercises and templates ---------------------------------------------------

def _exercise(r: dict) -> Exercise:
    secondary = tuple(s for s in (r["secondary_groups"] or "").split(",") if s)
    return Exercise(id=r["id"], name=r["name"], muscle_group=r["muscle_group"], secondary_groups=secondary, tier=r["tier"],
                    increment_kg=r["increment_kg"], rep_min=r["rep_min"], rep_max=r["rep_max"])


def list_exercise_rows(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM exercises ORDER BY tier, name"))


def exercises_by_id(conn: sqlite3.Connection) -> dict[int, Exercise]:
    return {r["id"]: _exercise(r) for r in list_exercise_rows(conn)}


def get_exercise(conn: sqlite3.Connection, exercise_id: int) -> Exercise | None:
    r = _row(conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)))
    return _exercise(r) if r else None


def insert_exercise(conn: sqlite3.Connection, *, name: str, muscle_group: str, secondary_groups: Sequence[str], tier: int,
                    increment_kg: float, rep_min: int, rep_max: int) -> dict:
    cur = conn.execute(
        "INSERT INTO exercises (name, muscle_group, secondary_groups, tier, increment_kg, rep_min, rep_max) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, muscle_group, ",".join(secondary_groups) or None, tier, increment_kg, rep_min, rep_max),
    )
    return _row(conn.execute("SELECT * FROM exercises WHERE id = ?", (cur.lastrowid,)))  # type: ignore[return-value]


def update_exercise(conn: sqlite3.Connection, exercise_id: int, **fields: Any) -> dict | None:
    allowed = {"name", "muscle_group", "secondary_groups", "tier", "increment_kg", "rep_min", "rep_max"}
    sets = {k: (",".join(v) if k == "secondary_groups" and not isinstance(v, str) else v) for k, v in fields.items() if k in allowed}
    if sets:
        conn.execute(f"UPDATE exercises SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), exercise_id))
    return _row(conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)))


def list_templates(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn.execute("SELECT * FROM templates WHERE active = 1 ORDER BY slot, id"))
    for r in rows:
        r["exercises"] = json.loads(r.pop("exercises_json"))
    return rows


def get_template(conn: sqlite3.Connection, name: str) -> dict | None:
    r = _row(conn.execute("SELECT * FROM templates WHERE name = ? COLLATE NOCASE", (name,)))
    if r:
        r["exercises"] = json.loads(r.pop("exercises_json"))
    return r


def upsert_template(conn: sqlite3.Connection, *, name: str, slot: int, exercises: list[dict], active: bool = True) -> dict:
    conn.execute(
        "INSERT INTO templates (name, slot, exercises_json, active) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET slot = excluded.slot, exercises_json = excluded.exercises_json, active = excluded.active",
        (name, slot, json.dumps(exercises), int(active)),
    )
    return get_template(conn, name)  # type: ignore[return-value]


def template_last_performed(conn: sqlite3.Connection) -> dict[str, date]:
    rows = _rows(conn.execute(
        "SELECT template, MAX(performed_on) AS last FROM workouts WHERE user_id = ? AND template IS NOT NULL GROUP BY template",
        (USER_ID,),
    ))
    return {r["template"]: date.fromisoformat(r["last"]) for r in rows}


# --- workouts ------------------------------------------------------------------

def insert_workout(conn: sqlite3.Connection, *, performed_on: date, template: str | None, duration_min: int | None,
                   rpe: int | None, notes: str | None, health_event_id: int | None, sets: Iterable[dict]) -> dict:
    cur = conn.execute(
        "INSERT INTO workouts (user_id, performed_on, template, duration_min, rpe, health_event_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (USER_ID, performed_on.isoformat(), template, duration_min, rpe, health_event_id, notes),
    )
    wid = cur.lastrowid
    for i, s in enumerate(sets):
        conn.execute(
            "INSERT INTO exercise_sets (workout_id, exercise_id, set_index, weight_kg, reps, rir, is_warmup) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wid, s["exercise_id"], s.get("set_index") or (i + 1), s["weight_kg"], s["reps"], s.get("rir"), int(bool(s.get("is_warmup")))),
        )
    return workout_detail(conn, wid)  # type: ignore[arg-type,return-value]


def workout_detail(conn: sqlite3.Connection, workout_id: int) -> dict | None:
    w = _row(conn.execute("SELECT * FROM workouts WHERE id = ? AND user_id = ?", (workout_id, USER_ID)))
    if not w:
        return None
    w["sets"] = _rows(conn.execute(
        "SELECT s.*, x.name AS exercise_name FROM exercise_sets s JOIN exercises x ON x.id = s.exercise_id "
        "WHERE s.workout_id = ? ORDER BY s.id", (workout_id,),
    ))
    return w


def delete_workout(conn: sqlite3.Connection, workout_id: int) -> bool:
    return conn.execute("DELETE FROM workouts WHERE id = ? AND user_id = ?", (workout_id, USER_ID)).rowcount > 0


def list_workout_rows(conn: sqlite3.Connection, *, since: date | None = None, limit: int | None = None) -> list[dict]:
    sql = "SELECT * FROM workouts WHERE user_id = ?"
    params: list[Any] = [USER_ID]
    if since is not None:
        sql += " AND performed_on >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY performed_on DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _rows(conn.execute(sql, params))


def workouts(conn: sqlite3.Connection, *, since: date | None = None, exercise_id: int | None = None) -> list[WorkoutRow]:
    """Engine rows (sets attached), oldest first. Tagged sessions included: the
    engine excludes them itself."""
    rows = list_workout_rows(conn, since=since)
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    sql = f"SELECT * FROM exercise_sets WHERE workout_id IN ({placeholders})"
    params: list[Any] = list(ids)
    if exercise_id is not None:
        sql += " AND exercise_id = ?"
        params.append(exercise_id)
    sets_by_workout: dict[int, list[SetRow]] = {}
    for s in _rows(conn.execute(sql + " ORDER BY id", params)):
        sets_by_workout.setdefault(s["workout_id"], []).append(
            SetRow(exercise_id=s["exercise_id"], set_index=s["set_index"], weight_kg=s["weight_kg"], reps=s["reps"],
                   rir=s["rir"], is_warmup=bool(s["is_warmup"]))
        )
    out = [
        WorkoutRow(id=r["id"], performed_on=date.fromisoformat(r["performed_on"]), sets=tuple(sets_by_workout.get(r["id"], ())),
                   health_event_id=r["health_event_id"], rpe=r["rpe"], template=r["template"])
        for r in rows
    ]
    out.reverse()
    if exercise_id is not None:
        out = [w for w in out if w.sets]
    return out


# --- daily rollup --------------------------------------------------------------

def get_rollup(conn: sqlite3.Connection, day: date) -> dict | None:
    return _row(conn.execute("SELECT * FROM daily_rollup WHERE user_id = ? AND day = ?", (USER_ID, day.isoformat())))


def upsert_rollup(conn: sqlite3.Connection, row: DayRow, *, water_ml: int | None = None, sleep_h: float | None = None,
                  trend_weight_kg: float | None = None, keep_metrics: bool = True) -> dict:
    """Write the computed intake columns; day metrics (steps, water, sleep) are
    kept from the existing row unless given."""
    existing = get_rollup(conn, row.day) or {}
    steps = row.steps if row.steps is not None else (existing.get("steps") if keep_metrics else None)
    water = water_ml if water_ml is not None else (existing.get("water_ml") if keep_metrics else None)
    sleep = sleep_h if sleep_h is not None else (existing.get("sleep_h") if keep_metrics else None)
    trend = trend_weight_kg if trend_weight_kg is not None else existing.get("trend_weight_kg")
    conn.execute(
        """
        INSERT INTO daily_rollup (user_id, day, kcal, protein_g, carbs_g, fat_g, fibre_g, alcohol_g, water_ml, steps, sleep_h,
                                  trend_weight_kg, mean_confidence, logged_complete, health_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, day) DO UPDATE SET kcal = excluded.kcal, protein_g = excluded.protein_g, carbs_g = excluded.carbs_g,
          fat_g = excluded.fat_g, fibre_g = excluded.fibre_g, alcohol_g = excluded.alcohol_g, water_ml = excluded.water_ml,
          steps = excluded.steps, sleep_h = excluded.sleep_h, trend_weight_kg = excluded.trend_weight_kg,
          mean_confidence = excluded.mean_confidence, logged_complete = excluded.logged_complete,
          health_event_id = excluded.health_event_id
        """,
        (USER_ID, row.day.isoformat(), row.kcal, row.protein_g, row.carbs_g, row.fat_g, row.fibre_g, row.alcohol_g, water, steps,
         sleep, trend, row.mean_confidence, int(row.logged_complete), row.health_event_id),
    )
    return get_rollup(conn, row.day)  # type: ignore[return-value]


def set_day_metrics(conn: sqlite3.Connection, day: date, **fields: Any) -> dict:
    allowed = {"steps", "water_ml", "sleep_h", "logged_complete"}
    sets = {k: (int(v) if k == "logged_complete" else v) for k, v in fields.items() if k in allowed and v is not None}
    conn.execute("INSERT OR IGNORE INTO daily_rollup (user_id, day) VALUES (?, ?)", (USER_ID, day.isoformat()))
    if sets:
        conn.execute(f"UPDATE daily_rollup SET {', '.join(f'{k} = ?' for k in sets)} WHERE user_id = ? AND day = ?",
                     (*sets.values(), USER_ID, day.isoformat()))
    return get_rollup(conn, day)  # type: ignore[return-value]


def rollup_days(conn: sqlite3.Connection, start: date, end: date) -> list[DayRow]:
    rows = _rows(conn.execute("SELECT * FROM daily_rollup WHERE user_id = ? AND day BETWEEN ? AND ? ORDER BY day",
                              (USER_ID, start.isoformat(), end.isoformat())))
    return [
        DayRow(day=date.fromisoformat(r["day"]), kcal=r["kcal"], protein_g=r["protein_g"], carbs_g=r["carbs_g"], fat_g=r["fat_g"],
               fibre_g=r["fibre_g"], alcohol_g=r["alcohol_g"], steps=r["steps"], logged_complete=bool(r["logged_complete"]),
               mean_confidence=r["mean_confidence"], health_event_id=r["health_event_id"])
        for r in rows
    ]


def update_trend_weights(conn: sqlite3.Connection, series: Sequence[TrendPoint]) -> None:
    for p in series:
        conn.execute("INSERT OR IGNORE INTO daily_rollup (user_id, day) VALUES (?, ?)", (USER_ID, p.day.isoformat()))
        conn.execute("UPDATE daily_rollup SET trend_weight_kg = ? WHERE user_id = ? AND day = ?",
                     (round(p.trend_kg, 3), USER_ID, p.day.isoformat()))


# --- tdee estimates ------------------------------------------------------------

def insert_tdee_estimate(conn: sqlite3.Connection, *, computed_on: date, window_days: int, tdee_kcal: float, confidence: float,
                         method: str) -> dict:
    cur = conn.execute(
        "INSERT INTO tdee_estimates (user_id, computed_on, window_days, tdee_kcal, confidence, method) VALUES (?, ?, ?, ?, ?, ?)",
        (USER_ID, computed_on.isoformat(), window_days, tdee_kcal, confidence, method),
    )
    return _row(conn.execute("SELECT * FROM tdee_estimates WHERE id = ?", (cur.lastrowid,)))  # type: ignore[return-value]


def latest_tdee_estimate(conn: sqlite3.Connection) -> dict | None:
    return _row(conn.execute("SELECT * FROM tdee_estimates WHERE user_id = ? ORDER BY computed_on DESC, id DESC LIMIT 1", (USER_ID,)))


# --- idempotency ---------------------------------------------------------------

def get_idempotent(conn: sqlite3.Connection, key: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM idempotency_keys WHERE key = ?", (key,)))


def put_idempotent(conn: sqlite3.Connection, *, key: str, route: str, status_code: int, body: str) -> None:
    conn.execute(
        "INSERT INTO idempotency_keys (key, route, status_code, response_json) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET route = excluded.route, status_code = excluded.status_code, "
        "response_json = excluded.response_json, created_at = datetime('now')",
        (key, route, status_code, body),
    )


def prune_idempotent(conn: sqlite3.Connection, *, older_than_hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff,)).rowcount


# --- export --------------------------------------------------------------------

EXPORT_TABLES = ("users", "health_events", "weight_logs", "food_entries", "workouts", "exercise_sets", "exercises", "targets",
                 "daily_rollup", "tdee_estimates", "favorites", "templates", "llm_calls", "review_log", "progress_photos")


def export_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    if table not in EXPORT_TABLES and table != "foods":
        raise ValueError(table)
    sql = "SELECT * FROM foods WHERE source != 'off'" if table == "foods" else f"SELECT * FROM {table}"
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return cols, [tuple(r) for r in cur.fetchall()]


# --- review log ----------------------------------------------------------------

def insert_review(conn: sqlite3.Connection, *, reviewed_on: date, assessment: str, rate_pct_week: float | None, reason: str,
                  proposals: Sequence[str], rails: Sequence[str], target_id: int | None, triggered_by: str) -> dict:
    cur = conn.execute(
        "INSERT INTO review_log (user_id, reviewed_on, assessment, rate_pct_week, reason, proposals_json, rails_json, target_id, triggered_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (USER_ID, reviewed_on.isoformat(), assessment, rate_pct_week, reason, json.dumps(list(proposals)), json.dumps(list(rails)),
         target_id, triggered_by),
    )
    return get_review(conn, cur.lastrowid)  # type: ignore[arg-type,return-value]


def get_review(conn: sqlite3.Connection, review_id: int) -> dict | None:
    r = _row(conn.execute("SELECT * FROM review_log WHERE id = ?", (review_id,)))
    return _review(r) if r else None


def _review(r: dict) -> dict:
    r["proposals"] = json.loads(r.pop("proposals_json") or "[]")
    r["rails"] = json.loads(r.pop("rails_json") or "[]")
    return r


def list_reviews(conn: sqlite3.Connection, limit: int = 12) -> list[dict]:
    rows = _rows(conn.execute("SELECT * FROM review_log WHERE user_id = ? ORDER BY reviewed_on DESC, id DESC LIMIT ?", (USER_ID, limit)))
    return [_review(r) for r in rows]


def latest_review(conn: sqlite3.Connection) -> dict | None:
    rows = list_reviews(conn, limit=1)
    return rows[0] if rows else None


def list_tdee_estimates(conn: sqlite3.Connection, limit: int = 26) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM tdee_estimates WHERE user_id = ? ORDER BY computed_on DESC, id DESC LIMIT ?", (USER_ID, limit)))


# --- app settings --------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = _row(conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)))
    return r["value"] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )


def all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in _rows(conn.execute("SELECT key, value FROM app_settings"))}


# --- event tagging -------------------------------------------------------------

def retag_rows(conn: sqlite3.Connection, event_id: int | None, start: date, end: date, *, only_untagged: bool = True,
               from_event_id: int | None = None) -> dict[str, int]:
    """Tag (or untag) rows logged in [start, end]. Used when an event is started
    after rows were logged, or ended earlier than today."""
    cond = "health_event_id IS NULL" if only_untagged else "health_event_id = ?"
    params_extra: tuple = () if only_untagged else (from_event_id,)
    counts = {}
    for table, col in (("weight_logs", "logged_on"), ("food_entries", "logged_on"), ("workouts", "performed_on"), ("daily_rollup", "day")):
        cur = conn.execute(
            f"UPDATE {table} SET health_event_id = ? WHERE user_id = ? AND {col} BETWEEN ? AND ? AND {cond}",
            (event_id, USER_ID, start.isoformat(), end.isoformat(), *params_extra),
        )
        counts[table] = cur.rowcount
    return counts
