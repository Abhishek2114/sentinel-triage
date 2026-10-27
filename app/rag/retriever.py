"""RAG retriever – find relevant runbook chunks for an anomaly (Phase 4 stub)."""

from __future__ import annotations


async def retrieve_relevant_chunks(
    query: str,
    top_k: int = 5,
) -> list[dict[str, str]]:
    """Placeholder – will use pgvector similarity search in Phase 4."""
    raise NotImplementedError("RAG retriever is not yet implemented (Phase 4).")
