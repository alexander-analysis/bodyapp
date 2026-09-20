"""FastAPI application. Routes live under ``/api/v1``.

Auth is a single static bearer token checked by middleware on every
``/api/v1`` route except ``/api/v1/health`` (the Docker healthcheck has no
token). Migrations run at startup so a deploy is a container restart.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import db, migrate, scheduler, store
from .api.routes import router as api_router
from .config import API_PREFIX, Settings, get_settings
from .engine.guards import SCOPE_STATEMENT

log = logging.getLogger("health")

APP_VERSION = os.environ.get("APP_VERSION", "dev")
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/srv/www"))
PUBLIC_PATHS = {f"{API_PREFIX}/health"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _idempotent_lookup(cfg: Settings, key: str) -> dict | None:
    conn = db.connect(cfg.db_path)
    try:
        return store.get_idempotent(conn, key)
    finally:
        conn.close()


def _idempotent_store(cfg: Settings, key: str, route: str, status: int, body: str) -> None:
    conn = db.connect(cfg.db_path)
    try:
        with db.transaction(conn):
            store.put_idempotent(conn, key=key, route=route, status_code=status, body=body)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for d in (settings.data_dir, settings.photo_dir, settings.backup_dir):
        d.mkdir(parents=True, exist_ok=True)
    migrate.upgrade(settings.db_url)
    if not settings.gemini_enabled:
        log.warning("GEMINI_API_KEY not set: photo/text identification, narratives and /ask are DISABLED; manual logging works")
    sched = scheduler.build(settings) if settings.scheduler_enabled else None
    if sched:
        sched.start()
    log.info("started version=%s db=%s gemini=%s jobs=%s", APP_VERSION, settings.db_path,
             settings.gemini_model if settings.gemini_enabled else "disabled", "on" if sched else "off")
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)


def create_app(settings: Settings | None = None, static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="Health Platform", version=APP_VERSION, lifespan=lifespan, description=SCOPE_STATEMENT)
    cfg = settings or get_settings()
    app.state.settings = cfg
    app.include_router(api_router)

    @app.middleware("http")
    async def idempotency(request: Request, call_next):  # noqa: ANN001, ANN202
        """Mutating requests carrying an Idempotency-Key are replayed from the
        stored response for 24 h (spec 10): the offline queue retries safely."""
        key = request.headers.get("idempotency-key", "").strip()
        path = request.url.path
        if not key or len(key) > 128 or request.method not in MUTATING or not path.startswith(API_PREFIX):
            return await call_next(request)
        route = f"{request.method} {path}"
        hit = await run_in_threadpool(_idempotent_lookup, cfg, key)
        if hit and hit["route"] == route:
            return Response(content=hit["response_json"], status_code=hit["status_code"], media_type="application/json",
                            headers={"Idempotent-Replayed": "true"})
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        if response.status_code < 500:
            await run_in_threadpool(_idempotent_store, cfg, key, route, response.status_code, body.decode("utf-8", "replace"))
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content=body, status_code=response.status_code, headers=headers, media_type=response.media_type)

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path
        if path.startswith(API_PREFIX) and path not in PUBLIC_PATHS:
            header = request.headers.get("authorization", "")
            scheme, _, token = header.partition(" ")
            expected = cfg.api_bearer_token.get_secret_value()
            if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), expected):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get(f"{API_PREFIX}/health")
    def health() -> JSONResponse:
        started = time.perf_counter()
        status = "ok"
        db_status = "ok"
        schema = None
        try:
            conn = db.connect(cfg.db_path)
            try:
                conn.execute("SELECT 1").fetchone()
                schema = db.schema_version(conn)
                if schema is None:
                    db_status = "unmigrated"
                    status = "degraded"
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — a liveness probe must not raise
            db_status = f"error: {type(exc).__name__}"
            status = "error"
        payload = {
            "status": status,
            "version": APP_VERSION,
            "db": db_status,
            "schema": schema,
            "gemini": cfg.gemini_model if cfg.gemini_enabled else "disabled: no GEMINI_API_KEY",
            "backup_newest_age_h": _newest_backup_age_h(cfg.backup_dir),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        # 503 makes the Docker healthcheck fail, which is what drives the rollback timer.
        return JSONResponse(payload, status_code=503 if status == "error" else 200)

    _mount_spa(app, static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built PWA. API routes are registered first, so they win; anything
    else resolves to a file under the build or falls back to index.html."""
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):  # noqa: ANN202
        if path.startswith(API_PREFIX.lstrip("/")):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if not index.is_file():
            return JSONResponse({"detail": "frontend not built", "version": APP_VERSION}, status_code=404)
        candidate = (static_dir / path).resolve() if path else index
        if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _newest_backup_age_h(backup_dir: Path) -> float | None:
    try:
        files = list(backup_dir.glob("health-*.db.gz"))
    except OSError:
        return None
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return round((time.time() - newest) / 3600, 1)


app = create_app()
