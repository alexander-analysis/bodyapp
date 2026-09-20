# syntax=docker/dockerfile:1
# Multi-stage: the frontend builds on the native CI platform (its output is
# platform-independent); only the Python stage is built for linux/arm64.

FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# curl: the compose healthcheck (spec 13.3).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /src/dist ./www

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION \
    STATIC_DIR=/srv/www \
    DATA_DIR=/data \
    DB_PATH=/data/health.db \
    PHOTO_DIR=/data/photos \
    BACKUP_DIR=/data/backups
LABEL org.opencontainers.image.revision=$APP_VERSION \
      org.opencontainers.image.title="health-platform" \
      org.opencontainers.image.description="Single-user adaptive nutrition and training tracker"

WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1
# One worker: APScheduler runs in-process and must not double-fire (spec 3 allows up to 2).
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
