"""Triage endpoints – turn an anomaly into an incident, (re)index runbooks."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models.incident import Incident
from app.rag.embeddings import Embedder, get_embedder
from app.rag.index import index_runbooks
from app.rag.summarizer import LLMClient, llm_from_settings
from app.schemas import IncidentRead, RunbookIndexResponse
from app.triage.service import triage

router = APIRouter(tags=["triage"])


async def get_embedder_dep() -> Embedder:
    # The first call loads the model (seconds); keep it off the event loop
    return await asyncio.to_thread(get_embedder, get_settings().embedding_model)


def get_llm_dep() -> LLMClient | None:
    return llm_from_settings(get_settings())


@router.post(
    "/anomalies/{anomaly_id}/triage",
    response_model=IncidentRead,
    responses={200: {"description": "Incident already existed"}, 201: {"description": "Created"}},
)
async def triage_anomaly(
    anomaly_id: int,
    response: Response,
    session: AsyncSession = Depends(get_session),
    embedder: Embedder = Depends(get_embedder_dep),
    llm: LLMClient | None = Depends(get_llm_dep),
) -> Incident:
    """Triage one anomaly now and return its incident.

    Idempotent: an anomaly has at most one incident, so calling this again
    returns the existing one (200) instead of making another LLM call.
    """
    try:
        outcome = await triage(session, anomaly_id, embedder=embedder, llm=llm)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK
    incident = await session.scalar(select(Incident).where(Incident.id == outcome.incident_id))
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
