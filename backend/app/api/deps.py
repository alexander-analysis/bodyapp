"""Per-request dependencies: settings, a connection, and "today" in the user's zone."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterator

from fastapi import Depends, HTTPException, Request

from .. import db, services, store
from ..config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(settings: Settings = Depends(get_settings_dep)) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_today(settings: Settings = Depends(get_settings_dep)) -> date:
    return services.today_local(settings.tz)


def current_target_kcal(conn: sqlite3.Connection, day: date) -> int | None:
    t = store.current_target(conn, day)
    return t.kcal if t else None


def raise_for(exc: Exception) -> HTTPException:
    if isinstance(exc, services.NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, services.Invalid):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, services.Rejected):
        return HTTPException(status_code=409, detail={"reason": exc.reason, "rails": list(exc.tripped)})
    raise exc
