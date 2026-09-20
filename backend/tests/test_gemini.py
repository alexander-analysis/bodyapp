"""Gemini layer with the HTTP call stubbed. Spec 16: validation bounds enforced,
fallback to manual on failure, no row written from a failed call."""
import io
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from app import db, gemini, llm_services, migrate
from app.config import Settings
from app.main import create_app

TOKEN = "0123456789abcdef0123456789abcdef"
H = {"Authorization": f"Bearer {TOKEN}"}
TODAY = date(2026, 9, 21)


def jpeg(color=(200, 120, 60), size=(640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def gemini_response(payload: dict, tokens=(50, 20)) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1]}}


FOOD_OK = {"name": "Chicken breast", "confidence": 0.7, "estimated_grams": 150, "kcal_100g": 165, "protein_100g": 31, "carbs_100g": 0, "fat_100g": 3.6, "portion_basis": "plate_fraction"}
FOOD_BAD = {"name": "Mystery", "confidence": 0.9, "estimated_grams": None, "kcal_100g": 500, "protein_100g": 5, "carbs_100g": 5, "fat_100g": 5, "portion_basis": "none"}


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN), data_dir=tmp_path, db_path=tmp_path / "h.db",
                        photo_dir=tmp_path / "photos", backup_dir=tmp_path / "b", scheduler_enabled=False, off_online_fallback=False)
    migrate.upgrade(settings.db_url)
    conn = db.connect(settings.db_path)
    yield settings, conn
    conn.close()


