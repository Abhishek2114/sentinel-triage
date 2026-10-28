#!/usr/bin/env python3
"""Retrieval ablation: which runbook ranking finds the right runbook?

Compares every retrieval mode (dense, bm25, char, hybrid = BM25 + char n-grams
with RRF, hybrid_dense) on hand-written paraphrased queries:

- ``eval/queries/retrieval_dev.jsonl`` (20 queries) may be looked at to pick
  the production default;
- ``eval/queries/retrieval_test.jsonl`` (50 queries) is held out: it was
  written before any mode was run and is not used to choose anything.

Caveat, stated in the README too: one person wrote both the runbooks and the
queries, so the queries can share vocabulary with the runbooks in ways real
on-call messages would not. The numbers compare modes against each other on
the same queries; they are not an estimate of accuracy in production.

    python scripts/eval_retrieval.py                 # hashing embedder (offline)
    python scripts/eval_retrieval.py --embedder all-MiniLM-L6-v2
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.eval.harness import RunbookIndex  # noqa: E402
from app.rag.embeddings import HASHING, get_embedder  # noqa: E402
from app.rag.ingest_docs import load_runbooks  # noqa: E402
from app.rag.retriever import RETRIEVAL_MODES  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(centre - half, 3), round(centre + half, 3))


def load(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(index: RunbookIndex, queries: list[dict[str, str]]) -> dict[str, Any]:
    ranks: list[int | None] = []
    for q in queries:
        docs = index.ranked_runbooks(q["query"], top_k=8)
        ranks.append(docs.index(q["runbook"]) + 1 if q["runbook"] in docs else None)
    n = len(ranks)
    hit1 = sum(r == 1 for r in ranks)
    return {
        "n": n,
        "hit@1": round(hit1 / n, 3),
        "hit@1_ci95": wilson(hit1, n),
        "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        "correct": [r == 1 for r in ranks],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embedder", default=HASHING)
    parser.add_argument("--runbooks-dir", type=Path, default=Path("data/runbooks"))
    parser.add_argument("--queries", type=Path, default=Path("eval/queries"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    embedder = get_embedder(args.embedder)
    if embedder.name != args.embedder:
        parser.error(f"Could not load embedder {args.embedder!r}")
    chunks = load_runbooks(args.runbooks_dir)
    sets = {
        split: load(args.queries / f"retrieval_{split}.jsonl") for split in ("dev", "test")
    }

    results: dict[str, Any] = {
        "embedder": embedder.name,
        "runbooks": len({c.source_doc for c in chunks}),
    }
    for split, queries in sets.items():
        results[split] = {}
        for mode in RETRIEVAL_MODES:
            index = RunbookIndex(embedder, chunks, mode=mode)
            results[split][mode] = score(index, queries)

    # Paired comparison on the held-out split: where do two modes disagree?
    test = results["test"]
    pairs = {}
    compared = (
        ("hybrid", "dense"),
        ("hybrid", "bm25"),
        ("hybrid", "char"),
        ("hybrid_dense", "hybrid"),
    )
    for a, b in compared:
        ca, cb = test[a]["correct"], test[b]["correct"]
        pairs[f"{a}_vs_{b}"] = {
            "only_first_right": sum(x and not y for x, y in zip(ca, cb, strict=True)),
            "only_second_right": sum(y and not x for x, y in zip(ca, cb, strict=True)),
        }
    results["test_paired"] = pairs

    print(f"embedder={embedder.name}, {results['runbooks']} runbooks, {len(chunks)} sections\n")
    print("| Mode | dev hit@1 | test hit@1 (95% CI) | test hit@3 | test MRR |")
    print("|---|---|---|---|---|")
    for mode in RETRIEVAL_MODES:
        d, t = results["dev"][mode], results["test"][mode]
        lo, hi = t["hit@1_ci95"]
        print(
            f"| {mode} | {d['hit@1']:.2f} | {t['hit@1']:.2f} ({lo:.2f}-{hi:.2f}) | "
            f"{t['hit@3']:.2f} | {t['mrr']:.2f} |"
        )
    print("\nPaired, held-out (queries only one of the two gets right at rank 1):")
    for name, p in pairs.items():
        print(f"  {name}: {p['only_first_right']} vs {p['only_second_right']}")

    output = args.output or Path(f"eval/results/retrieval_{embedder.name.replace('/', '_')}.json")
    for split in ("dev", "test"):
        for mode in RETRIEVAL_MODES:
            results[split][mode].pop("correct")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nSaved {output}")


if __name__ == "__main__":
    main()
