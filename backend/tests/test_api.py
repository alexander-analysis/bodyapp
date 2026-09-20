from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

TOKEN = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        gemini_api_key=SecretStr("test-key-not-real"), api_bearer_token=SecretStr(TOKEN),
        data_dir=tmp_path, db_path=tmp_path / "health.db", photo_dir=tmp_path / "photos", backup_dir=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as c:  # entering runs the lifespan: migrations on the temp DB
        yield c


def test_health_is_public_and_reports_schema(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["schema"] == "0001"
    assert body["backup_newest_age_h"] is None
    assert "version" in body


def test_health_reports_backup_freshness(client, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "health-2026-09-19.db.gz").write_bytes(b"x")
    assert client.get("/api/v1/health").json()["backup_newest_age_h"] == 0.0


def test_other_api_routes_require_the_bearer_token(client):
    assert client.get("/api/v1/targets").status_code == 401
    assert client.get("/api/v1/targets", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/targets", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    # right token: past the middleware (404 because the route does not exist yet)
    assert client.get("/api/v1/targets", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_blank_gemini_key_means_disabled_and_short_token_is_refused():
    s = Settings(gemini_api_key=SecretStr("  "), api_bearer_token=SecretStr(TOKEN))
    assert s.gemini_api_key is None and not s.gemini_enabled
    assert Settings(gemini_api_key=None, api_bearer_token=SecretStr(TOKEN)).gemini_enabled is False
    assert Settings(gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN)).gemini_enabled
    with pytest.raises(ValueError, match="API_BEARER_TOKEN"):
        Settings(gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr("short"))


def test_health_reports_gemini_state(tmp_path):
    settings = Settings(
        gemini_api_key=None, api_bearer_token=SecretStr(TOKEN),
        data_dir=tmp_path, db_path=tmp_path / "health.db", photo_dir=tmp_path / "p", backup_dir=tmp_path / "b",
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/health").json()["gemini"].startswith("disabled")


def test_secrets_do_not_leak_in_repr():
    s = Settings(gemini_api_key=SecretStr("super-secret"), api_bearer_token=SecretStr(TOKEN))
    assert "super-secret" not in repr(s) and TOKEN not in repr(s)
    assert "super-secret" not in str(s.model_dump())


def test_health_returns_503_when_the_database_is_unreachable(tmp_path):
    settings = Settings(
        gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN),
        data_dir=tmp_path, db_path=tmp_path / "health.db", photo_dir=tmp_path / "p", backup_dir=tmp_path / "b",
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/health").status_code == 200
        # Replace the database file with a directory: opening it now fails.
        (tmp_path / "health.db").unlink()
        (tmp_path / "health.db-wal").unlink(missing_ok=True)
        (tmp_path / "health.db-shm").unlink(missing_ok=True)
        (tmp_path / "health.db").mkdir()
        r = c.get("/api/v1/health")
        assert r.status_code == 503 and r.json()["status"] == "error"


def test_spa_is_served_with_index_fallback(tmp_path):
    www = tmp_path / "www"
    (www / "assets").mkdir(parents=True)
    (www / "index.html").write_text("<html>shell</html>")
    (www / "assets" / "app.js").write_text("console.log(1)")
    settings = Settings(
        gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN),
        data_dir=tmp_path, db_path=tmp_path / "health.db", photo_dir=tmp_path / "p", backup_dir=tmp_path / "b",
    )
    with TestClient(create_app(settings, static_dir=www)) as c:
        assert c.get("/").text == "<html>shell</html>"
        assert c.get("/weight").text == "<html>shell</html>"  # client-side route
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/../../etc/passwd").status_code in (200, 404) and "root:" not in c.get("/../../etc/passwd").text
        assert c.get("/api/v1/nope", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_spa_missing_build_is_a_clear_404(tmp_path):
    settings = Settings(
        gemini_api_key=SecretStr("k"), api_bearer_token=SecretStr(TOKEN),
        data_dir=tmp_path, db_path=tmp_path / "health.db", photo_dir=tmp_path / "p", backup_dir=tmp_path / "b",
    )
    with TestClient(create_app(settings, static_dir=tmp_path / "nowhere")) as c:
        r = c.get("/")
        assert r.status_code == 404 and r.json()["detail"] == "frontend not built"
