# Health Platform — working rules

Single-user, self-hosted nutrition + training tracker for a Raspberry Pi 5. The
full spec is `docs/SPEC.md`; this file carries the three things a future session
is most likely to violate.

## 1. The one rule

**The LLM never performs arithmetic and never sets a target.**

| Layer | Owns | Never does |
| --- | --- | --- |
| Deterministic engine (`backend/app/engine/`) | TDEE, trend weight, target adjustment, progression, mode rules, all guardrails | Interpret free text, identify food |
| Gemini layer | Food identification, text parsing, narrative summaries, answering questions about stored data | Compute any number written to `targets`, `daily_rollup`, or a guardrail |

Every number the user acts on must be traceable to a row and a formula. Gemini
output is parsed into a structured record, validated against bounds
(`engine/validation.py`), then handed to the engine. If Gemini is unavailable
the app degrades to manual entry and keeps working. `/food/photo` and
`/food/text` return candidates and write nothing; only `/food/entry` writes.

The engine is pure: dataclasses in (`engine/types.py`), dataclasses out, no
I/O. Keep it that way — every test in `backend/tests` runs without a database.

## 2. Guardrail constants — `backend/app/engine/guards.py`

```python
KCAL_FLOOR = 1700            # absolute minimum daily target
PROTEIN_FLOOR_G = 150
FAT_FLOOR_G = 80
MAX_TARGET_CHANGE_KCAL = 200
MIN_DAYS_BETWEEN_CHANGES = 7
MAX_LOSS_RATE_PCT_WEEK = 1.0
MAX_SICK_DAYS_BEFORE_REFERRAL = 7
MAX_GI_DAYS_BEFORE_REFERRAL = 3
MAX_UNLOGGED_GAP_DAYS = 4
```

Hard-coded module constants. Not env vars, not UI settings, not reachable by
Gemini. Changing one needs a code change, a commit, and updating
`tests/test_guards.py::test_constants_are_the_spec_values`. `apply_rails()` is
the single enforcement point: **every** `targets` row (engine review, mode
activation, user override) passes through it before it is written. Every clamp
writes a row with `set_by='engine'` and a `reason` naming the rail — never
silently adjust. The change-rate rail blocks engine writes only; floors and the
no-deficit-during-illness rail apply to user overrides too.

Scope statement to keep in the UI and in the code: this is a tracker, not a
clinician. When a rail trips it stops giving instructions rather than giving
cautious ones.

## 3. Analytics exclusion rule

Every row tagged with a `health_event_id` (illness, injury, travel, exam) is
stored and displayed but excluded from every computation: EWMA trend weight,
the 21-day adaptive TDEE window, volume and progression, weekly adherence.

Two symbols, and only two, spell this out:

- Python: `app.engine.exclusion.clean_rows()` — every analytic calls it.
- SQL: `app.db.CLEAN_PREDICATE` / `clean_where()` — every analytics query appends it.

Never write `health_event_id IS NULL` anywhere else. Audit those two and you
have audited the rule. `tests/test_exclusion.py` checks every analytic against
two invariants: dropping tagged rows changes nothing, and changing tagged rows'
values changes nothing.

Excluded ≠ unlogged: a sick day with tagged rows is not a logging gap, and days
covered by an event are transparent to the gap detector.

## Gemini boundaries (milestones 7-9)

- `app/gemini.py` is the only module that talks to the model. Every call:
  structured `responseSchema`, Pydantic validation *before* anything touches
  the DB, `engine.validation` bounds + macro check on every food candidate,
  hard daily cap (`GEMINI_DAILY_CALL_CAP`, logged when refused), retries with
  backoff, an `llm_calls` row per call, 24 h perceptual-hash cache for photos.
- `/food/photo` and `/food/text` return candidates and write nothing; only
  `/food/entry` writes, after the user confirms the portion.
- `/ask` passes rows scoped by the date range parsed from the question
  (`llm_services.parse_range`, max 92 days), never the whole database.
- The weekly narrative renders the summary numbers; the template fallback
  (`template_narrative`) renders the same numbers when the model is down.
- Physique analysis (`app/physique.py`) produces the model's *estimates*
  (body fat, muscularity, % toward the reference, actor match) for display
  only. They never feed targets, rollups or guardrails.
- Symptom severity suggestions are never applied automatically.