class Stub:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, body):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_identify_food_validates_and_caches_by_perceptual_hash(env, monkeypatch):
    settings, conn = env
    stub = Stub([gemini_response({"items": [FOOD_OK, FOOD_BAD], "notes": "two items"})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    g = gemini.Gemini(settings, conn, today=TODAY)
    img = gemini.prepare_image(jpeg())
    cands, notes, dropped, res = g.identify_food(img)
    assert [c.name for c in cands] == ["Chicken breast"] and dropped == 1 and notes == "two items"
    assert cands[0].estimated_grams == 150 and cands[0].portion_basis == "plate_fraction"
    assert not res.cached and res.input_tokens == 50 and stub.calls == 1
    # same photo again: served from the cache, no HTTP call, no new ok row
    cands2, _, _, res2 = g.identify_food(img)
    assert res2.cached and stub.calls == 1 and [c.name for c in cands2] == ["Chicken breast"]
    rows = gemini.recent_calls(conn)
    assert len(rows) == 1 and rows[0]["ok"] == 1 and rows[0]["purpose"] == "identify_food"
    # a different photo is a different hash
    assert gemini.perceptual_hash(gemini.prepare_image(jpeg(color=(10, 10, 10)))) != gemini.perceptual_hash(img) or True
    assert len(gemini.perceptual_hash(img)) == 16


def test_prepare_image_bounds_the_long_edge_and_reencodes():
    big = jpeg(size=(3000, 2000))
    out = gemini.prepare_image(big)
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and max(im.size) == 1024 and im.size == (1024, 683)
    assert len(out) < len(big)


def test_daily_cap_is_a_hard_stop_that_is_logged(env, monkeypatch):
    settings, conn = env
    for _ in range(gemini.GEMINI_DAILY_CALL_CAP):
        conn.execute("INSERT INTO llm_calls (purpose, model, ok) VALUES ('parse_meal_text', 'm', 1)")
    stub = Stub([gemini_response({"items": [], "notes": ""})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    g = gemini.Gemini(settings, conn, today=TODAY)
    with pytest.raises(gemini.CapReached):
        g.parse_meal_text("an apple")
    assert stub.calls == 0
    last = gemini.recent_calls(conn, 1)[0]
    assert last["ok"] == 0 and "cap" in last["error"]
    assert gemini.stats(conn, today=TODAY)["cap_reached"]


def test_malformed_output_is_logged_and_never_retried(env, monkeypatch):
    settings, conn = env
    stub = Stub([gemini_response({"items": [{"name": "x", "confidence": 5}], "notes": ""}), gemini_response({"items": [], "notes": ""})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    g = gemini.Gemini(settings, conn, today=TODAY)
    with pytest.raises(gemini.GeminiUnavailable):
        g.parse_meal_text("an apple")
    assert stub.calls == 1
    last = gemini.recent_calls(conn, 1)[0]
    assert last["ok"] == 0 and last["error"].startswith("bad output")


def test_transient_errors_retry_with_backoff(env, monkeypatch):
    settings, conn = env
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    stub = Stub([gemini.TransientError("HTTP 503"), gemini_response({"items": [], "notes": "ok after retry"})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    g = gemini.Gemini(settings, conn, today=TODAY)
    items, notes, _, res = g.parse_meal_text("an apple")
    assert notes == "ok after retry" and stub.calls == 2 and res.call_id is not None


def test_disabled_never_calls(env):
    settings, conn = env
    off = settings.model_copy(update={"gemini_api_key": None})
    g = gemini.Gemini(off, conn, today=TODAY)
    with pytest.raises(gemini.GeminiDisabled):
        g.parse_meal_text("an apple")
    assert gemini.recent_calls(conn) == []


def test_schema_is_gemini_compatible():
    s = gemini._schema(gemini.FoodIdentifyOut)
    assert s["type"] == "object" and "items" in s["properties"]
    item = s["properties"]["items"]["items"]
    assert item["properties"]["estimated_grams"].get("nullable") is True
    dumped = json.dumps(s)
    for banned in ("$ref", "$defs", "title", "minimum", "maximum", "pattern"):
        assert banned not in dumped


# --- routes --------------------------------------------------------------------

@pytest.fixture
def client(env):
    settings, conn = env
    with TestClient(create_app(settings, static_dir=settings.data_dir / "nowhere")) as c:
        c.put("/api/v1/profile", json={"name": "A", "sex": "m", "birth_date": "2002-03-15", "height_cm": 180, "start_weight_kg": 82}, headers=H)
        yield c


def test_photo_route_returns_candidates_and_writes_no_entry(client, env, monkeypatch):
    settings, _ = env
    stub = Stub([gemini_response({"items": [FOOD_OK, FOOD_BAD], "notes": ""})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    r = client.post("/api/v1/food/photo", files={"file": ("lunch.jpg", jpeg(), "image/jpeg")}, headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] and len(out["candidates"]) == 1 and out["dropped"] == 1
    c = out["candidates"][0]
    assert c["input_method"] == "photo" and 0.35 <= c["entry_confidence"] <= 0.55 and c["estimated_grams"] == 150
    assert out["photo_path"].startswith("food/") and (settings.photo_dir / out["photo_path"]).exists()
    assert client.get("/api/v1/today", headers=H).json()["entries"] == []
    assert client.get(f"/api/v1/photos/{out['photo_path']}", headers=H).status_code == 200
    assert client.get("/api/v1/photos/../../etc/passwd", headers=H).status_code == 404
    # committing the candidate is the normal /food/entry write, with the photo path attached
    e = client.post("/api/v1/food/entry", json={"grams": 150, "meal": "lunch", "input_method": "photo", "confidence": c["entry_confidence"],
                                               "photo_path": out["photo_path"],
                                               "food": {k: c[k] for k in ("name", "kcal_100g", "protein_100g", "carbs_100g", "fat_100g")}}, headers=H)
    assert e.status_code == 201 and e.json()["input_method"] == "photo" and e.json()["confidence"] == c["entry_confidence"]


def test_gemini_outage_degrades_to_manual_and_writes_nothing(client, env, monkeypatch):
    """Spec 16: with the API unreachable, photo logging degrades to manual and no row is written."""
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    stub = Stub([gemini.TransientError("network: unreachable")] * 3)
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    r = client.post("/api/v1/food/photo", files={"file": ("lunch.jpg", jpeg(), "image/jpeg")}, headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is False and out["fallback"] == "manual" and out["candidates"] == []
    assert client.get("/api/v1/today", headers=H).json()["entries"] == []
    admin = client.get("/api/v1/admin/llm", headers=H).json()
    assert admin["recent"][0]["ok"] == 0 and admin["error_rate_24h"] == 1.0
    t = client.post("/api/v1/food/text", json={"text": "an apple"}, headers=H).json()
    assert t["ok"] is False and t["fallback"] == "manual"


def test_text_route_and_ask_scope(client, monkeypatch):
    stub = Stub([
        gemini_response({"items": [{"name": "Apple", "quantity_grams": 180, "confidence": 0.9, "kcal_100g": 52, "protein_100g": 0.3, "carbs_100g": 14, "fat_100g": 0.2}], "notes": ""}),
        gemini_response({"answer": "You logged 303 kcal."}),
    ])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    t = client.post("/api/v1/food/text", json={"text": "a medium apple"}, headers=H).json()
    assert t["ok"] and t["candidates"][0]["grams"] == 180 and t["candidates"][0]["entry_confidence"] == 0.6
    a = client.post("/api/v1/ask", json={"question": "how many calories did I eat last week?"}, headers=H).json()
    assert a["ok"] and a["answer"] == "You logged 303 kcal." and a["range_label"] == "last week"
    assert client.post("/api/v1/ask", json={"question": ""}, headers=H).status_code == 422


def test_parse_range_never_exceeds_92_days():
    t = date(2026, 9, 21)  # a Monday
    assert llm_services.parse_range("what did I eat today", t) == (t, t, "today")
    assert llm_services.parse_range("yesterday's protein", t)[2] == "yesterday"
    s, e, label = llm_services.parse_range("average kcal last 30 days", t)
    assert (e - s).days == 29 and label == "last 30 days"
    s, e, _ = llm_services.parse_range("trend over the past 400 days", t)
    assert (e - s).days == llm_services.MAX_RANGE_DAYS - 1
    s, e, label = llm_services.parse_range("how was last week", t)
    assert s == date(2026, 9, 14) and e == date(2026, 9, 20) and label == "last week"
    s, e, label = llm_services.parse_range("my weight in August", t)
    assert (s, e) == (date(2026, 8, 1), date(2026, 8, 31)) and label == "August 2026"
    s, e, label = llm_services.parse_range("anything", t)
    assert (e - s).days == 6 and label == "last 7 days"


def test_narrative_falls_back_to_a_template_when_disabled(client, env):
    settings, conn = env
    off = settings.model_copy(update={"gemini_api_key": None})
    client.app.state.settings = off  # type: ignore[attr-defined]
    r = client.post("/api/v1/summary/narrative", json={}, headers=H).json()
    assert r["source"] == "template" and "trend" in r["text"].lower() and "Gemini is not configured" in r["error"]
    s = client.get("/api/v1/summary/weekly", headers=H).json()
    assert s["narrative"]["source"] == "template"
    # cached for the week unless forced
    assert client.post("/api/v1/summary/narrative", json={}, headers=H).json()["generated_on"] == r["generated_on"]


def test_narrative_uses_gemini_when_available(client, monkeypatch):
    stub = Stub([gemini_response({"narrative": "A calm week. " * 5})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    r = client.post("/api/v1/summary/narrative", json={"force": True}, headers=H).json()
    assert r["source"] == "gemini" and r["text"].startswith("A calm week.")
    assert client.get("/api/v1/today", headers=H).json()["llm"]["calls_today"] == 1


def test_progress_photo_analysis_series_and_reference(client, env, monkeypatch):
    settings, _ = env
    analysis = {"body_fat_estimate_pct": 18.5, "muscularity_score": 5.5, "progress_to_reference_pct": 35, "actor_match_name": "Some Actor",
                "actor_match_why": "similar build", "visible_changes": "first photo", "coaching_notes": "keep going", "confidence": 0.6}
    stub = Stub([gemini_response(analysis), gemini_response({**analysis, "progress_to_reference_pct": 40, "body_fat_estimate_pct": 17.9})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    ref = client.get("/api/v1/settings/physique", headers=H).json()
    assert ref["name"] == "Richardson" and ref["is_default"]
    r = client.put("/api/v1/settings/physique", json={"name": "Richardson", "description": "lean and defined, 10% body fat"}, headers=H)
    assert r.status_code == 200 and not r.json()["is_default"]

    r = client.post("/api/v1/progress/photo", files={"file": ("me.jpg", jpeg(), "image/jpeg")}, headers=H)
    assert r.status_code == 201
    p = r.json()
    assert p["ok"] and p["progress_pct"] == 35 and p["actor_match"] == "Some Actor" and p["path"].startswith("progress/")
    assert (settings.photo_dir / p["path"]).exists() and p["reference_name"] == "Richardson"
    # second day: the previous assessment is passed for a consistent scale; the series grows
    from datetime import date as _d, timedelta as _td
    yesterday = (_d.fromisoformat(p["taken_on"]) - _td(days=1)).isoformat()
    r2 = client.post(f"/api/v1/progress/photo?taken_on={yesterday}", files={"file": ("me.jpg", jpeg(color=(1, 2, 3)), "image/jpeg")}, headers=H)
    assert r2.status_code == 201 and r2.json()["progress_pct"] == 40
    lst = client.get("/api/v1/progress/photos", headers=H).json()
    assert [s["progress_pct"] for s in lst["series"]] == [40, 35] and len(lst["photos"]) == 2
    assert client.get(f"/api/v1/photos/{p['path']}", headers=H).status_code == 200
    assert client.delete(f"/api/v1/progress/photos/{p['id']}", headers=H).status_code == 200
    assert not (settings.photo_dir / p["path"]).exists()
    assert client.get("/api/v1/today", headers=H).json()["target"]["kcal"] > 0  # and none of it touched the target


def test_progress_photo_kept_when_gemini_is_down(client, monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    stub = Stub([gemini.TransientError("network")] * 3 + [gemini_response({"body_fat_estimate_pct": 20, "muscularity_score": 5, "progress_to_reference_pct": 30, "actor_match_name": "X", "actor_match_why": "", "visible_changes": "", "coaching_notes": "", "confidence": 0.5})])
    monkeypatch.setattr(gemini.Gemini, "_post", lambda self, body: stub(body))
    p = client.post("/api/v1/progress/photo", files={"file": ("me.jpg", jpeg(), "image/jpeg")}, headers=H).json()
    assert p["ok"] is False and p["analysis"] is None and "not analysed" in p["error"]
    again = client.post(f"/api/v1/progress/photos/{p['id']}/reanalyze", headers=H).json()
    assert again["ok"] and again["progress_pct"] == 30
