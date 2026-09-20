"""Progress photos + physique analysis (an addition beyond the spec).

A daily photo is resized and stored on the data volume, sent to Gemini with
the reference-physique description and the previous assessment (for a
consistent scale), and the structured result is stored for charts.

These numbers are the model's *estimates* for display only. They never touch
targets, rollups or guardrails.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from . import gemini, store
from .config import Settings

SETTING_REF_NAME = "physique.reference_name"
SETTING_REF_DESC = "physique.reference_description"
DEFAULT_REF_NAME = "Richardson"
DEFAULT_REF_DESC = (
    "Lean, athletic and clearly defined: visible abdominal separation, capped shoulders, a wide back tapering to a "
    "narrow waist, full chest and arms without bulk. Body fat roughly 10-12%. (Edit this in Settings to describe the "
    "physique you actually mean.)"
)


def reference(conn: sqlite3.Connection) -> dict:
    return {
        "name": store.get_setting(conn, SETTING_REF_NAME, DEFAULT_REF_NAME) or DEFAULT_REF_NAME,
        "description": store.get_setting(conn, SETTING_REF_DESC, DEFAULT_REF_DESC) or DEFAULT_REF_DESC,
        "is_default": store.get_setting(conn, SETTING_REF_DESC) is None,
    }


def set_reference(conn: sqlite3.Connection, *, name: str, description: str) -> dict:
    store.set_setting(conn, SETTING_REF_NAME, name.strip()[:80])
    store.set_setting(conn, SETTING_REF_DESC, description.strip()[:2000])
    return reference(conn)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["analysis"] = json.loads(d.pop("analysis_json") or "null")
    return d


def list_photos(conn: sqlite3.Connection, limit: int = 120) -> list[dict]:
    rows = conn.execute("SELECT * FROM progress_photos WHERE user_id = ? ORDER BY taken_on DESC, id DESC LIMIT ?",
                        (store.USER_ID, limit)).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_photo(conn: sqlite3.Connection, photo_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM progress_photos WHERE id = ? AND user_id = ?", (photo_id, store.USER_ID)).fetchone())


def previous_analysis(conn: sqlite3.Connection, before: date) -> dict | None:
    r = conn.execute(
        "SELECT taken_on, analysis_json FROM progress_photos WHERE user_id = ? AND taken_on < ? AND analysis_json IS NOT NULL "
        "ORDER BY taken_on DESC LIMIT 1", (store.USER_ID, before.isoformat()),
    ).fetchone()
    if not r:
        return None
    return {"taken_on": r["taken_on"], **json.loads(r["analysis_json"])}


def delete_photo(conn: sqlite3.Connection, settings: Settings, photo_id: int) -> bool:
    row = get_photo(conn, photo_id)
    if not row:
        return False
    conn.execute("DELETE FROM progress_photos WHERE id = ?", (photo_id,))
    try:
        (settings.photo_dir / row["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return True


def save_image(settings: Settings, jpeg: bytes, taken_on: date) -> str:
    digest = hashlib.sha1(jpeg).hexdigest()[:10]
    rel = Path("progress") / taken_on.strftime("%Y") / f"{taken_on.isoformat()}-{digest}.jpg"
    path = settings.photo_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)
    return rel.as_posix()


def analyze(conn: sqlite3.Connection, settings: Settings, raw_image: bytes, *, taken_on: date, today: date) -> dict:
    """Store the photo (replacing the day's earlier one) and analyse it. When the
    model is unavailable the photo is kept and can be re-analysed later."""
    jpeg = gemini.prepare_image(raw_image)
    path = save_image(settings, jpeg, taken_on)
    existing = conn.execute("SELECT id, path FROM progress_photos WHERE user_id = ? AND taken_on = ?",
                            (store.USER_ID, taken_on.isoformat())).fetchone()
    if existing and existing["path"] != path:
        try:
            (settings.photo_dir / existing["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    ref = reference(conn)
    user = store.get_user(conn) or {}
    latest_w = store.latest_weight(conn)
    profile = {"sex": user.get("sex"), "height_cm": user.get("height_cm"), "weight_kg": latest_w["weight_kg"] if latest_w else None,
               "birth_date": user.get("birth_date")}
    analysis: dict | None = None
    error: str | None = None
    call_id: int | None = None
    try:
        analysis, res = gemini.Gemini(settings, conn, today=today).analyze_physique(
            jpeg, reference_name=ref["name"], reference_description=ref["description"],
            previous=previous_analysis(conn, taken_on), profile=profile,
        )
        call_id = res.call_id
    except gemini.GeminiDisabled as exc:
        error = f"not analysed: {exc}"
    except gemini.GeminiUnavailable as exc:
        if str(exc).startswith("bad output"):
            error = "not analysed: the model could not assess this photo (is the whole body visible, in decent light?). Retake or try again."
        else:
            error = f"not analysed: {exc}"

    conn.execute(
        """
        INSERT INTO progress_photos (user_id, taken_on, path, analysis_json, body_fat_pct, muscularity, progress_pct, actor_match,
                                     confidence, reference_name, llm_call_id, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, taken_on) DO UPDATE SET path = excluded.path, analysis_json = excluded.analysis_json,
          body_fat_pct = excluded.body_fat_pct, muscularity = excluded.muscularity, progress_pct = excluded.progress_pct,
          actor_match = excluded.actor_match, confidence = excluded.confidence, reference_name = excluded.reference_name,
          llm_call_id = excluded.llm_call_id, error = excluded.error, created_at = datetime('now')
        """,
        (store.USER_ID, taken_on.isoformat(), path, json.dumps(analysis) if analysis else None,
         analysis["body_fat_estimate_pct"] if analysis else None, analysis["muscularity_score"] if analysis else None,
         analysis["progress_to_reference_pct"] if analysis else None, analysis["actor_match_name"] if analysis else None,
         analysis["confidence"] if analysis else None, ref["name"], call_id, error),
    )
    row = _row(conn.execute("SELECT * FROM progress_photos WHERE user_id = ? AND taken_on = ?", (store.USER_ID, taken_on.isoformat())).fetchone())
    assert row is not None
    row["ok"] = analysis is not None
    return row


def reanalyze(conn: sqlite3.Connection, settings: Settings, photo_id: int, *, today: date) -> dict:
    row = get_photo(conn, photo_id)
    if row is None:
        raise KeyError(photo_id)
    raw = (settings.photo_dir / row["path"]).read_bytes()
    return analyze(conn, settings, raw, taken_on=date.fromisoformat(row["taken_on"]), today=today)


def series(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT taken_on, body_fat_pct, muscularity, progress_pct, actor_match, confidence FROM progress_photos "
        "WHERE user_id = ? AND analysis_json IS NOT NULL ORDER BY taken_on", (store.USER_ID,),
    ).fetchall()
    return [dict(r) for r in rows]
