"""Find the runbook sections most relevant to an anomaly's evidence.

Five retrieval modes, compared in ``scripts/eval_retrieval.py``:
``dense`` (pgvector cosine distance), ``bm25``, ``char`` (character n-grams),
``hybrid`` (BM25 + char n-grams, RRF) and ``hybrid_dense`` (all three, RRF).
``rank_chunks`` is the shared ranking; ``retrieve`` feeds it from Postgres and
the offline evaluation feeds it from memory.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import numpy as np
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import RunbookChunkRow
from app.rag.embeddings import Embedder
from app.rag.hybrid import HybridIndex, rrf
from app.rag.ingest_docs import RunbookChunk

RetrievalMode = Literal["dense", "bm25", "char", "hybrid", "hybrid_dense"]
RETRIEVAL_MODES: tuple[RetrievalMode, ...] = ("dense", "bm25", "char", "hybrid", "hybrid_dense")


def resolve_mode(configured: str, embedder: Embedder) -> RetrievalMode:
    """``auto`` → BM25, what the retrieval ablation's dev splits selected.

    Triage queries are log templates. On a dev split of those (seeds 1-20)
    BM25 found the right runbook 67/67 times with either embedder; MiniLM
    alone 50/67 and BM25 + char n-grams (RRF) 66/67. See README "Retrieval".
    """
    if configured == "auto":
        return "bm25"
    assert configured in RETRIEVAL_MODES, configured
    return configured


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    source_doc: str
    section: str
    text: str
    score: float  # cosine similarity (dense) or RRF score; higher = more relevant


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


@lru_cache(maxsize=16)
def _hybrid_index(texts: tuple[str, ...]) -> HybridIndex:
    return HybridIndex(texts)


def rank_chunks(
    mode: RetrievalMode,
    query: str,
    texts: Sequence[str],
    dense_scores: np.ndarray | None = None,
    top_k: int = 4,
) -> list[tuple[int, float]]:
    """(chunk index, score) pairs, best first, for one retrieval mode."""
    if mode in ("dense", "hybrid_dense") and dense_scores is None:
        raise ValueError(f"mode {mode!r} needs dense scores")
    dense_order: list[int] = []
    if dense_scores is not None:
        dense_order = [int(i) for i in np.argsort(-dense_scores, kind="stable")]
    if mode == "dense":
        assert dense_scores is not None
        return [(i, float(dense_scores[i])) for i in dense_order[:top_k]]
    lists = _hybrid_index(tuple(texts)).rankings(query)
    if mode in ("bm25", "char"):
        return [(i, 1.0 / (rank + 1)) for rank, i in enumerate(lists[mode][:top_k])]
    extra = [dense_order] if mode == "hybrid_dense" else []
    return rrf([lists["bm25"], lists["char"], *extra])[:top_k]


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    top_k: int = 4,
    mode: RetrievalMode = "dense",
) -> list[RetrievedChunk]:
    """Top-k runbook chunks for ``query``.

    Only chunks embedded by the same embedder are compared: vectors from
    different models live in different spaces. Lexical modes rank the same
    rows in memory (a few hundred sections at most).
    """
    if not query.strip():
        return []
    if mode != "dense":
        return await _retrieve_lexical(session, embedder, query, top_k, mode)
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


async def _retrieve_lexical(
    session: AsyncSession, embedder: Embedder, query: str, top_k: int, mode: RetrievalMode
) -> list[RetrievedChunk]:
    columns: list[Any] = [
        RunbookChunkRow.source_doc,
        RunbookChunkRow.section,
        RunbookChunkRow.text,
    ]
    dense_scores = None
    if mode == "hybrid_dense":
        vector = (await asyncio.to_thread(embedder.embed, [query]))[0]
        distance: ColumnElement[float] = RunbookChunkRow.embedding.cosine_distance(
            vector.tolist()
        )
        columns.append(distance)
    rows = (
        await session.execute(
            select(*columns)
            .where(RunbookChunkRow.embedding_model == embedder.name)
            .order_by(RunbookChunkRow.source_doc, RunbookChunkRow.chunk_index)
        )
    ).all()
    if not rows:
        return []
    if mode == "hybrid_dense":
        dense_scores = np.array([1.0 - float(r[3]) for r in rows])
    texts = [r[2] for r in rows]
    ranked = await asyncio.to_thread(rank_chunks, mode, query, texts, dense_scores, top_k)
    return [RetrievedChunk(rows[i][0], rows[i][1], rows[i][2], score) for i, score in ranked]


async def runbook_context(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    max_docs: int = 2,
    top_k: int = 4,
    mode: RetrievalMode = "dense",
) -> list[RetrievedChunk]:
    """Every section of the best-matching runbooks, best runbook first.

    Retrieval picks the runbooks; the LLM then sees them whole, so the
    Remediation section is there even when only the Symptoms section matched.
    """
    hits = await retrieve(session, embedder, query, top_k, mode)
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
