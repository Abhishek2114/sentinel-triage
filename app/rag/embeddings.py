"""Text embedders for runbook retrieval.

Two implementations behind one small interface:

- ``SentenceTransformerEmbedder`` – the real one (all-MiniLM-L6-v2 by default).
- ``HashingEmbedder`` – dependency-free bag-of-words hashing. Used by tests, and
  as the fallback when the model can't be loaded (offline, slim image), so
  triage still finds runbooks by keyword overlap instead of failing.

Every stored vector records which embedder produced it; retrieval only compares
vectors from the same one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

# Fixed by the pgvector column; all-MiniLM-L6-v2 produces 384 dimensions
EMBEDDING_DIM = 384
HASHING = "hashing"

_TOKEN = re.compile(r"[a-z][a-z0-9_]+")
# Placeholders left by log templating carry no meaning for retrieval
_PLACEHOLDERS = {"num", "ip", "uuid", "hex", "path", "ts"}


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return one L2-normalised row of ``EMBEDDING_DIM`` floats per text."""
        ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.asarray(vectors / np.where(norms == 0, 1.0, norms), dtype=np.float32)


# Longest first. Crude, but "failing", "failed" and "failures" all become "fail"
_SUFFIXES = ("ations", "ation", "ions", "ures", "ing", "ion", "ure", "ed", "s")


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


class HashingEmbedder:
    """Signed feature hashing of stemmed word unigrams and bigrams."""

    name = HASHING

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _PLACEHOLDERS]
            for feature in tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]:
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                h = int.from_bytes(digest, "little")
                out[row, h % EMBEDDING_DIM] += 1.0 if h >> 63 else -1.0
        return _normalise(out)


class SentenceTransformerEmbedder:
    """Wraps a sentence-transformers model (loaded once per process)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model: Any = SentenceTransformer(model_name)
        dim = self._model.get_sentence_embedding_dimension()
        if dim != EMBEDDING_DIM:
            raise ValueError(
                f"{model_name} produces {dim}-d vectors; the runbook_chunks column is "
                f"{EMBEDDING_DIM}-d. Pick a {EMBEDDING_DIM}-d model or add a migration."
            )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(vectors, dtype=np.float32)


@lru_cache
def get_embedder(model_name: str) -> Embedder:
    """The configured embedder, falling back to hashing if the model won't load."""
    if model_name == HASHING:
        return HashingEmbedder()
    try:
        return SentenceTransformerEmbedder(model_name)
    except (ImportError, OSError) as exc:  # not installed, or can't download
        log.warning("embedder_fallback", model=model_name, error=str(exc), using=HASHING)
        return HashingEmbedder()
