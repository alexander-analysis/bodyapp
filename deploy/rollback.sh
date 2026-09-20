#!/usr/bin/env bash
# /opt/health/rollback.sh — run by health-rollback.timer every 2 minutes.
#
# healthy   -> record the running SHA as last-good. If the compose file is pinned
#              to a rollback SHA, check GHCR for a build newer than the one that
#              failed and, if there is one, un-pin back to :latest.
# unhealthy -> pin the api image to the last-good SHA and recreate the container.
#              Pinning (not just re-tagging) is what stops Watchtower from pulling
#              the broken :latest again five minutes later.
set -euo pipefail
cd /opt/health

COMPOSE=docker-compose.yml
GOOD=.last_good_sha
BAD=.rolled_back_from
CONTAINER=health-api-1
IMAGE="$(grep -E '^GHCR_IMAGE=' .env | cut -d= -f2- | tr -d '[:space:]')"
LABEL='{{index .Config.Labels "org.opencontainers.image.revision"}}'

status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
running=$(docker inspect -f "$LABEL" "$CONTAINER" 2>/dev/null || echo "")
pinned=$(grep -E 'image: .*GHCR_IMAGE' "$COMPOSE" | sed -E 's/.*:([^[:space:]]+)[[:space:]]*$/\1/')

pin() {  # pin <tag>
  sed -i -E "s#(image: .*GHCR_IMAGE[^:]*):[^[:space:]]+#\1:$1#" "$COMPOSE"
}

case "$status" in
  healthy)
    if [[ -n "$running" && "$running" != "dev" ]]; then
      echo "$running" > "$GOOD"
    fi
    if [[ "$pinned" != "latest" ]]; then
      # Rolled-back state. Is there a new build that is not the one that failed?
      docker pull -q "$IMAGE:latest" >/dev/null 2>&1 || exit 0
      latest=$(docker inspect -f "$LABEL" "$IMAGE:latest" 2>/dev/null || echo "")
      bad=$(cat "$BAD" 2>/dev/null || echo "")
      if [[ -n "$latest" && "$latest" != "$bad" && "$latest" != "$pinned" ]]; then
        pin latest
        docker compose up -d api
        logger -t health-rollback "new build ${latest} found; un-pinned ${pinned} -> latest"
      fi
    fi
    ;;
  unhealthy)
    if [[ "$pinned" != "latest" ]]; then
      logger -t health-rollback "still unhealthy on pinned ${pinned}; needs a human"
      exit 0
    fi
    prev=$(cat "$GOOD" 2>/dev/null || echo "")
    if [[ -z "$prev" || "$prev" == "$running" ]]; then
      logger -t health-rollback "unhealthy on ${running:-unknown} with no different last-good SHA; not rolling back"
      exit 0
    fi
    echo "${running}" > "$BAD"
    pin "$prev"
    docker compose up -d api
    logger -t health-rollback "rolled back ${running} -> ${prev}"
    ;;
  *)
    exit 0
    ;;
esac
