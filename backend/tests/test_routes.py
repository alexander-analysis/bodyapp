"""API layer: routes, transactions, idempotency, rails on writes, tagging."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import db, store
from app.config import Settings
from app.engine import guards
from app.main import create_app

TOKEN = "0123456789abcdef0123456789abcdef"
H = {"Authorization": f"Bearer {TOKEN}"}
PROFILE = {"name": "Alex", "sex": "m", "birth_date": "2002-03-15", "height_cm": 180, "goal_weight_kg": 76,
           "start_weight_kg": 82, "phase": "cut"}
CHICKEN = {"name": "Chicken breast", "kcal_100g": 165, "protein_100g": 31, "carbs_100g": 0, "fat_100g": 3.6}


@pytest.fixture
def app_env(tmp_path: Path):
    settings = Settings(
        gemini_api_key=None, api_bearer_token=SecretStr(TOKEN), data_dir=tmp_path, db_path=tmp_path / "health.db",
        photo_dir=tmp_path / "p", backup_dir=tmp_path / "b", scheduler_enabled=False, off_online_fallback=False,
    )
    with TestClient(create_app(settings, static_dir=tmp_path / "nowhere")) as c:
        yield c, settings


@pytest.fixture
def client(app_env):
    c, _ = app_env
    assert c.put("/api/v1/profile", json=PROFILE, headers=H).status_code == 200
    return c


def today_iso(app_env) -> str:
    from app import services

    return services.today_local(app_env[1].tz).isoformat()


# --- profile and targets -------------------------------------------------------

def test_profile_creates_a_formula_based_initial_target(app_env):
    c, _ = app_env
    assert c.get("/api/v1/today", headers=H).json()["target"] is None
    r = c.put("/api/v1/profile", json=PROFILE, headers=H)
    assert r.status_code == 200
    t = r.json()["target"]
    assert t["set_by"] == "engine" and t["phase"] == "cut"
    assert "Mifflin" in t["reason"] and guards.RAIL_FAT_FLOOR in t["reason"]  # 0.8 g/kg x 82 = 66 -> clamped to 80
    assert t["kcal"] >= guards.KCAL_FLOOR and t["protein_g"] >= guards.PROTEIN_FLOOR_G and t["fat_g_min"] == guards.FAT_FLOOR_G
    # saving the profile again does not create a second target
    c.put("/api/v1/profile", json={**PROFILE, "name": "Alexander"}, headers=H)
    assert len(c.get("/api/v1/targets", headers=H).json()["history"]) == 1
    assert c.get("/api/v1/profile", headers=H).json()["profile"]["name"] == "Alexander"


def test_override_goes_through_the_rails(client):
    r = client.post("/api/v1/targets/override", json={"kcal": 1500, "reason": "trying"}, headers=H)
    assert r.status_code == 201
    assert r.json()["kcal"] == guards.KCAL_FLOOR and r.json()["set_by"] == "user"
    assert guards.RAIL_KCAL_FLOOR in r.json()["reason"] and r.json()["rails_tripped"] == [guards.RAIL_KCAL_FLOOR]
    assert client.post("/api/v1/targets/override", json={"reason": "nothing"}, headers=H).status_code == 422
    hist = client.get("/api/v1/targets", headers=H).json()
    assert hist["current"]["kcal"] == guards.KCAL_FLOOR and len(hist["history"]) == 2


# --- weight --------------------------------------------------------------------

def test_weight_upsert_trend_and_delete(client, app_env):
    today = date.fromisoformat(today_iso(app_env))
    for i, kg in enumerate((82.0, 81.8, 81.9, 81.5, 81.6, 81.4, 81.3, 81.2)):
        d = today - timedelta(days=7 - i)
        assert client.post("/api/v1/weight", json={"day": d.isoformat(), "weight_kg": kg}, headers=H).status_code == 201
    # same day again replaces, never duplicates
    client.post("/api/v1/weight", json={"weight_kg": 81.0, "waist_cm": 84}, headers=H)
    trend = client.get("/api/v1/weight/trend?days=30", headers=H).json()
    assert len(trend["points"]) == 8
    assert trend["latest"]["weight_kg"] == 81.0 and trend["latest"]["waist_cm"] == 84
    assert trend["trend_kg"] is not None and trend["rate_pct_week"] is not None
    assert client.delete(f"/api/v1/weight/{today.isoformat()}", headers=H).status_code == 200
    assert client.delete(f"/api/v1/weight/{today.isoformat()}", headers=H).status_code == 404
    assert len(client.get("/api/v1/weight/trend?days=30", headers=H).json()["points"]) == 7


# --- food ----------------------------------------------------------------------

def test_inline_food_is_saved_scaled_and_searchable(client):
    r = client.post("/api/v1/food/entry", json={"meal": "lunch", "grams": 150, "food": CHICKEN}, headers=H)
    assert r.status_code == 201
    e = r.json()
    assert e["kcal"] == 247.5 and e["protein_g"] == 46.5 and e["food_id"] is not None and e["input_method"] == "manual"
    assert e["confidence"] == 1.0 and e["health_event_id"] is None
    found = client.get("/api/v1/food/search?q=chicken", headers=H).json()["results"]
    assert [f["name"] for f in found] == ["Chicken breast"] and found[0]["source"] == "user"
    # log the saved food by id
    r = client.post("/api/v1/food/entry", json={"meal": "dinner", "grams": 200, "food_id": e["food_id"]}, headers=H)
    assert r.status_code == 201 and r.json()["kcal"] == 330.0
    today = client.get("/api/v1/today", headers=H).json()
    assert today["consumed"]["kcal"] == 577.5 and len(today["entries"]) == 2
    assert today["remaining"]["kcal"] == round(today["target"]["kcal"] - 577.5)


def test_macro_inconsistent_food_is_rejected(client):
    bad = {"name": "Mystery", "kcal_100g": 500, "protein_100g": 5, "carbs_100g": 5, "fat_100g": 5}
    r = client.post("/api/v1/food/entry", json={"grams": 100, "food": bad}, headers=H)
    assert r.status_code == 422 and "macro inconsistency" in r.json()["detail"]
    assert client.get("/api/v1/food/search?q=mystery", headers=H).json()["results"] == []


def test_manual_macros_entry_and_delete(client):
    r = client.post("/api/v1/food/entry", json={"grams": 300, "macros": {"kcal": 600, "protein_g": 30, "carbs_g": 60, "fat_g": 20}}, headers=H)
    assert r.status_code == 201
    eid = r.json()["id"]
    assert client.get("/api/v1/today", headers=H).json()["consumed"]["kcal"] == 600.0
    assert client.delete(f"/api/v1/food/entry/{eid}", headers=H).status_code == 200
    assert client.delete(f"/api/v1/food/entry/{eid}", headers=H).status_code == 404
    assert client.get("/api/v1/today", headers=H).json()["consumed"]["kcal"] == 0.0


def test_entry_needs_exactly_one_nutrition_source(client):
    assert client.post("/api/v1/food/entry", json={"grams": 100}, headers=H).status_code == 422
    assert client.post("/api/v1/food/entry", json={"grams": 100, "food_id": 999}, headers=H).status_code == 404


def test_favorites_round_trip_and_suggestions(client, app_env):
    fid = client.post("/api/v1/food/entry", json={"grams": 150, "food": CHICKEN}, headers=H).json()["food_id"]
    r = client.post("/api/v1/food/favorites", json={"label": "Chicken lunch", "items": [{"food_id": fid, "grams": 150}]}, headers=H)
    assert r.status_code == 201
    fav_id = r.json()["id"]
    r = client.post(f"/api/v1/food/favorites/{fav_id}/log", json={"meal": "lunch", "scale": 2}, headers=H)
    assert r.status_code == 201 and r.json()["entries"][0]["grams"] == 300 and r.json()["entries"][0]["input_method"] == "favorite"
    assert r.json()["entries"][0]["confidence"] == 0.9
    favs = client.get("/api/v1/food/favorites", headers=H).json()
    assert favs["favorites"][0]["use_count"] == 1 and favs["suggestions"] == []  # already a favorite: not suggested
    # a food logged three times at a similar portion, not yet a favorite, is suggested
    other = client.post("/api/v1/food/entry", json={"grams": 100, "food": {"name": "Oats", "kcal_100g": 379, "protein_100g": 13, "carbs_100g": 68, "fat_100g": 7}}, headers=H).json()["food_id"]
    for g in (100, 95, 108):
        client.post("/api/v1/food/entry", json={"grams": g, "food_id": other}, headers=H)
    sugg = client.get("/api/v1/food/favorites", headers=H).json()["suggestions"]
    assert sugg and sugg[0]["food_id"] == other and sugg[0]["times"] == 4
    assert client.delete(f"/api/v1/food/favorites/{fav_id}", headers=H).status_code == 200


def test_barcode_lookup_local_then_off_api(client, monkeypatch):
    from app import services

    r = client.post("/api/v1/food/barcode", json={"barcode": "5449000000996"}, headers=H)
    assert r.status_code == 200 and r.json()["food"] is None and r.json()["source"] is None  # fallback off in tests
    # a manually saved food with a barcode resolves locally
    client.post("/api/v1/food/entry", json={"grams": 100, "food": {**CHICKEN, "barcode": "1234567890123"}}, headers=H)
    r = client.post("/api/v1/food/barcode", json={"barcode": "1234567890123"}, headers=H)
    assert r.json()["source"] == "local" and r.json()["food"]["name"] == "Chicken breast"
    # the OFF API path, stubbed: cached as an 'off' food, second lookup is local
    monkeypatch.setattr(services, "fetch_off_product", lambda code, timeout=4.0: {
        "barcode": code, "name": "Skyr", "brand": "Arla", "kcal_100g": 63.0, "protein_100g": 11.0, "carbs_100g": 4.0, "fat_100g": 0.2, "fibre_100g": 0.0})
    client.app.state.settings.off_online_fallback = True  # type: ignore[attr-defined]
    r = client.post("/api/v1/food/barcode", json={"barcode": "5711953000000"}, headers=H)
    assert r.json()["source"] == "off_api" and r.json()["food"]["source"] == "off"
    assert client.post("/api/v1/food/barcode", json={"barcode": "5711953000000"}, headers=H).json()["source"] == "local"
    assert client.post("/api/v1/food/barcode", json={"barcode": "abc"}, headers=H).status_code == 422


def test_off_import_from_a_synthetic_export(client, app_env, tmp_path):
    import gzip

    from app import off_import

    _, settings = app_env
    cols = ["code", "product_name", "brands", "countries_tags", "energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g", "fiber_100g"]
    rows = [
        ["8410000000001", "Galletas Maria", "Cuetara", "en:spain", "440", "7", "75", "12", "3"],  # ok
        ["8410000000002", "French thing", "X", "en:france", "300", "5", "50", "8", "1"],  # wrong country
        ["8410000000003", "Hallucinated", "X", "en:spain,en:germany", "500", "5", "5", "5", "0"],  # macro inconsistency
        ["8410000000004", "No nutrition", "X", "en:netherlands", "", "", "", "", ""],  # empty
        ["8410000000005", "Knackebrot", "Wasa", "en:germany", "330", "9", "62", "1.5", "14"],  # ok
        ["8410000000006", "KJ only", "X", "en:czech-republic", "", "10", "20", "5", ""],  # kcal missing, no kj column
    ]
    path = tmp_path / "off.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(chr(9).join(cols) + chr(10))
        for r in rows:
            f.write(chr(9).join(r) + chr(10))
    n = off_import.run_import(settings.db_path, file=str(path))
    assert n == 2
    r = client.post("/api/v1/food/barcode", json={"barcode": "8410000000005"}, headers=H).json()
    assert r["source"] == "local" and r["food"]["name"] == "Knackebrot" and r["food"]["source"] == "off"
    assert client.post("/api/v1/food/barcode", json={"barcode": "8410000000003"}, headers=H).json()["food"] is None
    st = client.get("/api/v1/admin/off", headers=H).json()
    assert st["off_products"] == 2 and st["last_import"] and st["running"] is False
    # re-import updates in place, never duplicates
    assert off_import.run_import(settings.db_path, file=str(path)) == 2
    assert client.get("/api/v1/admin/off", headers=H).json()["off_products"] == 2
    assert [f["name"] for f in client.get("/api/v1/food/search?q=knack", headers=H).json()["results"]] == ["Knackebrot"]


# --- idempotency ---------------------------------------------------------------

def test_same_idempotency_key_writes_once(client):
    body = {"meal": "lunch", "grams": 150, "food": CHICKEN}
    h = {**H, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"}
    a = client.post("/api/v1/food/entry", json=body, headers=h)
    b = client.post("/api/v1/food/entry", json=body, headers=h)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert b.headers.get("idempotent-replayed") == "true" and a.headers.get("idempotent-replayed") is None
    assert len(client.get("/api/v1/today", headers=H).json()["entries"]) == 1
    # a different key writes again; the same key on a different route is not replayed
    c = client.post("/api/v1/food/entry", json=body, headers={**H, "Idempotency-Key": "other"})
    assert c.status_code == 201 and c.json()["id"] != a.json()["id"]
    w = client.post("/api/v1/weight", json={"weight_kg": 80}, headers=h)
    assert w.status_code == 201 and "weight_kg" in w.json()


def test_idempotent_replay_of_a_client_error(client):
    h = {**H, "Idempotency-Key": "bad-1"}
    a = client.post("/api/v1/food/entry", json={"grams": 100}, headers=h)
    b = client.post("/api/v1/food/entry", json={"grams": 100}, headers=h)
    assert a.status_code == b.status_code == 422 and b.headers.get("idempotent-replayed") == "true"


# --- training ------------------------------------------------------------------

def test_next_session_rotates_templates_and_prescribes(client):
    first = client.get("/api/v1/workout/next", headers=H).json()
    assert first["template"] == "Upper A" and not first["blocked"]
    bench = next(x for x in first["exercises"] if x["exercise"]["name"] == "Bench press")
    assert bench["weight_kg"] is None and bench["sets"] == 3
    r = client.post("/api/v1/workout", json={"template": "Upper A", "sets": [
        {"exercise_id": 2, "weight_kg": 60, "reps": 10, "rir": 1}, {"exercise_id": 2, "weight_kg": 60, "reps": 10, "rir": 1},
        {"exercise_id": 2, "weight_kg": 60, "reps": 10, "rir": 1}]}, headers=H)
    assert r.status_code == 201 and len(r.json()["sets"]) == 3
    assert client.get("/api/v1/workout/next", headers=H).json()["template"] == "Lower A"
    upper = client.get("/api/v1/workout/next?template=Upper A", headers=H).json()
    bench = next(x for x in upper["exercises"] if x["exercise"]["name"] == "Bench press")
    assert bench["weight_kg"] == 62.5 and bench["target_reps"] == 6  # rep_max 10 hit with RIR 1: +2.5 kg, reset to rep_min
    hist = client.get("/api/v1/workout/history/2", headers=H).json()
    assert hist["e1rm_trend"][0]["e1rm"] == 80.0 and hist["sessions"][0]["working_sets"] == 3
    vol = client.get("/api/v1/volume/weekly", headers=H).json()
    assert {g["muscle_group"]: g["sets"] for g in vol["groups"]} == {"chest": 3.0, "triceps": 1.5, "front_delts": 1.5}
    assert client.get("/api/v1/workout/next?template=Nope", headers=H).status_code == 404
    assert client.post("/api/v1/workout", json={"sets": [{"exercise_id": 999, "weight_kg": 1, "reps": 1}]}, headers=H).status_code == 404
    recent = client.get("/api/v1/workout/recent", headers=H).json()["workouts"]
    assert len(recent) == 1 and client.delete(f"/api/v1/workout/{recent[0]['id']}", headers=H).status_code == 200


def test_exercises_and_templates_are_editable(client):
    ex = client.get("/api/v1/exercises", headers=H).json()
    assert len(ex["exercises"]) == 22 and [t["name"] for t in ex["templates"]] == ["Upper A", "Lower A", "Upper B", "Lower B"]
    r = client.post("/api/v1/exercises", json={"name": "Dip", "muscle_group": "chest", "secondary_groups": ["triceps"]}, headers=H)
    assert r.status_code == 201 and r.json()["secondary_groups"] == "triceps"
    assert client.post("/api/v1/exercises", json={"name": "Dip", "muscle_group": "chest"}, headers=H).status_code == 409
    assert client.patch(f"/api/v1/exercises/{r.json()['id']}", json={"increment_kg": 5}, headers=H).json()["increment_kg"] == 5
    t = client.put("/api/v1/templates", json={"name": "Push", "slot": 5, "exercises": [{"exercise_id": 2, "sets": 4}]}, headers=H)
    assert t.status_code == 200 and t.json()["exercises"] == [{"exercise_id": 2, "sets": 4}]


# --- tagging, day metrics, export -----------------------------------------------

def test_rows_logged_during_an_event_are_tagged(client, app_env):
    _, settings = app_env
    today = date.fromisoformat(today_iso(app_env))
    # a clean weigh-in yesterday; the profile's start weight sits on today and is replaced below
    client.post("/api/v1/weight", json={"day": (today - timedelta(days=1)).isoformat(), "weight_kg": 82.0}, headers=H)
    conn = db.connect(settings.db_path)
    with db.transaction(conn):
        ev = store.insert_event(conn, type="illness", severity="mild", fever_flag=False, symptoms=None, started_at=today,
                                ended_at=None, ramp_until=None, created_by="user", notes=None)
    conn.close()
    w = client.post("/api/v1/weight", json={"weight_kg": 79.0}, headers=H).json()
    e = client.post("/api/v1/food/entry", json={"grams": 100, "food": CHICKEN}, headers=H).json()
    k = client.post("/api/v1/workout", json={"sets": [{"exercise_id": 1, "weight_kg": 80, "reps": 5}]}, headers=H).json()
    assert w["health_event_id"] == e["health_event_id"] == k["health_event_id"] == ev["id"]
    today_payload = client.get("/api/v1/today", headers=H).json()
    assert today_payload["mode"]["names"] == ["illness:mild"] and today_payload["mode"]["force_maintenance"]
    assert today_payload["next_session"]["exercises"][0]["sets"] <= 2  # volume cap 0.5 on 3 sets
    # the sick weigh-in is excluded from the trend, and the override rail forces maintenance
    trend = client.get("/api/v1/weight/trend", headers=H).json()
    assert [p["excluded"] for p in trend["points"]] == [False, True]
    r = client.post("/api/v1/targets/override", json={"kcal": 1800, "phase": "cut", "reason": "x"}, headers=H)
    assert r.status_code == 201 and r.json()["phase"] == "maintain" and guards.RAIL_ILLNESS_NO_DEFICIT in r.json()["reason"]


def test_day_metrics_and_rollup(client, app_env):
    today = today_iso(app_env)
    client.post("/api/v1/food/entry", json={"meal": "breakfast", "grams": 100, "food": CHICKEN}, headers=H)
    r = client.patch(f"/api/v1/day/{today}", json={"steps": 8000, "water_ml": 1500}, headers=H)
    assert r.status_code == 200 and r.json()["steps"] == 8000 and r.json()["kcal"] == 165.0 and r.json()["water_ml"] == 1500
    payload = client.get("/api/v1/today", headers=H).json()
    assert payload["day_metrics"] == {"steps": 8000, "water_ml": 1500, "sleep_h": None, "logged_complete": False}
    assert payload["tdee"]["method"] == "formula"


def test_export_is_a_zip_of_csvs(client):
    import io
    import zipfile

    r = client.get("/api/v1/export", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"users.csv", "targets.csv", "weight_logs.csv", "food_entries.csv", "foods.csv", "exercises.csv"} <= set(names)


def test_all_routes_require_the_token(client):
    for method, path in (("get", "/api/v1/today"), ("post", "/api/v1/weight"), ("get", "/api/v1/export"), ("put", "/api/v1/profile")):
        assert getattr(client, method)(path).status_code == 401


def test_weekly_summary_has_the_numbers_and_no_narrative_yet(client):
    client.post("/api/v1/food/entry", json={"meal": "lunch", "grams": 150, "food": CHICKEN}, headers=H)
    r = client.get("/api/v1/summary/weekly", headers=H)
    assert r.status_code == 200
    s = r.json()
    assert s["narrative"] is None and s["target"]["phase"] == "cut"
    assert s["days_logged"] == 1 and s["tdee"]["method"] == "formula"
    assert s["adherence"]["days_considered"] == 0  # one entry is not a logged-complete day
    assert s["volume"]["window_days"] == 7 and s["events"] == []


def test_weekly_job_records_tdee_and_reviews_through_the_rails(client, app_env):
    """Three weeks of a stalled cut: the job writes a tdee_estimates row, one target
    change with a reason, and a review_log row; a second run inside 7 days is rejected."""
    _, settings = app_env
    today = date.fromisoformat(today_iso(app_env))
    conn = db.connect(settings.db_path)
    with db.transaction(conn):
        # the initial target was written today by the fixture; pretend it is a month old
        conn.execute("UPDATE targets SET effective_from = ?", ((today - timedelta(days=30)).isoformat(),))
        for i in range(21, -1, -1):
            d = today - timedelta(days=i)
            store.upsert_weight(conn, day=d, weight_kg=82.0 + 0.02 * (i % 3), waist_cm=None, source="manual", health_event_id=None)
            from app.engine.types import DayRow
            store.upsert_rollup(conn, DayRow(d, kcal=1970.0, protein_g=165.0, carbs_g=200.0, fat_g=80.0, fibre_g=30.0,
                                             steps=9000, logged_complete=True, mean_confidence=0.9))
    conn.close()
    r = client.post("/api/v1/review/run", headers=H)
    assert r.status_code == 201
    out = r.json()
    assert out["tdee_estimate"]["method"] == "adaptive" and out["tdee_estimate"]["confidence"] > 0.5
    review = out["review"]
    assert review["assessment"] == "stalled" and review["target"] is not None
    assert review["target"]["kcal"] == 1970 - 150 and "stalled" in review["target"]["reason"]
    assert review["target"]["set_by"] == "engine" and review["triggered_by"] == "job"
    # second run within seven days: the rail rejects the change and the log says so
    again = client.post("/api/v1/review/run", headers=H).json()["review"]
    assert again["target"] is None and guards.RAIL_CHANGE_RATE in again["rails"]
    got = client.get("/api/v1/review", headers=H).json()
    assert got["latest"]["id"] == again["id"] and len(got["history"]) == 2 and len(got["tdee_history"]) == 2
    assert len(client.get("/api/v1/targets", headers=H).json()["history"]) == 2
