# ── Build stage ────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# ── API server ─────────────────────────────────────────
FROM base AS api
EXPOSE 8000
# Migrate once, then start the workers (each worker running create_all would race)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]

# ── Celery worker ──────────────────────────────────────
FROM base AS worker
CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
