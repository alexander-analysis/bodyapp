#!/usr/bin/env bash
# One-time, idempotent Pi setup. Run as the normal Pi user (not root):
#
#   git clone <repo> /opt/health/src        (or: sudo mkdir -p /opt/health && sudo chown $USER /opt/health first)
#   cp /opt/health/src/.env.example /opt/health/.env && chmod 600 /opt/health/.env && nano /opt/health/.env
#   bash /opt/health/src/deploy/bootstrap.sh
#
# Layout after this runs:
#   /opt/health/src/                 git checkout (only needed to re-run bootstrap / update deploy files)
#   /opt/health/docker-compose.yml   runtime copy; rollback.sh may pin the api tag in it
#   /opt/health/Caddyfile, *.sh      runtime copies
#   /opt/health/.env                 secrets + deploy settings, chmod 600, never in git
#   /mnt/storage/health/             health.db, photos/, backups/ (attached storage, not the SD card)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME=/opt/health
ENV_FILE="$RUNTIME/.env"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]' || true; }
env_set_default() { grep -qE "^$1=" "$ENV_FILE" || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }

[[ $EUID -ne 0 ]] || die "run as the Pi user, not root (sudo is used where needed)"
[[ -f "$ENV_FILE" ]] || die "create $ENV_FILE first (template: $SRC/.env.example). Never scripted, never committed."
chmod 600 "$ENV_FILE"

log "packages"
sudo apt-get update -qq
sudo apt-get install -y -qq curl git ca-certificates >/dev/null

log "docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
docker compose version >/dev/null 2>&1 || sg docker -c "docker compose version" >/dev/null || die "docker compose plugin missing"

log "tailscale"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sudo sh
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not up yet; this will print a login URL:"
  sudo tailscale up --ssh
fi
[[ -S /var/run/tailscale/tailscaled.sock ]] || die "tailscaled socket not found; Caddy needs it for the .ts.net certificate"

log "required .env keys"
for k in API_BEARER_TOKEN GHCR_IMAGE TS_HOSTNAME; do   # GEMINI_API_KEY is optional until photo logging is wanted
  [[ -n "$(env_get "$k")" ]] || die "$k is empty in $ENV_FILE"
done
tok="$(env_get API_BEARER_TOKEN)"
[[ ${#tok} -ge 32 ]] || die "API_BEARER_TOKEN must be >= 32 chars (openssl rand -hex 32)"
case "$(env_get TS_HOSTNAME)" in *.ts.net) ;; *) die "TS_HOSTNAME must be the MagicDNS name, e.g. health.<tailnet>.ts.net";; esac

log "derived .env keys (only added if missing)"
env_set_default HEALTH_DATA_DIR /mnt/storage/health
env_set_default PUID "$(id -u)"
env_set_default PGID "$(id -g)"
env_set_default BIND_IP 0.0.0.0
env_set_default DOCKER_CONFIG_DIR "$HOME/.docker"
env_set_default TZ Europe/Madrid
DATA="$(env_get HEALTH_DATA_DIR)"

log "directories"
sudo mkdir -p "$RUNTIME" "$DATA/photos" "$DATA/backups"
sudo chown "$USER":"$USER" "$RUNTIME" "$DATA" "$DATA/photos" "$DATA/backups"
mountpoint -q "$(dirname "$DATA")" || echo "WARNING: $(dirname "$DATA") is not a mount point — is the attached storage mounted? SQLite on the SD card wears it out."
mkdir -p "$HOME/.docker"
[[ -f "$HOME/.docker/config.json" ]] || echo '{}' > "$HOME/.docker/config.json"   # bind-mounted into watchtower; must exist as a file

log "runtime files"
if [[ -f "$RUNTIME/docker-compose.yml" ]] && grep -qE 'image: .*GHCR_IMAGE[^:]*:[0-9a-f]{40}' "$RUNTIME/docker-compose.yml"; then
  echo "WARNING: existing compose is pinned to a rollback SHA; keeping it. Delete it and re-run to reset to :latest."
else
  install -m 644 "$SRC/deploy/docker-compose.yml" "$RUNTIME/docker-compose.yml"
fi
install -m 644 "$SRC/deploy/Caddyfile" "$RUNTIME/Caddyfile"
for f in rollback backup offsite-sync disk-check restore-test; do
  install -m 755 "$SRC/deploy/$f.sh" "$RUNTIME/$f.sh"
done

log "systemd timers (system scope, run as $USER)"
for u in "$SRC"/deploy/systemd/health-*; do
  sed "s/__USER__/$USER/g" "$u" | sudo tee "/etc/systemd/system/$(basename "$u")" >/dev/null
done
sudo systemctl daemon-reload

log "port check"
if ss -ltn 2>/dev/null | grep -qE ':(80|443)[[:space:]]'; then
  echo "WARNING: something already listens on :80 or :443. Caddy will fail to start until BIND_IP in .env points at the Tailscale IP ($(tailscale ip -4 2>/dev/null || echo '?')) or the other service moves."
fi

log "pull + start"
run_docker() {
  if id -nG "$USER" | grep -qw docker && docker ps >/dev/null 2>&1; then "$@"; else sg docker -c "$*"; fi
}
cd "$RUNTIME"
run_docker docker compose pull
run_docker docker compose up -d

sudo systemctl enable --now health-rollback.timer health-backup.timer health-disk.timer health-offsite.timer

log "waiting for the api healthcheck"
for _ in $(seq 1 30); do
  st=$(run_docker docker inspect -f '{{.State.Health.Status}}' health-api-1 2>/dev/null || echo starting)
  [[ "$st" == "healthy" ]] && break
  sleep 3
done
echo "api: ${st:-unknown}"
echo
echo "Deployed. Open https://$(env_get TS_HOSTNAME) from a device on the tailnet."
echo "Then: /opt/health/backup.sh && /opt/health/restore-test.sh   (milestone 2 requires a verified restore)"
echo "Log out and back in once so your shell picks up the docker group."
