"""All routes under /api/v1 (spec section 10). Read-only endpoints never write;
``/food/entry`` is the only food write path. Every handler runs inside one
transaction so a failed write leaves nothing behind.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import threading
import zipfile
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import db, gemini, llm_services, services, store
from ..config import API_PREFIX, Settings
from .deps import current_target_kcal, get_conn, get_settings_dep, get_today, raise_for
from .schemas import (
    DayMetricsIn,
    EntryIn,
    EventIn,
    EventPatch,
    ExerciseIn,
    ExercisePatch,
    FavoriteIn,
    LogFavoriteIn,
    ProfileIn,
    TargetOverrideIn,
    TemplateIn,
    WeightIn,
    WorkoutIn,
)

router = APIRouter(prefix=API_PREFIX)


# --- today ---------------------------------------------------------------------

@router.get("/today")
def today(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today),
          settings: Settings = Depends(get_settings_dep)) -> dict:
    from ..main import APP_VERSION

    return services.today_payload(conn, today=day, gemini_enabled=settings.gemini_enabled, version=APP_VERSION)


@router.patch("/day/{day}")
def patch_day(day: date, body: DayMetricsIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    with db.transaction(conn):
        store.set_day_metrics(conn, day, **body.model_dump())
        row = services.rollup_day(conn, day)
    return row


# --- profile -------------------------------------------------------------------

@router.get("/profile")
def get_profile(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {"profile": store.get_user(conn)}


@router.put("/profile")
def put_profile(body: ProfileIn, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    try:
        with db.transaction(conn):
            user = services.save_profile(conn, today=day, **body.model_dump())
    except (services.Invalid, services.Rejected, services.NotFound) as exc:
        raise raise_for(exc) from exc
    return {"profile": user, "target": store.current_target_row(conn, day)}


# --- targets -------------------------------------------------------------------

@router.get("/targets")
def get_targets(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    return {"current": store.current_target_row(conn, day), "history": store.list_targets(conn)}


@router.get("/review")
def reviews(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {"latest": store.latest_review(conn), "history": store.list_reviews(conn), "tdee_history": store.list_tdee_estimates(conn)}


@router.post("/review/run", status_code=201)
def run_review(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today),
               settings: Settings = Depends(get_settings_dep)) -> dict:
    """Run the weekly job now (the timer does this Sunday night). Idempotent in
    effect: the change-rate rail rejects a second change inside seven days."""
    with db.transaction(conn):
        return services.weekly_job(conn, today=day, settings=settings)


@router.post("/targets/override", status_code=201)
def override(body: TargetOverrideIn, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    fields = body.model_dump()
    reason = fields.pop("reason")
    try:
        with db.transaction(conn):
            row = services.override_target(conn, today=day, reason=reason, **fields)
    except (services.Invalid, services.Rejected, services.NotFound) as exc:
        raise raise_for(exc) from exc
    return row


# --- weight --------------------------------------------------------------------

@router.post("/weight", status_code=201)
def post_weight(body: WeightIn, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    with db.transaction(conn):
        row = services.log_weight(conn, day=body.day or day, weight_kg=body.weight_kg, waist_cm=body.waist_cm)
    return row


@router.delete("/weight/{day}")
def delete_weight(day: date, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    with db.transaction(conn):
        ok = store.delete_weight(conn, day)
        if ok:
            services.refresh_trend(conn)
    if not ok:
        raise HTTPException(404, "no weigh-in on that day")
    return {"deleted": day.isoformat()}


@router.get("/weight/trend")
def weight_trend(days: int = Query(default=90, ge=7, le=730), conn: sqlite3.Connection = Depends(get_conn),
                 day: date = Depends(get_today)) -> dict:
    return services.weight_trend(conn, days=days, today=day)


# --- food ----------------------------------------------------------------------

@router.get("/food/search")
def food_search(q: str = Query(min_length=1, max_length=80), limit: int = Query(default=20, ge=1, le=50),
                conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {"results": store.search_foods(conn, q, limit)}


@router.post("/food/barcode")
def food_barcode(body: dict, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings_dep)) -> dict:
    code = str(body.get("barcode", "")).strip()
    if not code:
        raise HTTPException(422, "barcode required")
    try:
        with db.transaction(conn):
            return services.lookup_barcode(conn, code, online=settings.off_online_fallback)
    except services.Invalid as exc:
        raise raise_for(exc) from exc


_off_job: threading.Thread | None = None


@router.get("/admin/off")
def off_status(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    from .. import off_import

    return off_import.status(conn) | {"running": bool(_off_job and _off_job.is_alive())}


@router.post("/admin/off/refresh", status_code=202)
def off_refresh(settings: Settings = Depends(get_settings_dep)) -> dict:
    """Start the mirror import in the background (the scheduler does this monthly)."""
    global _off_job
    from .. import off_import

    if _off_job and _off_job.is_alive():
        return {"started": False, "running": True}
    _off_job = threading.Thread(target=off_import.run_import, args=(settings.db_path,),
                                kwargs={"limit": settings.off_import_limit}, daemon=True, name="off-import")
    _off_job.start()
    return {"started": True, "running": True}


@router.get("/food/entries")
def food_entries(day: date | None = None, conn: sqlite3.Connection = Depends(get_conn), today_: date = Depends(get_today)) -> dict:
    d = day or today_
    return {"day": d.isoformat(), "entries": store.entries_for_day(conn, d)}


@router.post("/food/entry", status_code=201)
def food_entry(body: EntryIn, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings_dep),
               today_: date = Depends(get_today)) -> dict:
    on = body.day or (body.logged_at.date() if body.logged_at else today_)
    try:
        with db.transaction(conn):
            row = services.log_entry(
                conn, tz=settings.tz, day=body.day, logged_at=body.logged_at, meal=body.meal, grams=body.grams,
                food_id=body.food_id, food=body.food.model_dump() if body.food else None,
                macros=body.macros.model_dump() if body.macros else None, input_method=body.input_method,
                confidence=body.confidence, photo_path=body.photo_path, target_kcal=current_target_kcal(conn, on),
            )
    except (services.Invalid, services.NotFound) as exc:
        raise raise_for(exc) from exc
    return row


@router.delete("/food/entry/{entry_id}")
def delete_food_entry(entry_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        with db.transaction(conn):
            row = services.remove_entry(conn, entry_id)
    except services.NotFound as exc:
        raise raise_for(exc) from exc
    return {"deleted": entry_id, "day": row["logged_on"]}


@router.get("/food/favorites")
def favorites(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    return {"favorites": store.list_favorites(conn),
            "suggestions": store.favorite_suggestions(conn, since=day - timedelta(days=30))}


@router.post("/food/favorites", status_code=201)
def create_favorite(body: FavoriteIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    items = []
    for it in body.items:
        if (it.food_id is None) == (it.macros is None):
            raise HTTPException(422, "each item needs exactly one of food_id or macros")
        if it.food_id is not None and store.get_food(conn, it.food_id) is None:
            raise HTTPException(404, f"food {it.food_id}")
        items.append({k: v for k, v in it.model_dump().items() if v is not None})
    with db.transaction(conn):
        return store.insert_favorite(conn, label=body.label, items=items)


@router.post("/food/favorites/{fav_id}/log", status_code=201)
def log_favorite(fav_id: int, body: LogFavoriteIn, conn: sqlite3.Connection = Depends(get_conn),
                 settings: Settings = Depends(get_settings_dep), today_: date = Depends(get_today)) -> dict:
    on = body.day or today_
    try:
        with db.transaction(conn):
            rows = services.log_favorite(conn, fav_id=fav_id, tz=settings.tz, day=body.day, meal=body.meal, scale=body.scale,
                                         target_kcal=current_target_kcal(conn, on))
    except (services.Invalid, services.NotFound) as exc:
        raise raise_for(exc) from exc
    return {"entries": rows}


@router.delete("/food/favorites/{fav_id}")
def delete_favorite(fav_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    with db.transaction(conn):
        ok = store.delete_favorite(conn, fav_id)
    if not ok:
        raise HTTPException(404, f"favorite {fav_id}")
    return {"deleted": fav_id}


# --- training ------------------------------------------------------------------

@router.get("/exercises")
def exercises(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {"exercises": store.list_exercise_rows(conn), "templates": store.list_templates(conn)}


@router.post("/exercises", status_code=201)
def create_exercise(body: ExerciseIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    if body.rep_min > body.rep_max:
        raise HTTPException(422, "rep_min must be <= rep_max")
    try:
        with db.transaction(conn):
            return store.insert_exercise(conn, **body.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "an exercise with that name exists") from exc


@router.patch("/exercises/{exercise_id}")
def patch_exercise(exercise_id: int, body: ExercisePatch, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    with db.transaction(conn):
        row = store.update_exercise(conn, exercise_id, **fields)
    if row is None:
        raise HTTPException(404, f"exercise {exercise_id}")
    return row


@router.put("/templates")
def put_template(body: TemplateIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    known = store.exercises_by_id(conn)
    for it in body.exercises:
        if it.exercise_id not in known:
            raise HTTPException(404, f"exercise {it.exercise_id}")
    with db.transaction(conn):
        return store.upsert_template(conn, name=body.name, slot=body.slot, exercises=[it.model_dump() for it in body.exercises],
                                     active=body.active)


@router.get("/workout/next")
def workout_next(template: str | None = None, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    try:
        return services.next_session(conn, today=day, template_name=template)
    except services.NotFound as exc:
        raise raise_for(exc) from exc


@router.post("/workout", status_code=201)
def post_workout(body: WorkoutIn, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    try:
        with db.transaction(conn):
            return services.log_workout(conn, performed_on=body.performed_on or day, template=body.template,
                                        duration_min=body.duration_min, rpe=body.rpe, notes=body.notes,
                                        sets=[s.model_dump() for s in body.sets])
    except (services.Invalid, services.NotFound) as exc:
        raise raise_for(exc) from exc


@router.get("/workout/recent")
def workout_recent(limit: int = Query(default=10, ge=1, le=100), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    rows = store.list_workout_rows(conn, limit=limit)
    return {"workouts": [store.workout_detail(conn, r["id"]) for r in rows]}


@router.delete("/workout/{workout_id}")
def delete_workout(workout_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    with db.transaction(conn):
        ok = store.delete_workout(conn, workout_id)
    if not ok:
        raise HTTPException(404, f"workout {workout_id}")
    return {"deleted": workout_id}


@router.get("/workout/history/{exercise_id}")
def workout_history(exercise_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        return services.exercise_history(conn, exercise_id)
    except services.NotFound as exc:
        raise raise_for(exc) from exc


@router.get("/volume/weekly")
def volume_weekly(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    return services.weekly_volume(conn, today=day)


# --- modes (milestone 8) -------------------------------------------------------

@router.get("/modes/active")
def modes_active(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    return services.active_modes(conn, today=day)


@router.get("/modes")
def modes_history(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    rows = store.list_event_rows(conn)
    for r in rows:
        import json as _json

        r["symptoms"] = _json.loads(r.pop("symptoms_json") or "{}")
    return {"events": rows}


@router.post("/modes", status_code=201)
def modes_start(body: EventIn, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    try:
        with db.transaction(conn):
            return services.start_event(conn, today=day, **body.model_dump())
    except (services.Invalid, services.NotFound) as exc:
        raise raise_for(exc) from exc


@router.patch("/modes/{event_id}")
def modes_patch(event_id: int, body: EventPatch, conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    try:
        with db.transaction(conn):
            return services.update_event(conn, event_id, today=day, **body.model_dump())
    except (services.Invalid, services.NotFound) as exc:
        raise raise_for(exc) from exc


@router.post("/modes/suggest")
def modes_suggest(body: dict, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings_dep),
                  day: date = Depends(get_today)) -> dict:
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 600:
        raise HTTPException(422, "describe the symptoms in 1-600 characters")
    with db.transaction(conn):
        return llm_services.suggest_severity(conn, settings, text, today=day)


# --- gemini paths (read-only: they return candidates, never write entries) -------

MAX_UPLOAD_BYTES = 12 * 1024 * 1024


@router.post("/food/photo")
async def food_photo(file: UploadFile = File(...), conn: sqlite3.Connection = Depends(get_conn),
                     settings: Settings = Depends(get_settings_dep), day: date = Depends(get_today)) -> dict:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "image too large (12 MB max)")
    if not raw:
        raise HTTPException(422, "empty upload")
    with db.transaction(conn):  # the llm_calls audit row
        return llm_services.photo_candidates(conn, settings, raw, today=day)


@router.post("/food/text")
def food_text(body: dict, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings_dep),
              day: date = Depends(get_today)) -> dict:
    text = str(body.get("text", ""))
    if len(text) > 1000:
        raise HTTPException(422, "text too long")
    with db.transaction(conn):
        return llm_services.text_candidates(conn, settings, text, today=day)


@router.get("/photos/{path:path}")
def photo_file(path: str, settings: Settings = Depends(get_settings_dep)) -> FileResponse:
    target = (settings.photo_dir / path).resolve()
    if settings.photo_dir.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "no such photo")
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/ask")
def ask(body: dict, conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings_dep),
        day: date = Depends(get_today)) -> dict:
    question = str(body.get("question", "")).strip()
    if not question or len(question) > 500:
        raise HTTPException(422, "question must be 1-500 characters")
    with db.transaction(conn):
        return llm_services.ask(conn, settings, question, today=day)


@router.get("/admin/llm")
def llm_admin(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today), settings: Settings = Depends(get_settings_dep)) -> dict:
    return {"enabled": settings.gemini_enabled, "model": settings.gemini_model if settings.gemini_enabled else None,
            **gemini.stats(conn, today=day), "recent": gemini.recent_calls(conn)}


# --- summary -------------------------------------------------------------------

@router.get("/summary/weekly")
def summary_weekly(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> dict:
    out = services.weekly_summary(conn, today=day)
    out["narrative"] = llm_services.stored_narrative(conn, day)
    return out


@router.post("/summary/narrative")
def summary_narrative(body: dict | None = None, conn: sqlite3.Connection = Depends(get_conn),
                      settings: Settings = Depends(get_settings_dep), day: date = Depends(get_today)) -> dict:
    force = bool((body or {}).get("force"))
    with db.transaction(conn):
        return llm_services.generate_narrative(conn, settings, today=day, force=force)


# --- export --------------------------------------------------------------------

@router.get("/export")
def export(conn: sqlite3.Connection = Depends(get_conn), day: date = Depends(get_today)) -> StreamingResponse:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in (*store.EXPORT_TABLES, "foods"):
            cols, rows = store.export_rows(conn, table)
            text = io.StringIO()
            w = csv.writer(text)
            w.writerow(cols)
            w.writerows(rows)
            zf.writestr(f"{table}.csv", text.getvalue())
    buf.seek(0)
    name = f"health-export-{day.isoformat()}.zip"
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})
