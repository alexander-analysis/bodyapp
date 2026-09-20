"""In-process jobs (APScheduler, one uvicorn worker — see the Dockerfile).

nightly_rollup   00:10  roll up yesterday and today, refresh trend weights
prune            03:00  drop idempotency keys older than 24 h
(milestone 5 adds the Sunday-night weekly review; milestone 6 the monthly OFF refresh)
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, services, store
from .config import Settings

log = logging.getLogger("health.jobs")


def job_nightly_rollup(settings: Settings) -> None:
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            rows = services.nightly_rollup(conn, today=services.today_local(settings.tz))
        log.info("nightly rollup: %s", [r["day"] for r in rows])
    finally:
        conn.close()


def job_prune(settings: Settings) -> None:
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            n = store.prune_idempotent(conn)
        log.info("pruned %d idempotency keys", n)
    finally:
        conn.close()


def build(settings: Settings) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(job_nightly_rollup, CronTrigger(hour=0, minute=10), args=[settings], id="nightly_rollup",
                  replace_existing=True, misfire_grace_time=3600)
    sched.add_job(job_prune, CronTrigger(hour=3, minute=0), args=[settings], id="prune_idempotency",
                  replace_existing=True, misfire_grace_time=3600)
    return sched
