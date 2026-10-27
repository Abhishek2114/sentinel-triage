"""Find the runbook sections most relevant to an anomaly's evidence.

``retrieve`` runs the similarity search in Postgres (pgvector cosine distance);
``rank_in_memory`` does the same ranking in numpy for the offline evaluation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import RunbookChunkRow
from app.rag.embeddings import Embedder
from app.rag.ingest_docs import RunbookChunk


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    source_doc: str
    section: str
    text: str
    score: float  # cosine similarity, higher = more relevant


def rank_runbooks(chunks: Sequence[RetrievedChunk]) -> list[str]:
    """Runbooks ordered by their best-matching section."""
    best: dict[str, float] = {}
    for c in chunks:
        best[c.source_doc] = max(best.get(c.source_doc, -1.0), c.score)
    return sorted(best, key=lambda doc: best[doc], reverse=True)


def rank_in_memory(
    query_vector: np.ndarray,
    chunks: Sequence[RunbookChunk],
    chunk_vectors: np.ndarray,
    top_k: int = 4,
) -> list[RetrievedChunk]:
    """Top-k chunks by cosine similarity (vectors are already normalised)."""
    scores = chunk_vectors @ query_vector
    order = np.argsort(-scores, kind="stable")[:top_k]
    return [
        RetrievedChunk(chunks[i].source_doc, chunks[i].section, chunks[i].text, float(scores[i]))
        for i in order
    ]


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    top_k: int = 4,
) -> list[RetrievedChunk]:
    """Top-k runbook chunks for ``query`` from pgvector.

    Only chunks embedded by the same embedder are compared: vectors from
    different models live in different spaces.
    """
    if not query.strip():
        return []
    # Model inference is CPU-bound; keep it off the event loop
    vector = (await asyncio.to_thread(embedder.embed, [query]))[0]
    # pgvector has no type hints; pin the comparator's result type for mypy
    distance: ColumnElement[float] = RunbookChunkRow.embedding.cosine_distance(vector.tolist())
    rows = await session.execute(
        select(RunbookChunkRow.source_doc, RunbookChunkRow.section, RunbookChunkRow.text, distance)
        .where(RunbookChunkRow.embedding_model == embedder.name)
        .order_by(distance)
        .limit(top_k)
    )
    return [RetrievedChunk(doc, section, text, 1.0 - float(d)) for doc, section, text, d in rows]


async def runbook_context(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    max_docs: int = 2,
    top_k: int = 4,
) -> list[RetrievedChunk]:
    """Every section of the best-matching runbooks, best runbook first.

    Retrieval picks the runbooks; the LLM then sees them whole, so the
    Remediation section is there even when only the Symptoms section matched.
    """
    hits = await retrieve(session, embedder, query, top_k)
    docs = rank_runbooks(hits)[:max_docs]
    if not docs:
        return []
    best = {doc: max(h.score for h in hits if h.source_doc == doc) for doc in docs}
    rows = await session.execute(
        select(
            RunbookChunkRow.source_doc,
            RunbookChunkRow.section,
            RunbookChunkRow.text,
        )
        .where(
            RunbookChunkRow.embedding_model == embedder.name,
            RunbookChunkRow.source_doc.in_(docs),
        )
        .order_by(RunbookChunkRow.chunk_index)
    )
    chunks = [RetrievedChunk(doc, section, text, best[doc]) for doc, section, text in rows]
    return sorted(chunks, key=lambda c: docs.index(c.source_doc))  # stable: keeps section order
