"""Milestone 8 through the API: sick mode excludes rows from every analytic, holds
the target at maintenance without writing a row, ramps on exit, and merges
conservatively with exam mode."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import gemini
from app.config import Settings
from app.engine import guards
from app.main import create_app
from tests.test_gemini import Stub, gemini_response

TOKEN = "0123456789abcdef0123456789abcdef"
H = {"Authorization": f"Bearer {TOKEN}"}
CHICKEN = {"name": "Chicken breast", "kcal_100g": 165, "protein_100g": 31, "carbs_100g": 0, "fat_100g": 3.6}


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN), data_dir=tmp_path, db_path=tmp_path / "h.db",
                        photo_dir=tmp_path / "p", backup_dir=tmp_path / "b", scheduler_enabled=False, off_online_fallback=False)
    with TestClient(create_app(settings, static_dir=tmp_path / "nowhere")) as c:
        c.put("/api/v1/profile", json={"name": "A", "sex": "m", "birth_date": "2002-03-15", "height_cm": 180, "start_weight_kg": 82, "phase": "cut"}, headers=H)
        yield c


def today_of(client) -> date:
    return date.fromisoformat(client.get("/api/v1/today", headers=H).json()["day"])


def test_sick_mode_holds_maintenance_tags_rows_and_ramps(client):
    today = today_of(client)
    # clean history: a weigh-in and a meal yesterday and this morning
    client.post("/api/v1/weight", json={"day": (today - timedelta(days=1)).isoformat(), "weight_kg": 82.0}, headers=H)
    client.post("/api/v1/weight", json={"weight_kg": 79.5}, headers=H)  # this morning, before feeling ill
    client.post("/api/v1/food/entry", json={"grams": 100, "food": CHICKEN, "meal": "breakfast"}, headers=H)
    before = client.get("/api/v1/today", headers=H).json()
    assert before["target"]["phase"] == "cut" and before["mode"]["names"] == []

    r = client.post("/api/v1/modes", json={"type": "illness", "severity": "moderate", "fever_flag": True, "notes": "flu",
                                           "started_at": (today - timedelta(days=2)).isoformat()}, headers=H)
    assert r.status_code == 201
    ev = r.json()
    assert ev["retagged"]["weight_logs"] == 2 and ev["retagged"]["food_entries"] == 1  # rows since the start are now tagged
    assert client.post("/api/v1/modes", json={"type": "illness", "severity": "mild"}, headers=H).status_code == 422  # one at a time

    t = client.get("/api/v1/today", headers=H).json()
    assert t["mode"]["names"] == ["illness:moderate"] and t["mode"]["training"] == "blocked" and t["mode"]["fever_lock"]
    assert t["target"]["held"] and t["target"]["phase"] == "maintain" and t["target"]["kcal"] > before["target"]["kcal"]
    assert t["target"]["stored_kcal"] == before["target"]["kcal"] and "resumes" in t["target"]["held_reason"]
    assert t["next_session"]["blocked"]
    # no targets row was written; the stored target is untouched
    assert len(client.get("/api/v1/targets", headers=H).json()["history"]) == 1
    # the sick weigh-in is excluded from the trend
    trend = client.get("/api/v1/weight/trend", headers=H).json()
    assert [(p["raw_kg"], p["excluded"]) for p in trend["points"]] == [(82.0, True), (79.5, True)]
    assert trend["bands"][0]["type"] == "illness"
    # new rows while sick are tagged
    e = client.post("/api/v1/food/entry", json={"grams": 100, "food_id": None, "macros": {"kcal": 300, "protein_g": 10, "carbs_g": 40, "fat_g": 10}}, headers=H).json()
    assert e["health_event_id"] == ev["id"]
    # an override while sick is forced to maintain by the rail
    o = client.post("/api/v1/targets/override", json={"kcal": 1800, "phase": "cut", "reason": "no"}, headers=H).json()
    assert o["phase"] == "maintain" and guards.RAIL_ILLNESS_NO_DEFICIT in o["reason"]

    active = client.get("/api/v1/modes/active", headers=H).json()
    assert [a["id"] for a in active["active"]] == [ev["id"]] and active["ramping"] == []

    # recovered now: yesterday was the last sick day -> a 2-day illness, 2-day ramp covering today and tomorrow
    r = client.patch(f"/api/v1/modes/{ev['id']}", json={"end_now": True}, headers=H)
    assert r.status_code == 200 and r.json()["ended_at"] == (today - timedelta(days=1)).isoformat()
    assert r.json()["ramp_until"] == (today + timedelta(days=1)).isoformat()
    active = client.get("/api/v1/modes/active", headers=H).json()
    assert active["active"] == [] and [x["id"] for x in active["ramping"]] == [ev["id"]] and active["mode"]["names"] == [f"ramp:{ev['id']}"]
    t = client.get("/api/v1/today", headers=H).json()
    assert t["target"]["held"] and t["mode"]["training"] == "reduced" and not t["mode"]["progression_allowed"]  # ramp: no deficit, previous loads
    # rows logged today are clean again (the ramp is not an exclusion), the sick ones stay tagged
    w = client.post("/api/v1/weight", json={"weight_kg": 81.0}, headers=H).json()
    assert w["health_event_id"] is None
    pts = client.get("/api/v1/weight/trend", headers=H).json()["points"]
    assert [p["excluded"] for p in pts] == [True, False]  # 82.0 (2 days ago, sick) tagged; today's 81.0 replaced this morning's tagged row
    # the weekly review refuses to act while the window holds event or ramp days
    review = client.post("/api/v1/review/run", headers=H).json()["review"]
    assert review["assessment"] in ("window_not_clean", "insufficient_weigh_ins", "active_event")


def test_ending_an_event_earlier_untags_later_rows(client):
    today = today_of(client)
    ev = client.post("/api/v1/modes", json={"type": "travel", "started_at": (today - timedelta(days=3)).isoformat()}, headers=H).json()
    for i in range(3):
        client.post("/api/v1/weight", json={"day": (today - timedelta(days=i)).isoformat(), "weight_kg": 80.0 + i * 0.1}, headers=H)
    assert all(p["excluded"] for p in client.get("/api/v1/weight/trend", headers=H).json()["points"] if p["raw_kg"] < 82)
    client.patch(f"/api/v1/modes/{ev['id']}", json={"ended_at": (today - timedelta(days=2)).isoformat()}, headers=H)
    pts = {p["day"]: p["excluded"] for p in client.get("/api/v1/weight/trend", headers=H).json()["points"]}
    assert pts[(today - timedelta(days=2)).isoformat()] is True and pts[(today - timedelta(days=1)).isoformat()] is False and pts[today.isoformat()] is False
    assert client.patch(f"/api/v1/modes/{ev['id']}", json={"ended_at": (today - timedelta(days=9)).isoformat()}, headers=H).status_code == 422
    assert client.patch("/api/v1/modes/999", json={"end_now": True}, headers=H).status_code == 404


def test_sick_plus_exam_is_conservative_through_the_api(client):
    client.post("/api/v1/modes", json={"type": "exam"}, headers=H)
    t = client.get("/api/v1/today", headers=H).json()
    assert t["mode"]["names"] == ["exam"] and t["mode"]["training"] == "reduced" and t["mode"]["volume_cap"] == pytest.approx(2 / 3)
    assert t["target"]["held"] and t["target"]["phase"] == "maintain"
    client.post("/api/v1/modes", json={"type": "illness", "severity": "mild"}, headers=H)
    t = client.get("/api/v1/today", headers=H).json()
    assert sorted(t["mode"]["names"]) == ["exam", "illness:mild"]
    assert t["mode"]["training"] == "reduced" and t["mode"]["volume_cap"] == 0.5 and not t["mode"]["progression_allowed"]
    assert not t["mode"]["logging_strict"]
    bench = next(x for x in t["next_session"]["exercises"] if x["exercise"]["name"] == "Bench press")
    assert bench["sets"] == 2  # 3 x 0.5 rounded


def test_injury_omits_affected_exercises_only(client):
    client.post("/api/v1/modes", json={"type": "injury", "symptoms": {"affected_groups": ["shoulders", "chest"]}}, headers=H)
    t = client.get("/api/v1/today", headers=H).json()
    assert t["mode"]["names"] == ["injury"] and t["mode"]["training"] == "normal" and not t["target"]["held"]
    ex = {x["exercise"]["name"]: x for x in t["next_session"]["exercises"]}
    assert ex["Bench press"]["omitted"] and ex["Overhead press"]["omitted"] and not ex["Barbell row"]["omitted"]


def test_symptom_suggestion_is_never_applied(client, monkeypatch):
    stub = Stub([gemini_response({"severity": "moderate", "fever_likely": True, "rationale": "fever and aches"})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    r = client.post("/api/v1/modes/suggest", json={"text": "38.5 fever, aching all over"}, headers=H).json()
    assert r["ok"] and r["suggestion"]["severity"] == "moderate" and r["suggestion"]["fever_likely"]
    assert client.get("/api/v1/modes/active", headers=H).json()["active"] == []  # nothing started
    assert client.get("/api/v1/modes", headers=H).json()["events"] == []
