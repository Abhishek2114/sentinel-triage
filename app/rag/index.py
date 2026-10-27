"""Embed runbooks and store them in pgvector."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.runbook import RunbookChunkRow
from app.rag.embeddings import Embedder
from app.rag.ingest_docs import load_runbooks

log = get_logger(__name__)


async def index_runbooks(session: AsyncSession, embedder: Embedder, directory: str | Path) -> int:
    """Replace this embedder's chunks with freshly embedded runbooks.

    Delete-and-insert in one transaction: the corpus is small, and it means a
    renamed or deleted runbook can't linger in search results.
    """
    chunks = load_runbooks(directory)
    vectors = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])

    await session.execute(
        delete(RunbookChunkRow).where(RunbookChunkRow.embedding_model == embedder.name)
    )
    if chunks:
        await session.execute(
            insert(RunbookChunkRow),
            [
                {
                    "source_doc": c.source_doc,
                    "chunk_index": c.chunk_index,
                    "section": c.section,
                    "text": c.text,
                    "embedding_model": embedder.name,
                    "embedding": vector.tolist(),
                }
                for c, vector in zip(chunks, vectors, strict=True)
            ],
        )
    await session.commit()
    log.info("runbooks_indexed", chunks=len(chunks), embedder=embedder.name)
    return len(chunks)


async def count_chunks(session: AsyncSession, embedder_name: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(RunbookChunkRow)
        .where(RunbookChunkRow.embedding_model == embedder_name)
    )
    return int(result.scalar_one())
