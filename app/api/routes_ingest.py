"""Ingest endpoint – upload log lines or point to a file."""

import asyncio
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.metrics import record_ingest
from app.core.security import require_key
from app.ingest.pipeline import prepare_lines
from app.ingest.reader import read_file_lines
from app.models.event import LogEvent
from app.schemas import IngestRequest, IngestResponse

router = APIRouter(tags=["ingest"])
log = get_logger(__name__)

_BATCH_SIZE = 1000


def _resolve_ingest_path(file_path: str) -> Path:
    """Resolve a requested path, refusing anything outside the ingest root.

    Without this, a caller could ingest /etc/passwd or .env and then read it
    back line by line through GET /events.
    """
    root = Path(get_settings().ingest_root).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail=f"file_path must be inside {root}")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


@router.post(
    "/ingest", response_model=IngestResponse, dependencies=[Depends(require_key)]
)
async def ingest_logs(
    body: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Parse and store log lines from raw input or a file path."""
    if body.lines is not None:
        lines = body.lines
    elif body.file_path:
        path = _resolve_ingest_path(body.file_path)
        lines = [line async for line in read_file_lines(path)]
    else:
        raise HTTPException(status_code=422, detail="Provide either 'lines' or 'file_path'")

    settings = get_settings()
    # Parsing, redaction and templating are CPU work: run them in a thread so
    # one large request doesn't stall every other request on this process
    prepared = await asyncio.to_thread(
        prepare_lines,
        lines,
        max_line_chars=settings.max_line_chars,
        max_clock_skew=timedelta(seconds=settings.max_clock_skew_seconds),
        max_multiline_lines=settings.max_multiline_lines,
    )
    for start in range(0, len(prepared.rows), _BATCH_SIZE):
        await session.execute(insert(LogEvent), prepared.rows[start : start + _BATCH_SIZE])
    ingested, errors = len(prepared.rows), prepared.errors
    record_ingest(ingested, errors, prepared.stats)

    # Commit before responding, so "ingested" is only reported once it is durable
    await session.commit()

    log.info("ingest_complete", ingested=ingested, errors=errors, **dict(prepared.stats))
    return IngestResponse(ingested=ingested, errors=errors)
