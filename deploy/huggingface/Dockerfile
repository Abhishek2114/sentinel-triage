# Hugging Face Space (Docker SDK): the whole app in one container on port 7860.
# This file and README.md are all the Space holds; the code is cloned from
# GitHub at build time. Walkthrough: docs/DEPLOY_FREE.md.
#
# Postgres (Neon) and Redis (Upstash) are external and come in as Space
# secrets. Celery isn't used: the API runs detection itself after new logs
# arrive, and the demo button runs detection and triage in the request.

# ── Source ─────────────────────────────────────────────
FROM alpine:3.20 AS source
RUN apk add --no-cache git
ARG REPO=https://github.com/Abhishek2114/sentinel-triage.git
# A branch, tag or full commit SHA
ARG REF=main
# The repo's ref list: when a commit lands, this file changes and the clone
# below re-runs instead of reusing a cached, older checkout
ADD ${REPO}/info/refs?service=git-upload-pack /tmp/refs
RUN git init -q /src && cd /src && \
    git fetch -q --depth 1 "$REPO" "$REF" && \
    git checkout -q FETCH_HEAD && \
    rm -rf .git

# ── Dashboard build ────────────────────────────────────
FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY --from=source /src/dashboard/package.json /src/dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=source /src/dashboard/ ./
RUN npm run build

# ── App ────────────────────────────────────────────────
FROM python:3.11-slim

# The app is copied in, not pip-installed, so console scripts (alembic)
# need /app on the import path to find the `app` package
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# CPU-only torch first: sentence-transformers would otherwise pull CUDA
COPY --from=source /src/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir .

COPY --from=source /src/app ./app
COPY --from=source /src/agent ./agent
COPY --from=source /src/scripts ./scripts
COPY --from=source /src/data ./data
COPY --from=source /src/migrations ./migrations
COPY --from=source /src/alembic.ini ./
COPY --from=dashboard /dashboard/dist ./dashboard/dist

# Spaces run the container as uid 1000
RUN useradd --create-home --uid 1000 user
USER user

# Defaults for a public demo; DATABASE_URL, REDIS_URL and API_KEYS come from
# the Space's secrets. Override any of these as Space variables.
ENV ENVIRONMENT=production \
    AUTO_CREATE_TABLES=false \
    DEMO_MODE=true \
    EMBEDDING_MODEL=hashing \
    INPROCESS_SCHEDULER=true \
    FRAME_ANCESTORS='["https://huggingface.co"]' \
    LLM_MAX_CALLS_PER_DAY=100 \
    WEB_CONCURRENCY=1

EXPOSE 7860
# Migrate, then serve. One process: the in-process scheduler and the live
# alert relay each run once per process.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 7860 --proxy-headers --forwarded-allow-ips='*'"]
