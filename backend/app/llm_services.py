"""Orchestration for the Gemini jobs (milestones 7-8). Every function degrades:
when the model is disabled, capped or failing, the caller gets a structured
"fallback" answer and the app keeps working on manual entry.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import gemini, services, store
from .config import Settings
from .engine import validation


def _client(settings: Settings, conn: sqlite3.Connection, today: date) -> gemini.Gemini:
    return gemini.Gemini(settings, conn, today=today)


def _fallback(kind: str, exc: Exception) -> dict:
    if isinstance(exc, gemini.GeminiDisabled):
        reason = "Gemini is not configured (no API key); use search or manual entry."
    elif isinstance(exc, gemini.CapReached):
        reason = str(exc)
    else:
        reason = f"Gemini unavailable: {exc}"
    return {"ok": False, "fallback": kind, "error": reason}


# --- photos --------------------------------------------------------------------

def save_food_photo(settings: Settings, jpeg: bytes, *, today: date) -> str:
    """Store the *resized* JPEG under PHOTO_DIR/food/YYYY-MM/<sha>.jpg; return the relative path."""
    digest = hashlib.sha1(jpeg).hexdigest()[:16]
    rel = Path("food") / today.strftime("%Y-%m") / f"{today.isoformat()}-{digest}.jpg"
    path = settings.photo_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(jpeg)
    return rel.as_posix()


def photo_candidates(conn: sqlite3.Connection, settings: Settings, raw_image: bytes, *, today: date) -> dict:
    """POST /food/photo: read-only. Returns candidates; writes no entry."""
    try:
        jpeg = gemini.prepare_image(raw_image)
    except Exception as exc:  # noqa: BLE001 — not an image
        return {"ok": False, "fallback": "manual", "error": f"could not read the image: {exc}", "candidates": [], "photo_path": None}
    photo_path = save_food_photo(settings, jpeg, today=today)
    try:
        candidates, notes, dropped, res = _client(settings, conn, today).identify_food(jpeg)
    except (gemini.GeminiDisabled, gemini.GeminiUnavailable) as exc:
        return _fallback("manual", exc) | {"candidates": [], "photo_path": photo_path}
    return {
        "ok": True, "photo_path": photo_path, "cached": res.cached, "dropped": dropped, "notes": notes,
        "candidates": [_candidate_out(c, "photo") for c in candidates],
    }


def _candidate_out(c: validation.FoodCandidate, method: str) -> dict:
    return asdict(c) | {"input_method": method, "entry_confidence": validation.input_confidence(method, c.confidence)}  # type: ignore[arg-type]


def text_candidates(conn: sqlite3.Connection, settings: Settings, text: str, *, today: date) -> dict:
    """POST /food/text: read-only. Returns parsed items with grams to confirm."""
    if not text.strip():
        return {"ok": False, "fallback": "manual", "error": "empty text", "candidates": []}
    try:
        items, notes, dropped, res = _client(settings, conn, today).parse_meal_text(text)
    except (gemini.GeminiDisabled, gemini.GeminiUnavailable) as exc:
        return _fallback("manual", exc) | {"candidates": []}
    for it in items:
        it["input_method"] = "text"
        it["entry_confidence"] = validation.input_confidence("text")
    return {"ok": True, "cached": res.cached, "dropped": dropped, "notes": notes, "candidates": items}


# --- weekly narrative ------------------------------------------------------------

def narrative_key(week_end: date) -> str:
    return f"narrative.{week_end.isoformat()}"


def stored_narrative(conn: sqlite3.Connection, week_end: date) -> dict | None:
    raw = store.get_setting(conn, narrative_key(week_end))
    return json.loads(raw) if raw else None


def generate_narrative(conn: sqlite3.Connection, settings: Settings, *, today: date, force: bool = False) -> dict:
    existing = stored_narrative(conn, today)
    if existing and not force:
        return existing
    summary = services.weekly_summary(conn, today=today)
    summary.pop("narrative", None)
    try:
        text, res = _client(settings, conn, today).weekly_narrative(summary)
        out = {"text": text, "source": "gemini", "generated_on": today.isoformat(), "cached": res.cached}
    except (gemini.GeminiDisabled, gemini.GeminiUnavailable) as exc:
        out = {"text": template_narrative(summary), "source": "template", "generated_on": today.isoformat(),
               "error": _fallback("template", exc)["error"]}
    store.set_setting(conn, narrative_key(today), json.dumps(out))
    return out


def template_narrative(s: dict) -> str:
    """Deterministic prose from the same numbers, for when the model is unavailable."""
    parts: list[str] = []
    t = s.get("trend") or {}
    if t.get("change_kg") is not None:
        parts.append(f"Your trend weight moved {t['change_kg']:+.2f} kg this week ({t['start_kg']} → {t['end_kg']} kg)"
                     + (f", about {-t['rate_pct_week']:+.2f}% of body weight per week." if t.get("rate_pct_week") is not None else "."))
    else:
        parts.append("Not enough weigh-ins this week to read the trend.")
    a = s.get("adherence")
    if a and a.get("days_considered"):
        parts.append(f"You logged {s.get('days_logged', 0)} of 7 days, {a['days_considered']} of them completely; "
                     f"calories landed within ±10% of target on {a['kcal_hit_pct']:.0f}% of those days and protein hit target on "
                     f"{a['protein_hit_pct'] if a['protein_hit_pct'] is not None else 0:.0f}%.")
    else:
        parts.append(f"You logged {s.get('days_logged', 0)} of 7 days; none were complete enough to score adherence.")
    if s.get("estimates_flagged"):
        parts.append("Intake figures this week are estimates (low logging confidence), so the maintenance estimate is down-weighted.")
    td = s.get("tdee")
    if td:
        parts.append(f"Maintenance is estimated at {td['tdee_kcal']:.0f} kcal ({td['method']}"
                     + (f", confidence {td['confidence']*100:.0f}%" if td["method"] == "adaptive" else "") + ").")
    if s.get("days_excluded"):
        parts.append(f"{s['days_excluded']} day(s) fell inside a health event and were excluded from every calculation; any weight rebound after illness is rehydration, not fat.")
    changes = s.get("target_changes") or []
    if changes:
        c = changes[0]
        parts.append(f"Targets changed on {c['effective_from']}: {c['kcal']} kcal, {c['protein_g']} g protein, {c['steps']} steps — {c['reason']}")
    else:
        parts.append("Targets did not change this week.")
    flags = [g for g in (s.get("volume") or {}).get("groups", []) if g["flag"] != "ok"]
    if flags:
        parts.append("Training volume flags: " + ", ".join(f"{g['muscle_group']} {g['flag']} ({g['sets']} sets)" for g in flags) + ".")
    return " ".join(parts)


# --- ask -----------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MAX_RANGE_DAYS = 92


def parse_range(question: str, today: date) -> tuple[date, date, str]:
    """Scope the rows Gemini sees by the date range in the question (spec 10).
    Default: the last 7 days. Never the whole database."""
    q = question.lower()
    if "today" in q:
        return today, today, "today"
    if "yesterday" in q:
        d = today - timedelta(days=1)
        return d, d, "yesterday"
    m = re.search(r"(?:last|past)\s+(\d{1,3})\s+days?", q)
    if m:
        n = min(int(m.group(1)), MAX_RANGE_DAYS)
        return today - timedelta(days=n - 1), today, f"last {n} days"
    m = re.search(r"(?:last|past)\s+(\d{1,2})\s+weeks?", q)
    if m:
        n = min(int(m.group(1)) * 7, MAX_RANGE_DAYS)
        return today - timedelta(days=n - 1), today, f"last {n // 7} weeks"
    if "this week" in q:
        start = today - timedelta(days=today.weekday())
        return start, today, "this week"
    if "last week" in q:
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), "last week"
    if "this month" in q:
        return today.replace(day=1), today, "this month"
    if "last month" in q:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, "last month"
    for name, idx in MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            year = today.year if idx <= today.month else today.year - 1
            last = calendar.monthrange(year, idx)[1]
            end = date(year, idx, last)
            return date(year, idx, 1), min(end, today), f"{name.title()} {year}"
    return today - timedelta(days=6), today, "last 7 days"


def scoped_rows(conn: sqlite3.Connection, start: date, end: date) -> dict[str, Any]:
    days = [asdict(d) for d in store.rollup_days(conn, start, end)]
    weights = [w for w in store.list_weight_rows(conn, since=start) if w["logged_on"] <= end.isoformat()]
    workouts = [
        {"performed_on": w.performed_on.isoformat(), "template": w.template, "sets": len(w.sets), "rpe": w.rpe,
         "excluded": w.health_event_id is not None,
         "top_sets": sorted({(s.exercise_id, s.weight_kg) for s in w.sets if not s.is_warmup}, key=lambda x: -x[1])[:6]}
        for w in store.workouts(conn, since=start) if w.performed_on <= end
    ]
    targets = [t for t in store.list_targets(conn) if t["effective_from"] <= end.isoformat()][:3]
    events = [asdict(e) for e in store.list_events(conn) if (e.ended_at or end) >= start and e.started_at <= end]
    entries_by_day = {}
    cur = start
    while cur <= end and (end - start).days <= 31:
        rows = store.entries_for_day(conn, cur)
        if rows:
            entries_by_day[cur.isoformat()] = [{"meal": r["meal"], "food": r["food_name"], "grams": r["grams"], "kcal": r["kcal"], "protein_g": r["protein_g"]} for r in rows]
        cur += timedelta(days=1)
    return {"range": {"start": start.isoformat(), "end": end.isoformat()}, "daily_rollup": days, "weights": weights,
            "workouts": workouts, "targets": targets, "health_events": events, "food_entries": entries_by_day}


def ask(conn: sqlite3.Connection, settings: Settings, question: str, *, today: date) -> dict:
    start, end, label = parse_range(question, today)
    rows = scoped_rows(conn, start, end)
    try:
        answer, res = _client(settings, conn, today).answer_query(question, rows)
    except (gemini.GeminiDisabled, gemini.GeminiUnavailable) as exc:
        return _fallback("unavailable", exc) | {"answer": "Unavailable", "range": rows["range"], "range_label": label}
    return {"ok": True, "answer": answer, "range": rows["range"], "range_label": label, "cached": res.cached}


# --- symptoms ------------------------------------------------------------------

def suggest_severity(conn: sqlite3.Connection, settings: Settings, text: str, *, today: date) -> dict:
    """Never applied automatically: the user picks."""
    try:
        out, res = _client(settings, conn, today).suggest_symptom_level(text)
    except (gemini.GeminiDisabled, gemini.GeminiUnavailable) as exc:
        return _fallback("manual", exc) | {"suggestion": None}
    return {"ok": True, "suggestion": out, "cached": res.cached}
