"""Unit tests for runbook chunking."""

import pytest

from app.rag.ingest_docs import chunk_document


def test_short_doc_is_one_chunk():
    # Used to emit a second chunk that only repeated the tail of the first
    assert len(chunk_document(" ".join(["w"] * 500), chunk_size=512, overlap=64)) == 1


def test_chunks_overlap():
    words = [str(i) for i in range(20)]
    chunks = chunk_document(" ".join(words), chunk_size=10, overlap=2)
    assert chunks[0].split()[-2:] == chunks[1].split()[:2]
    assert chunks[-1].split()[-1] == "19"


def test_overlap_not_smaller_than_size_is_rejected():
    # Used to loop forever
    with pytest.raises(ValueError):
        chunk_document("a b c", chunk_size=5, overlap=5)