## Modes (milestone 8)

During an illness or its return ramp the Today target is *held* at
maintenance at read time (`services.effective_target`); no `targets` row is
written, so the cut resumes by itself. Writes during illness still go through
`apply_rails`, which forces `maintain`. Starting an event retags rows logged
since its start; ending one earlier untags the later rows. "Recovered now"
means yesterday was the last sick day (a same-day event stays active until
midnight); the ramp is `min(duration, 7)` days.

## Layout

```
backend/app/engine/     pure engine (the product): trend, tdee, targets, guards, progression, volume, modes, rollup, validation
backend/app/config.py   pydantic-settings; secrets are SecretStr, read once
backend/app/db.py       sqlite3 connection + PRAGMAs (WAL, foreign_keys, 64 MB cache) + CLEAN_PREDICATE
backend/app/migrate.py  runs Alembic programmatically (startup + tests)
backend/alembic/        hand-written SQL migrations (no autogenerate)
backend/tests/          pytest; fixtures/synthetic.py = 60 days with a 4-day illness on days 30-33
backend/app/backup.py   SQLite online-backup CLI (python -m app.backup); runs inside the container from a timer
backend/app/store.py    every SQL statement (rows in, dicts/dataclasses out)
backend/app/services.py orchestration per user action; llm_services.py for the Gemini jobs; physique.py for progress photos
backend/app/api/        routes.py (all of /api/v1), schemas.py (request bodies), deps.py
backend/app/scheduler.py nightly rollup 00:10, prune 03:00, weekly review Sun 23:30, OFF refresh 1st 04:30
backend/app/off_import.py Open Food Facts streaming importer (python -m app.off_import)
backend/dev.py          local dev launcher (scratch data dir, fixed dev token)
frontend/               React + Vite + TS; milestone-2 shell only, the PWA arrives in milestone 3
deploy/                 compose, Caddyfile, systemd units, rollback/backup/bootstrap scripts, runbook (README.md)
Dockerfile              multi-stage: node build (native platform) -> python:3.12-slim (linux/arm64)
.github/workflows/      tests -> arm64 image -> GHCR (:latest + :<sha>)
```

Deploy rules: secrets only in `/opt/health/.env` on the Pi; data on
`/mnt/storage/health`, never the SD card; `/api/v1/health` must return 503 when
the DB is unusable (that is what drives rollback); shell scripts and units stay
LF (`.gitattributes`).

## Commands

```bash
.venv/Scripts/python.exe -m pytest                      # all tests (Windows dev box)
cd backend && python -m alembic upgrade head            # needs DB_PATH or a .env
cd backend && python -m uvicorn app.main:app --reload   # needs GEMINI_API_KEY + API_BEARER_TOKEN (>= 32 chars)
```

Migrations also run automatically at app startup (`main.py` lifespan).

## Interpretation decisions (not in the spec, chosen here)

- Adaptive TDEE: mean intake over *logged-complete* clean days only (an
  incomplete day is unknown, not small); `n` = day-transitions between the
  trend endpoints less excluded days. Exact on a linear series.
- Blend confidence = `min(1, complete_days/21) × min(1, mean_confidence/0.6)`.
- Weekly review's "14+ clean days" is strict: the whole 14-day window must be
  free of event rows, active events and return-ramp days, with ≥ 8 weigh-ins.
  This is what makes the rehydration rebound unable to move a target.
- Rate bands between the named triggers (0.15–0.3 "slow", 0.8–1.0 "fast") are
  reported, not acted on. Stall action alternates kcal → steps, and uses steps
  when a cut would land below the floor.
- Post-event trend: resumes from the pre-event anchor; the first 5 clean
  readings use alpha × k/5. Excluded points get a display-only interpolation.
- Referral: GI > 3 days; any other illness > 7 days.
- Mode merge is per-field conservative (`modes._POLICY`); ramp volume cap 0.75.
- `users.goal_weight_kg` and `idempotency_keys` were added to the spec schema.
- Secrets: `.env` only, `chmod 600`, never committed. Never log or return them.
- `GEMINI_API_KEY` is optional (blank = disabled). The spec's "fail loudly if
  empty" intent is kept differently: `/health` reports `gemini: disabled`, startup
  logs a warning, and the Gemini client must raise rather than call without a
  key. Never add a silent fallback.
