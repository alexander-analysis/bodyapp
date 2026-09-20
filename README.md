# Health Platform

Single-user, self-hosted adaptive nutrition and training tracker for a
Raspberry Pi 5, reachable from a phone as an installable PWA. A deterministic
Python engine owns every number; Gemini only identifies food, parses text and
writes prose. Full spec: [docs/SPEC.md](docs/SPEC.md). Working rules for
contributors and future sessions: [CLAUDE.md](CLAUDE.md).

## Status

All eight spec milestones are built, plus a progress-photo feature (§9 below). 238 backend tests.

| # | Milestone | State |
| --- | --- | --- |
| 1 | Schema, engine, tests | done |
| 2 | Docker, CI, Pi deploy, Tailscale | built; CI publishes `ghcr.io/alexander-analysis/bodyapp` on every push — the Pi bootstrap is the one step not yet run |
| 3 | Manual logging, weight, PWA shell, offline queue | done, verified in the browser including an API outage |
| 4 | Trend weight and adaptive TDEE | done (live on Today; weekly `tdee_estimates` rows) |
| 5 | Targets engine and guardrails, scheduled | done (Sunday job, `review_log`, manual run) |
| 6 | Barcode + Open Food Facts mirror | done (streaming importer, monthly job, live OFF fallback, scanner) |
| 7 | Gemini photo and text logging | done (validated, capped, cached, audited; verified against the live API) |
| 8 | Modes, weekly narrative, `/ask` | done |
| 9 | Progress photos: physique analysis vs a reference, charted | done (display-only estimates) |

## Development

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest
```

Running the API locally needs `GEMINI_API_KEY` and a 32+ character
`API_BEARER_TOKEN` in the environment or a `.env` (see `.env.example`):

```bash
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

`GET /api/v1/health` is public; everything else under `/api/v1` needs
`Authorization: Bearer <token>`.

Frontend dev server (proxies `/api` to :8000):

```bash
cd frontend && npm install && npm run dev
```

## Deploy

`git push main` builds an arm64 image and the Pi picks it up unattended. One-time
setup, verification steps and the runbook: [deploy/README.md](deploy/README.md).
