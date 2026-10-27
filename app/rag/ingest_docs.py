"""Chunk and embed runbook documents for RAG retrieval (Phase 4 stub)."""

from __future__ import annotations

from pathlib import Path


def chunk_document(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split a document into overlapping chunks of ``chunk_size`` words."""
    if not 0 <= overlap < chunk_size:
        # overlap >= chunk_size would never advance and loop forever
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            # Last chunk reached the end; another would only repeat its tail
            break
        start += chunk_size - overlap
    return chunks


def load_runbooks(directory: str | Path) -> list[dict[str, str]]:
    """Load markdown runbooks from a directory and return chunks with metadata."""
    dir_path = Path(directory)
    results: list[dict[str, str]] = []
    for md_file in sorted(dir_path.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        for chunk in chunk_document(text):
            results.append({
                "source_doc": md_file.name,
                "text": chunk,
            })
    return results
