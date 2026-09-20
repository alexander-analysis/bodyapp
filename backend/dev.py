"""Local dev launcher: uvicorn with a scratch data dir and a fixed dev token.
Never used on the Pi (Docker runs uvicorn directly with /opt/health/.env)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("HEALTH_DEV_DATA", ROOT / ".dev-data"))
DATA.mkdir(exist_ok=True)
os.environ.setdefault("API_BEARER_TOKEN", "dev-token-dev-token-dev-token-dev-token")
os.environ.setdefault("DATA_DIR", str(DATA))
os.environ.setdefault("DB_PATH", str(DATA / "health.db"))
os.environ.setdefault("PHOTO_DIR", str(DATA / "photos"))
os.environ.setdefault("BACKUP_DIR", str(DATA / "backups"))
os.environ.setdefault("STATIC_DIR", str(ROOT / "frontend" / "dist"))
os.environ.setdefault("APP_VERSION", "dev")
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), reload=False, log_level="info")
