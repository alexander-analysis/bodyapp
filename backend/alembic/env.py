"""Alembic environment. The URL comes from ``-x db_url=...`` (tests, tooling)
or from the app settings; never from alembic.ini."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, event

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # migrations are hand-written SQL; no autogenerate


def _db_url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    if "db_url" in x:
        return x["db_url"]
    if os.environ.get("DB_PATH"):
        return f"sqlite:///{Path(os.environ['DB_PATH']).as_posix()}"
    from app.config import get_settings

    return get_settings().db_url


def run_migrations_offline() -> None:
    context.configure(url=_db_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_db_url())

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
