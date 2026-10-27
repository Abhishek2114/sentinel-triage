"""Ingest endpoint – upload log lines or point to a file."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.ingest.parser import parse_line
from app.ingest.reader import read_file_lines
from app.ingest.templates import extract_template, template_id
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


@router.post("/ingest", response_model=IngestResponse)
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

    ingested = 0
    errors = 0
    batch: list[dict[str, object]] = []

    for raw_line in lines:
        if not raw_line.strip():
            continue
        parsed = parse_line(raw_line)
        if parsed is None:
            errors += 1
            continue

        parsed.template_id = template_id(extract_template(parsed.message))
        batch.append(parsed.model_dump())
        ingested += 1

        if len(batch) >= _BATCH_SIZE:
            await session.execute(insert(LogEvent), batch)
            batch.clear()

    if batch:
        await session.execute(insert(LogEvent), batch)

    # Commit before responding, so "ingested" is only reported once it is durable
    await session.commit()

    log.info("ingest_complete", ingested=ingested, errors=errors)
    return IngestResponse(ingested=ingested, errors=errors)
