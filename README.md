# Health Platform

Single-user, self-hosted adaptive nutrition and training tracker for a
Raspberry Pi 5, reachable from a phone as an installable PWA. A deterministic
Python engine owns every number; Gemini only identifies food, parses text and
writes prose. Full spec: [docs/SPEC.md](docs/SPEC.md). Working rules for
contributors and future sessions: [CLAUDE.md](CLAUDE.md).

## Status

| # | Milestone | State |
| --- | --- | --- |
| 1 | Schema, engine, tests | done — `pytest` green (194 tests) |
| 2 | Docker, CI, Pi deploy, Tailscale | built — image, CI, compose, timers, runbook in [deploy/README.md](deploy/README.md); needs the one-time Pi/GitHub steps to go live |
| 3 | Manual logging, weight, PWA shell | |
| 4 | Trend weight and adaptive TDEE (wired to the API) | |
| 5 | Targets engine and guardrails (scheduled) | |
| 6 | Barcode + Open Food Facts mirror | |
| 7 | Gemini photo and text logging | |
| 8 | Modes, weekly narrative, `/ask` | |

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
