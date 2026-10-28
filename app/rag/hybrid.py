"""Lexical retrieval: BM25 over stemmed words, TF-IDF over character n-grams,
fused with reciprocal-rank fusion (RRF).

The two lexical views fail differently. BM25 matches whole (stemmed) words,
so it is sharp on exact vocabulary ("SQLSTATE", "OOMKilled") but blind to
variants it can't stem ("conn" vs "connection", "timedout"). Character
3-5-grams match those partial overlaps but blur rare exact terms. RRF combines
ranks rather than scores, so neither needs calibrating against the other.

The corpora here are small (tens to hundreds of documents), so indexes are
built in memory in milliseconds. ``scripts/eval_retrieval.py`` compares every
variant, including the dense embedders, on a held-out query set.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.embeddings import _PLACEHOLDERS, _TOKEN, _stem

RRF_K = 60  # the constant from Cormack et al. (2009); not tuned here
_PLACEHOLDER_TAG = re.compile(r"<\w+>|\[REDACTED:\w+\]")


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _PLACEHOLDERS]


def _clean(text: str) -> str:
    return _PLACEHOLDER_TAG.sub(" ", text).lower()


class BM25:
    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in docs]
        self.lengths = np.array([sum(c.values()) for c in self.docs], dtype=np.float64)
        self.avg_len = float(self.lengths.mean()) if len(docs) else 0.0
        df: Counter[str] = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(len(self.docs))
        if not self.docs:
            return out
        norm = self.k1 * (1 - self.b + self.b * self.lengths / max(self.avg_len, 1e-9))
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            tf = np.array([c.get(term, 0) for c in self.docs], dtype=np.float64)
            out += idf * tf * (self.k1 + 1) / (tf + norm)
        return out


class CharNgrams:
    def __init__(self, docs: Sequence[str], ngram_range: tuple[int, int] = (3, 5)) -> None:
        self.empty = not any(d.strip() for d in docs)
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=ngram_range, sublinear_tf=True, preprocessor=_clean
        )
        self.matrix = None if self.empty else self.vectorizer.fit_transform(docs)
        self.n = len(docs)

    def scores(self, query: str) -> np.ndarray:
        if self.matrix is None:
            return np.zeros(self.n)
        q = self.vectorizer.transform([query])
        return np.asarray((self.matrix @ q.T).toarray()).ravel()  # rows are L2-normalised


def ranking(scores: np.ndarray) -> list[int]:
    """Document indices with a positive score, best first (ties by index)."""
    order = np.argsort(-scores, kind="stable")
    return [int(i) for i in order if scores[i] > 0]


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    fused: dict[int, float] = {}
    for ranked in rankings:
        for rank, doc in enumerate(ranked):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))


class HybridIndex:
    """BM25 + character n-grams over one corpus, queried with RRF."""

    def __init__(self, docs: Sequence[str]) -> None:
        self.bm25 = BM25(docs)
        self.chars = CharNgrams(docs)

    def rankings(self, query: str) -> dict[str, list[int]]:
        return {
            "bm25": ranking(self.bm25.scores(query)),
            "char": ranking(self.chars.scores(query)),
        }

    def search(
        self, query: str, top_k: int = 5, extra: Sequence[Sequence[int]] = ()
    ) -> list[tuple[int, float]]:
        """Fused (doc index, RRF score) pairs; ``extra`` adds e.g. a dense ranking."""
        r = self.rankings(query)
        return rrf([r["bm25"], r["char"], *extra])[:top_k]
