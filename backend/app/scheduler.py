"""In-process jobs (APScheduler, one uvicorn worker — see the Dockerfile).

nightly_rollup   00:10        roll up yesterday and today, refresh trend weights
prune            03:00        drop idempotency keys older than 24 h
weekly_review    Sun 23:30    rollup -> trend -> TDEE estimate row -> target review through the rails
off_refresh      1st 04:30    re-import the Open Food Facts mirror
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


def job_weekly(settings: Settings) -> None:
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            out = services.weekly_job(conn, today=services.today_local(settings.tz), settings=settings)
        log.info("weekly job: review=%s target=%s", out["review"]["assessment"], (out["review"].get("target") or {}).get("kcal"))
    finally:
        conn.close()


def job_off_refresh(settings: Settings) -> None:
    from . import off_import

    try:
        n = off_import.run_import(settings.db_path, limit=settings.off_import_limit)
        log.info("OFF mirror refreshed: %d products", n)
    except Exception:  # noqa: BLE001 — a failed refresh keeps last month's mirror
        log.exception("OFF mirror refresh failed")


def build(settings: Settings) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(job_nightly_rollup, CronTrigger(hour=0, minute=10), args=[settings], id="nightly_rollup",
                  replace_existing=True, misfire_grace_time=3600)
    sched.add_job(job_prune, CronTrigger(hour=3, minute=0), args=[settings], id="prune_idempotency",
                  replace_existing=True, misfire_grace_time=3600)
    # Sunday night, after the nightly rollup would have run for Saturday; the job rolls up Sunday itself.
    sched.add_job(job_weekly, CronTrigger(day_of_week="sun", hour=23, minute=30), args=[settings], id="weekly_review",
                  replace_existing=True, misfire_grace_time=6 * 3600)
    # Monthly, after the 04:00 backup: the mirror is ~400 MB of rows; commits in batches.
    sched.add_job(job_off_refresh, CronTrigger(day=1, hour=4, minute=30), args=[settings], id="off_refresh",
                  replace_existing=True, misfire_grace_time=24 * 3600)
    return sched
