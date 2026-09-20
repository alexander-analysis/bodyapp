"""Orchestration: one function per user action, composing ``engine`` (pure) and
``store`` (rows). Every number that reaches the user is produced here by an
engine function or read from a row — never invented in a route.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import store
from .engine import guards, modes, progression, rollup, targets, tdee, trend, validation, volume
from .engine.trend import TrendPoint
from .engine.types import Target


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Rejected(Exception):
    """A rail rejected the write; ``reason`` names it."""

    def __init__(self, reason: str, tripped: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.tripped = tripped


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def today_local(tz: str) -> date:
    return now_local(tz).date()


# --- profile and targets -------------------------------------------------------

def save_profile(conn: sqlite3.Connection, *, name: str, sex: str, birth_date: date, height_cm: float,
                 goal_weight_kg: float | None, timezone: str, start_weight_kg: float | None, phase: str, today: date) -> dict:
    """Create or update the single user. On first save, write the initial target
    (formula-based, rail-checked) so the Today screen has numbers immediately."""
    user = store.upsert_user(conn, name=name, sex=sex, birth_date=birth_date, height_cm=height_cm,
                             goal_weight_kg=goal_weight_kg, timezone=timezone)
    weight = start_weight_kg
    latest = store.latest_weight(conn)
    if weight is None and latest:
        weight = latest["weight_kg"]
    if start_weight_kg is not None and latest is None:
        log_weight(conn, day=today, weight_kg=start_weight_kg, waist_cm=None, source="manual")
    if store.current_target(conn, today) is None and weight is not None:
        profile = store.user_profile(conn)
        assert profile is not None
        proposed = targets.initial_target(profile, weight, phase, today)
        _write_target(conn, proposed, today=today)
    return user


def _write_target(conn: sqlite3.Connection, proposed: Target, *, today: date) -> dict:
    events = store.list_events(conn)
    previous = store.current_target(conn, today)
    maint = current_maintenance_kcal(conn, today)
    res = guards.apply_rails(proposed, guards.RailContext(
        today=today, events=events, maintenance_kcal=maint, previous=previous, last_change_on=store.last_change_on(conn),
    ))
    if res.rejected:
        raise Rejected(res.reason, res.tripped)
    assert res.target is not None
    row = store.insert_target(conn, res.target)
    row["rails_tripped"] = list(res.tripped)
    return row


def override_target(conn: sqlite3.Connection, *, today: date, reason: str, **changes: Any) -> dict:
    current = store.current_target(conn, today)
    if current is None:
        raise Invalid("no target yet: save a profile with a start weight first")
    fields = {k: v for k, v in changes.items() if v is not None}
    if not fields:
        raise Invalid("nothing to change")
    proposed = Target(**{**asdict(current), **fields, "effective_from": today, "set_by": "user",
                         "reason": f"user override: {reason.strip() or 'no reason given'}"})
    return _write_target(conn, proposed, today=today)


def current_maintenance_kcal(conn: sqlite3.Connection, today: date) -> int | None:
    est = tdee_estimate(conn, today)
    return int(round(est.tdee_kcal)) if est and est.tdee_kcal else None


# --- weight --------------------------------------------------------------------

def log_weight(conn: sqlite3.Connection, *, day: date, weight_kg: float, waist_cm: float | None, source: str = "manual") -> dict:
    row = store.upsert_weight(conn, day=day, weight_kg=weight_kg, waist_cm=waist_cm, source=source,
                              health_event_id=store.active_event_id(conn, day))
    refresh_trend(conn)
    return row


def refresh_trend(conn: sqlite3.Connection) -> list[trend.TrendPoint]:
    series = trend.trend_weight(store.weight_points(conn))
    store.update_trend_weights(conn, series)
    return series


def weight_trend(conn: sqlite3.Connection, *, days: int, today: date) -> dict:
    series = trend.trend_weight(store.weight_points(conn))
    start = today - timedelta(days=days - 1)
    points = [p for p in series if p.day >= start]
    events = store.list_events(conn)
    bands = [
        {"event_id": e.id, "type": e.type, "severity": e.severity, "start": e.started_at.isoformat(),
         "end": (e.ended_at or today).isoformat(), "ramp_until": e.ramp_until.isoformat() if e.ramp_until else None}
        for e in events if (e.ended_at or today) >= start
    ]
    clean = [p for p in points if not p.excluded]
    rate = None
    if len(clean) >= 2:
        first = trend.trend_on_or_after(series, today - timedelta(days=13))
        last = trend.trend_on_or_before(series, today)
        if first and last:
            rate = trend.rate_pct_per_week(first, last)
    latest = store.latest_weight(conn)
    return {
        "days": days,
        "points": [{"day": p.day.isoformat(), "raw_kg": p.raw_kg, "trend_kg": round(p.trend_kg, 2), "excluded": p.excluded} for p in points],
        "bands": bands,
        "rate_pct_week": round(rate, 2) if rate is not None else None,
        "latest": latest,
        "trend_kg": round(clean[-1].trend_kg, 2) if clean else None,
    }


# --- food ----------------------------------------------------------------------

def log_entry(conn: sqlite3.Connection, *, tz: str, day: date | None, logged_at: datetime | None, meal: str | None,
              grams: float, food_id: int | None, food: dict | None, macros: dict | None, input_method: str,
              confidence: float | None, photo_path: str | None, target_kcal: int | None) -> dict:
    """Commit a confirmed entry. Exactly one of food_id / food / macros supplies the
    nutrition; per-100g sources are scaled by the confirmed grams."""
    sources = [x is not None for x in (food_id, food, macros)]
    if sum(sources) != 1:
        raise Invalid("give exactly one of food_id, food, macros")
    if grams <= 0 or grams > 5000:
        raise Invalid("grams must be between 0 and 5000")

    when = logged_at or now_local(tz)
    on = day or when.date()
    fid = food_id
    fibre = alcohol = 0.0

    if food_id is not None:
        row = store.get_food(conn, food_id)
        if row is None:
            raise NotFound(f"food {food_id}")
        cand = _candidate_from_food(row)
        totals = validation.scale_entry(cand, grams)
        fibre = round((row["fibre_100g"] or 0.0) * grams / 100.0, 1)
    elif food is not None:
        cand = validation.FoodCandidate(
            name=food["name"], confidence=1.0, kcal_100g=food["kcal_100g"], protein_100g=food["protein_100g"],
            carbs_100g=food["carbs_100g"], fat_100g=food["fat_100g"],
        )
        errors = validation.validate_candidate(cand)
        if errors:
            raise Invalid("; ".join(errors))
        totals = validation.scale_entry(cand, grams)
        fibre = round(float(food.get("fibre_100g") or 0.0) * grams / 100.0, 1)
        if food.get("save", True):
            existing = store.get_food_by_barcode(conn, food["barcode"]) if food.get("barcode") else None
            if existing:
                fid = existing["id"]
            else:
                saved = store.insert_food(conn, name=food["name"], brand=food.get("brand"), kcal_100g=food["kcal_100g"],
                                          protein_100g=food["protein_100g"], carbs_100g=food["carbs_100g"], fat_100g=food["fat_100g"],
                                          fibre_100g=float(food.get("fibre_100g") or 0.0), source="user", verified=True,
                                          barcode=food.get("barcode"))
                fid = saved["id"]
    else:
        assert macros is not None
        totals = {"grams": grams, "kcal": float(macros["kcal"]), "protein_g": float(macros["protein_g"]),
                  "carbs_g": float(macros["carbs_g"]), "fat_g": float(macros["fat_g"])}
        if totals["kcal"] < 0 or totals["kcal"] > 6000:
            raise Invalid("kcal out of range")
        fibre = float(macros.get("fibre_g") or 0.0)
        alcohol = float(macros.get("alcohol_g") or 0.0)

    conf = confidence if confidence is not None else validation.input_confidence(input_method)  # type: ignore[arg-type]
    row = store.insert_entry(
        conn, food_id=fid, logged_at=when, logged_on=on, meal=meal, grams=grams, kcal=totals["kcal"],
        protein_g=totals["protein_g"], carbs_g=totals["carbs_g"], fat_g=totals["fat_g"], fibre_g=fibre, alcohol_g=alcohol,
        input_method=input_method, confidence=round(conf, 3), photo_path=photo_path,
        health_event_id=store.active_event_id(conn, on),
    )
    rollup_day(conn, on, target_kcal=target_kcal)
    return row


def _candidate_from_food(row: dict) -> validation.FoodCandidate:
    return validation.FoodCandidate(
        name=row["name"], confidence=1.0, kcal_100g=row["kcal_100g"], protein_100g=row["protein_100g"],
        carbs_100g=row["carbs_100g"], fat_100g=row["fat_100g"],
    )


def remove_entry(conn: sqlite3.Connection, entry_id: int) -> dict:
    row = store.delete_entry(conn, entry_id)
    if row is None:
        raise NotFound(f"entry {entry_id}")
    rollup_day(conn, date.fromisoformat(row["logged_on"]))
    return row


def log_favorite(conn: sqlite3.Connection, *, fav_id: int, tz: str, day: date | None, meal: str | None,
                 scale: float, target_kcal: int | None) -> list[dict]:
    """Log every item of a favorite (scaled), as ``input_method='favorite'``."""
    fav = store.get_favorite(conn, fav_id)
    if fav is None:
        raise NotFound(f"favorite {fav_id}")
    out = []
    for item in fav["items"]:
        grams = float(item["grams"]) * scale
        if item.get("food_id"):
            out.append(log_entry(conn, tz=tz, day=day, logged_at=None, meal=meal, grams=grams, food_id=item["food_id"],
                                 food=None, macros=None, input_method="favorite", confidence=None, photo_path=None,
                                 target_kcal=target_kcal))
        else:
            m = item["macros"]
            macros = {k: float(m[k]) * scale for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
            macros["fibre_g"] = float(m.get("fibre_g") or 0.0) * scale
            out.append(log_entry(conn, tz=tz, day=day, logged_at=None, meal=meal, grams=grams, food_id=None, food=None,
                                 macros=macros, input_method="favorite", confidence=None, photo_path=None,
                                 target_kcal=target_kcal))
    store.touch_favorite(conn, fav_id, now_local(tz))
    return out


# --- rollup --------------------------------------------------------------------

def rollup_day(conn: sqlite3.Connection, day: date, *, target_kcal: int | None = None) -> dict:
    if target_kcal is None:
        t = store.current_target(conn, day)
        target_kcal = t.kcal if t else None
    existing = store.get_rollup(conn, day) or {}
    entries = store.entry_rows_for_day(conn, day)
    user_marked = None
    if existing.get("logged_complete_override") is not None:  # reserved for a future column
        user_marked = bool(existing["logged_complete_override"])
    row = rollup.rollup_day(
        day, entries, target_kcal=target_kcal, steps=existing.get("steps"),
        health_event_id=store.active_event_id(conn, day), user_marked_complete=user_marked,
    )
    return store.upsert_rollup(conn, row)


def nightly_rollup(conn: sqlite3.Connection, *, today: date) -> list[dict]:
    """Roll up yesterday and today (safety net for days with no writes)."""
    out = [rollup_day(conn, d) for d in (today - timedelta(days=1), today)]
    refresh_trend(conn)
    return out


# --- tdee ----------------------------------------------------------------------

def tdee_estimate(conn: sqlite3.Connection, today: date) -> tdee.TdeeEstimate | None:
    profile = store.user_profile(conn)
    if profile is None:
        return None
    series = trend.trend_weight(store.weight_points(conn))
    days = store.rollup_days(conn, today - timedelta(days=tdee.WINDOW_DAYS - 1), today)
    return tdee.estimate_tdee(profile=profile, days=days, trend=series, as_of=today, events=store.list_events(conn))


# --- training ------------------------------------------------------------------

def log_workout(conn: sqlite3.Connection, *, performed_on: date, template: str | None, duration_min: int | None,
                rpe: int | None, notes: str | None, sets: list[dict]) -> dict:
    if not sets:
        raise Invalid("a workout needs at least one set")
    known = store.exercises_by_id(conn)
    for s in sets:
        if s["exercise_id"] not in known:
            raise NotFound(f"exercise {s['exercise_id']}")
    return store.insert_workout(conn, performed_on=performed_on, template=template, duration_min=duration_min, rpe=rpe,
                                notes=notes, health_event_id=store.active_event_id(conn, performed_on), sets=sets)


def next_session(conn: sqlite3.Connection, *, today: date, template_name: str | None) -> dict:
    events = store.list_events(conn)
    state = modes.resolve(events, today)
    templates = store.list_templates(conn)
    if not templates:
        return {"template": None, "exercises": [], "blocked": False, "mode": _mode_payload(state), "note": "no templates defined"}
    if template_name:
        tmpl = store.get_template(conn, template_name)
        if tmpl is None:
            raise NotFound(f"template {template_name}")
    else:
        last = store.template_last_performed(conn)
        tmpl = min(templates, key=lambda t: (last.get(t["name"], date.min), t["slot"]))

    blocked = state.rules.training == "blocked"
    exercises = store.exercises_by_id(conn)
    history = store.workouts(conn, since=today - timedelta(days=120))
    items = []
    for item in tmpl["exercises"]:
        ex = exercises.get(item["exercise_id"])
        if ex is None:
            continue
        affected = modes.exercise_is_affected(ex, state.affected_groups)
        p = progression.prescribe_next(
            ex, [w for w in history if w.sets_for(ex.id)],
            allow_progression=state.rules.progression_allowed and not affected,
            load_cap=state.rules.load_cap_last_session,
        )
        sets = max(1, round(item.get("sets", p.sets) * state.rules.volume_cap)) if not blocked else 0
        items.append({
            "exercise": {"id": ex.id, "name": ex.name, "muscle_group": ex.muscle_group, "rep_min": ex.rep_min,
                         "rep_max": ex.rep_max, "increment_kg": ex.increment_kg},
            "weight_kg": p.weight_kg, "target_reps": p.target_reps, "sets": sets,
            "stalled": p.stalled, "stall_sessions": p.stall_sessions, "proposal": p.proposal, "note": p.note,
            "omitted": affected, "omit_reason": "injury: affected muscle group" if affected else None,
        })
    return {
        "template": tmpl["name"], "exercises": items, "blocked": blocked,
        "note": "training blocked by the active mode; rest" if blocked else None,
        "mode": _mode_payload(state),
    }


def exercise_history(conn: sqlite3.Connection, exercise_id: int) -> dict:
    ex = store.get_exercise(conn, exercise_id)
    if ex is None:
        raise NotFound(f"exercise {exercise_id}")
    ws = store.workouts(conn, exercise_id=exercise_id)
    sessions = progression.summarize(exercise_id, ws)
    return {
        "exercise": asdict(ex),
        "sessions": [
            {"workout_id": s.workout_id, "performed_on": s.performed_on.isoformat(), "top_weight_kg": s.top_weight_kg,  # type: ignore[union-attr]
             "min_reps": s.min_reps, "max_reps": s.max_reps, "mean_rir": s.mean_rir, "e1rm": round(s.e1rm, 1) if s.e1rm else None,
             "working_sets": s.working_sets}
            for s in sessions
        ],
        "e1rm_trend": [{"day": d.isoformat(), "e1rm": v} for d, v in progression.e1rm_trend(exercise_id, ws)],  # type: ignore[union-attr]
        "excluded_sessions": sum(1 for w in ws if w.health_event_id is not None),
    }


def weekly_volume(conn: sqlite3.Connection, *, today: date) -> dict:
    state = modes.resolve(store.list_events(conn), today)
    ws = store.workouts(conn, since=today - timedelta(days=volume.WINDOW_DAYS - 1))
    vol = volume.weekly_volume(ws, store.exercises_by_id(conn), today)
    flags = volume.volume_flags(vol, cap=state.rules.volume_cap or 1.0)
    return {
        "as_of": today.isoformat(), "window_days": volume.WINDOW_DAYS,
        "band": {"low": volume.LOW_SETS, "high": volume.HIGH_SETS, "cap": state.rules.volume_cap},
        "groups": [{"muscle_group": g, "sets": s, "flag": flags[g]} for g, s in vol.items()],
    }


# --- today ---------------------------------------------------------------------

def _mode_payload(state: modes.ModeState) -> dict:
    r = state.rules
    return {
        "names": list(state.names), "training": r.training, "force_maintenance": r.force_maintenance,
        "kcal_multiplier": r.kcal_multiplier, "volume_cap": r.volume_cap, "progression_allowed": r.progression_allowed,
        "logging_strict": r.logging_strict, "guidance": list(r.guidance), "fever_lock": state.training_locked_by_fever,
        "referral_due": [e.id for e in state.referrals], "affected_groups": list(state.affected_groups),
    }


def today_payload(conn: sqlite3.Connection, *, today: date, gemini_enabled: bool, version: str) -> dict:
    user = store.get_user(conn)
    target_row = store.current_target_row(conn, today)
    entries = store.entries_for_day(conn, today)
    consumed = {
        "kcal": round(sum(e["kcal"] for e in entries), 1),
        "protein_g": round(sum(e["protein_g"] for e in entries), 1),
        "carbs_g": round(sum(e["carbs_g"] for e in entries), 1),
        "fat_g": round(sum(e["fat_g"] for e in entries), 1),
        "fibre_g": round(sum(e["fibre_g"] or 0 for e in entries), 1),
        "alcohol_g": round(sum(e["alcohol_g"] or 0 for e in entries), 1),
    }
    remaining = None
    if target_row:
        remaining = {
            "kcal": round(target_row["kcal"] - consumed["kcal"]),
            "protein_g": round(target_row["protein_g"] - consumed["protein_g"]),
            "fat_g": round(target_row["fat_g_min"] - consumed["fat_g"]),
            "fibre_g": round(target_row["fibre_g"] - consumed["fibre_g"]),
        }
    metrics = store.get_rollup(conn, today) or {}
    est = tdee_estimate(conn, today) if user else None
    state = modes.resolve(store.list_events(conn), today)
    series = trend.trend_weight(store.weight_points(conn)) if user else []
    clean = [p for p in series if not p.excluded]
    latest = store.latest_weight(conn)
    return {
        "day": today.isoformat(),
        "profile": user,
        "target": target_row,
        "consumed": consumed,
        "remaining": remaining,
        "entries": entries,
        "day_metrics": {"steps": metrics.get("steps"), "water_ml": metrics.get("water_ml"), "sleep_h": metrics.get("sleep_h"),
                        "logged_complete": bool(metrics.get("logged_complete"))},
        "weight": {"latest": latest, "trend_kg": round(clean[-1].trend_kg, 2) if clean else None} if latest else None,
        "tdee": {"tdee_kcal": est.tdee_kcal, "method": est.method, "confidence": est.confidence, "adaptive_kcal": est.adaptive_kcal,
                 "formula_kcal": est.formula_kcal, "complete_days": est.complete_days, "notes": list(est.notes)} if est else None,
        "mode": _mode_payload(state),
        "next_session": next_session(conn, today=today, template_name=None) if user else None,
        "gemini_enabled": gemini_enabled,
        "scope": guards.SCOPE_STATEMENT,
        "version": version,
    }


# --- weekly summary ------------------------------------------------------------

def weekly_summary(conn: sqlite3.Connection, *, today: date) -> dict:
    """Structured stats for the last 7 days. The prose narrative is a milestone-8
    Gemini job; it renders these numbers, it never computes them."""
    start = today - timedelta(days=6)
    target = store.current_target(conn, today)
    days = store.rollup_days(conn, start, today)
    adherence = rollup.weekly_adherence(days, target) if target else None
    series = trend.trend_weight(store.weight_points(conn))
    first = trend.trend_on_or_after(series, start)
    last = trend.trend_on_or_before(series, today)
    change = round(last.trend_kg - first.trend_kg, 2) if first and last and first.day < last.day else None
    est = tdee_estimate(conn, today)
    vol = weekly_volume(conn, today=today)
    events = [e for e in store.list_events(conn) if (e.ended_at or today) >= start]
    targets_this_week = [t for t in store.list_targets(conn) if date.fromisoformat(t["effective_from"]) >= start]
    return {
        "week": {"start": start.isoformat(), "end": today.isoformat()},
        "target": asdict(target) | {"effective_from": target.effective_from.isoformat()} if target else None,
        "adherence": asdict(adherence) if adherence else None,
        "days_logged": sum(1 for d in days if not d.is_unlogged),
        "days_excluded": sum(1 for d in days if d.health_event_id is not None),
        "trend": {"start_kg": round(first.trend_kg, 2) if first else None, "end_kg": round(last.trend_kg, 2) if last else None,
                  "change_kg": change,
                  "rate_pct_week": round(trend.rate_pct_per_week(first, last), 2) if first and last and first.day < last.day else None},
        "tdee": {"tdee_kcal": est.tdee_kcal, "method": est.method, "confidence": est.confidence, "notes": list(est.notes)} if est else None,
        "volume": vol,
        "events": [{"id": e.id, "type": e.type, "severity": e.severity, "started_at": e.started_at.isoformat(),
                    "ended_at": e.ended_at.isoformat() if e.ended_at else None} for e in events],
        "target_changes": targets_this_week,
        "estimates_flagged": bool(adherence and adherence.mean_confidence is not None and adherence.mean_confidence < 0.6),
        "narrative": None,
    }


# --- weekly jobs (milestones 4 and 5) ------------------------------------------

def record_tdee_estimate(conn: sqlite3.Connection, *, today: date) -> dict | None:
    """Weekly recompute (spec 5.2): persist the current estimate to ``tdee_estimates``."""
    est = tdee_estimate(conn, today)
    if est is None or not est.tdee_kcal:
        return None
    return store.insert_tdee_estimate(conn, computed_on=today, window_days=est.window_days, tdee_kcal=est.tdee_kcal,
                                      confidence=est.confidence, method=est.method)


def run_weekly_review(conn: sqlite3.Connection, *, today: date, triggered_by: str = "job") -> dict:
    """Sunday-night target review (spec 5.3). At most one change, always through the
    rails, and every outcome — including "no change" — is logged with its reason."""
    profile = store.user_profile(conn)
    current = store.current_target(conn, today)
    if profile is None or current is None:
        row = store.insert_review(conn, reviewed_on=today, assessment="no_target", rate_pct_week=None,
                                  reason="no profile or target yet", proposals=(), rails=(), target_id=None, triggered_by=triggered_by)
        return row
    series = refresh_trend(conn)
    ctx = targets.ReviewContext(
        today=today, profile=profile, current=current, trend=series, events=store.list_events(conn),
        days=store.rollup_days(conn, today - timedelta(days=targets.REVIEW_WINDOW_DAYS - 1), today),
        last_change_on=store.last_change_on(conn), previous_stall_action=store.last_stall_action(conn),  # type: ignore[arg-type]
        maintenance_kcal=current_maintenance_kcal(conn, today),
    )
    result = targets.weekly_review(ctx)
    target_row = store.insert_target(conn, result.proposed) if result.proposed is not None else None
    row = store.insert_review(
        conn, reviewed_on=today, assessment=result.assessment, rate_pct_week=result.rate_pct_week, reason=result.reason,
        proposals=result.proposals, rails=result.rails_tripped, target_id=target_row["id"] if target_row else None,
        triggered_by=triggered_by,
    )
    row["target"] = target_row
    return row


def weekly_job(conn: sqlite3.Connection, *, today: date) -> dict:
    """Sunday night, after the rollup: rollup -> trend -> TDEE estimate -> review."""
    nightly_rollup(conn, today=today)
    est = record_tdee_estimate(conn, today=today)
    review = run_weekly_review(conn, today=today, triggered_by="job")
    return {"tdee_estimate": est, "review": review}


# --- barcode (milestone 6) -----------------------------------------------------

OFF_API = "https://world.openfoodfacts.org/api/v2/product/{code}?fields=code,product_name,brands,nutriments"


def lookup_barcode(conn: sqlite3.Connection, code: str, *, online: bool = True, timeout: float = 4.0) -> dict:
    """Local mirror first (offline, free). If the product is missing and we are
    online, ask the OFF API once and cache the answer as an ``off`` food."""
    code = "".join(ch for ch in code if ch.isdigit())
    if not code:
        raise Invalid("barcode must be digits")
    food = store.get_food_by_barcode(conn, code)
    if food:
        return {"barcode": code, "food": food, "source": "local"}
    if not online:
        return {"barcode": code, "food": None, "source": None}
    fetched = fetch_off_product(code, timeout=timeout)
    if fetched is None:
        return {"barcode": code, "food": None, "source": None}
    food = store.insert_food(conn, source="off", verified=False, **fetched)
    return {"barcode": code, "food": food, "source": "off_api"}


def fetch_off_product(code: str, *, timeout: float = 4.0) -> dict | None:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(OFF_API.format(code=code), headers={"User-Agent": "health-platform-pi/0.1 (single user)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    product = data.get("product") or {}
    if data.get("status") != 1 or not product:
        return None
    n = product.get("nutriments") or {}

    def num(*keys: str) -> float | None:
        for k in keys:
            v = n.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    kcal = num("energy-kcal_100g")
    kj = num("energy-kj_100g", "energy_100g")
    if kcal is None and kj is not None:
        kcal = kj / 4.184
    p, c, f = num("proteins_100g"), num("carbohydrates_100g"), num("fat_100g")
    name = (product.get("product_name") or "").strip()
    if kcal is None or p is None or c is None or f is None or not name:
        return None
    if not (0 <= kcal <= 900 and 0 <= p <= 100 and 0 <= c <= 100 and 0 <= f <= 100) or not validation.macros_consistent(kcal, p, c, f):
        return None
    return {
        "barcode": code, "name": name[:120], "brand": ((product.get("brands") or "").strip() or None),
        "kcal_100g": round(kcal, 1), "protein_100g": round(p, 1), "carbs_100g": round(c, 1), "fat_100g": round(f, 1),
        "fibre_100g": round(max(0.0, min(num("fiber_100g") or 0.0, 100.0)), 1),
    }
