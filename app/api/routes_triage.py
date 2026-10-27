"""Triage endpoints – turn an anomaly into an incident, (re)index runbooks."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_embedder_dep, get_llm_dep, get_redis_factory
from app.core.config import get_settings
from app.core.db import get_session
from app.models.incident import Incident
from app.rag.embeddings import Embedder
from app.rag.index import index_runbooks
from app.rag.summarizer import LLMClient
from app.realtime import RedisFactory, publish_incident
from app.schemas import IncidentRead, RunbookIndexResponse
from app.triage.service import triage

router = APIRouter(tags=["triage"])


@router.post(
    "/anomalies/{anomaly_id}/triage",
    response_model=IncidentRead,
    responses={
        200: {"description": "Joined an open incident, or was already triaged"},
        201: {"description": "Opened a new incident"},
    },
)
async def triage_anomaly(
    anomaly_id: int,
    response: Response,
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder_dep),
    llm: LLMClient | None = Depends(get_llm_dep),
    redis: RedisFactory = Depends(get_redis_factory),
) -> Incident:
    """Triage one anomaly now and return its incident.

    If an open incident on the same service is close in time, the anomaly
    joins it (no LLM call). Idempotent: triaging again returns the same incident.
    """
    try:
        outcome = await triage(
            session,
            anomaly_id,
            embedder=embedder,
            llm=llm,
            group_gap=timedelta(minutes=get_settings().incident_group_minutes),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if outcome.event is not None:
        await publish_incident(session, redis, outcome.incident_id, outcome.event)
    response.status_code = status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK
    incident = await session.scalar(
        select(Incident)
        .where(Incident.id == outcome.incident_id)
        .execution_options(populate_existing=True)  # grouping updated it with UPDATE
    )
    assert incident is not None
    return incident


@router.post("/runbooks/index", response_model=RunbookIndexResponse)
async def reindex_runbooks(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder_dep),
) -> RunbookIndexResponse:
    """Re-embed every runbook under RUNBOOKS_DIR (the worker also does this on start)."""
    chunks = await index_runbooks(session, embedder, get_settings().runbooks_dir)
    return RunbookIndexResponse(chunks=chunks, embedding_model=embedder.name)
