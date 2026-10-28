# ── Dashboard build ────────────────────────────────────
FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ── Python base ────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies. CPU-only torch first: sentence-transformers would
# otherwise pull the CUDA build, several GB a VPS has no use for.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir .

# Copy application code
COPY app/ ./app/
COPY agent/ ./agent/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Run as an unprivileged user; HOME holds the Hugging Face model cache
RUN useradd --create-home --uid 1000 app
USER app

# ── API server (also serves the built dashboard) ───────
FROM base AS api
COPY --from=dashboard /dashboard/dist ./dashboard/dist
EXPOSE 8000
# uvicorn reads its worker count from WEB_CONCURRENCY. Proxy headers are
# trusted from anyone because only the reverse proxy can reach this port.
ENV WEB_CONCURRENCY=2
# Migrate once, then start the workers (each worker running create_all would race)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]

# ── Celery worker ──────────────────────────────────────
FROM base AS worker
CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
