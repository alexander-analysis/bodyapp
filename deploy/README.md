# Deploying to the Pi

Target state (spec §13): `git push main` → GitHub Actions tests and builds an
arm64 image → GHCR → Watchtower on the Pi pulls and recreates the container →
healthcheck passes or a systemd timer pins the previous SHA. The Pi has no
inbound access and holds no GitHub credentials.

```
phone ──HTTPS (Tailscale)──▶ caddy:443 ──▶ api:8000 (uvicorn, serves /api and the PWA)
                                              │
                                              └─▶ /data = /mnt/storage/health (SQLite WAL, photos, backups)
watchtower ── polls GHCR every 5 min ── recreates api on a new :latest
health-rollback.timer ── every 2 min ── unhealthy? pin last-good SHA / healthy? record SHA, un-pin when fixed
health-backup.timer ── 04:00 daily ── SQLite online backup → /data/backups, 30 kept, verified
```

## One-time setup

### 1. GitHub

1. Create the repository, push `main`. The workflow in `.github/workflows/deploy.yml`
   runs automatically (tests → build → push to `ghcr.io/<user>/<repo>`).
2. After the first successful run, open the package on GitHub → *Package settings*
   → either make it **public** (simplest — the image contains no secrets; `.env`
   is excluded by `.dockerignore`) or keep it private and run `docker login ghcr.io`
   on the Pi with a classic PAT that has `read:packages`.

### 2. Tailscale admin console

- DNS → enable **MagicDNS** and **HTTPS Certificates**.
- Note the tailnet name; the Pi's hostname becomes `<pi-hostname>.<tailnet>.ts.net`.
  If you want the name `health.…`, set the Pi's Tailscale hostname:
  `sudo tailscale up --ssh --hostname health`.

### 3. On the Pi (as your normal user)

The repo is `alexander-analysis/bodyapp`; the image is public, so no `docker login`.

```bash
sudo mkdir -p /opt/health && sudo chown "$USER" /opt/health
git clone https://github.com/alexander-analysis/bodyapp.git /opt/health/src
cp /opt/health/src/.env.example /opt/health/.env
chmod 600 /opt/health/.env
nano /opt/health/.env          # API_BEARER_TOKEN (openssl rand -hex 32), GHCR_IMAGE, TS_HOSTNAME; GEMINI_API_KEY optional
bash /opt/health/src/deploy/bootstrap.sh
```

`bootstrap.sh` prints a Tailscale login URL on a fresh Pi — open it and sign in;
the script continues once the Pi has joined the tailnet. Afterwards, from the
app's Settings page, press **Import / refresh now** once to load the Open Food
Facts mirror (or wait for the monthly job on the 1st).

`bootstrap.sh` is idempotent: installs Docker and Tailscale if missing, creates
`/mnt/storage/health/{photos,backups}`, copies the runtime files to
`/opt/health`, installs the four systemd timers, pulls and starts the stack, and
waits for the healthcheck. It refuses to run as root and refuses to run without
a complete `.env`.

The box is shared with the media server, both NewBlood bots, Mordhau and
Thorzon. Every container has a `mem_limit` (api 512 MB, caddy 64 MB,
watchtower 64 MB). If the media server transcodes with ffmpeg, cap its threads
— an unbounded transcode will OOM this stack first and look like an app bug.

### 4. Verify (milestone 2 "done when")

```bash
curl -fsS https://health.<tailnet>.ts.net/api/v1/health      # from any tailnet device
/opt/health/backup.sh && /opt/health/restore-test.sh          # a backup exists and restores cleanly
docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' health-api-1   # running SHA
cat /opt/health/.last_good_sha
systemctl list-timers 'health-*'
```

Then the integration checks from spec §16:

- **Deploy**: push a trivial commit; within ~6 minutes (CI ≈ 3–5 min under QEMU +
  Watchtower's 5-minute poll) the running SHA matches `git rev-parse HEAD`.
- **Rollback**: push a commit that breaks the healthcheck (e.g. make `/api/v1/health`
  return 503). Watch `journalctl -t health-rollback -f`: within ~4 minutes of the
  container going unhealthy the compose file is pinned to the previous SHA and
  the old image is running. Push the fix; the timer un-pins to `:latest` once it
  sees a build whose SHA differs from the one that failed.

## Day to day

| Task | Command |
| --- | --- |
| Logs | `cd /opt/health && docker compose logs -f api` |
| Which version is live | `docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' health-api-1` |
| Force a pull now | `docker compose pull && docker compose up -d` (or wait for Watchtower) |
| Rollback state | `grep image: /opt/health/docker-compose.yml`, `cat /opt/health/.rolled_back_from` |
| Backups | `ls -la /mnt/storage/health/backups`, `journalctl -t health-backup` |
| Restore | see the output of `/opt/health/restore-test.sh` |
| Offsite copy | `rclone config`, then set `OFFSITE_REMOTE=` in `.env`; `health-offsite.timer` syncs weekly |
| Update deploy files | `git -C /opt/health/src pull && bash /opt/health/src/deploy/bootstrap.sh` |

## Notes and deviations from the spec

- **The api container serves the PWA** (index.html fallback) and Caddy proxies
  everything to it. The spec had Caddy `file_server` a `/srv/www` directory; that
  needs a shared volume between two containers for no gain at one user. Caddy
  still owns TLS, compression and headers.
- **One uvicorn worker**, not two: APScheduler runs in-process and two workers
  would double-fire the nightly rollup and weekly review.
- **Backups run inside the container** (`python -m app.backup`, SQLite's online
  backup API) so the host does not need `sqlite3`. `/api/v1/health` reports the
  age of the newest backup.
- **Rollback un-pins itself.** The spec's script pins `:PREV` and stops; that
  would leave the Pi on the old build forever, because Watchtower only watches
  the tag in the compose file. `rollback.sh` records the failed SHA and, while
  pinned, checks GHCR for a build that is neither the failed one nor the pinned
  one, then returns to `:latest`.
- **`/api/v1/health` returns 503** when the database cannot be opened, so the
  Docker healthcheck (and therefore the rollback) is driven by the app, not just
  by "the process answers".
- **Caddy gets the `.ts.net` certificate from tailscaled** via the socket bind-mounted
  in the compose file. If that ever breaks, the fallback is
  `sudo tailscale serve --bg https:443 http://127.0.0.1:8000` on the host with
  Caddy removed; Tailscale then terminates TLS itself.
- **Port 80/443** are bound on all interfaces by default. Set `BIND_IP` to the
  Pi's Tailscale IP to keep the service invisible on the LAN.
- The Watchtower service bind-mounts `$HOME/.docker/config.json`; bootstrap
  creates it (`{}`) if missing, because Docker would otherwise create a
  *directory* at that path.
- **Gemini key is optional.** Without it the app runs with photo/text logging,
  narratives, `/ask` and physique analysis disabled (loudly: `/health` shows
  it). Add `GEMINI_API_KEY=` to `/opt/health/.env` and `docker compose up -d api`
  to enable them.
- **First OFF import** streams ~1.3 GB and takes a while on a Pi 5; it commits
  every 2000 rows, so it can be interrupted and resumed. Roughly 400k products
  for ES/NL/DE/CZ.
