"""Rank fusion and the final score."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

RRF_K = 60

# Final blend when the cross-encoder answered (weights for ce, graph, fused).
# Fit on your eval set with `python -m eval.rag_eval --fit`, then set RAG_W_* in .env.
W_RERANK = float(os.environ.get("RAG_W_RERANK", "0.70"))
W_GRAPH = float(os.environ.get("RAG_W_GRAPH", "0.15"))
W_FUSED = float(os.environ.get("RAG_W_FUSED", "0.15"))
# Without a reranker.
W_FUSED_ONLY = 0.6
W_GRAPH_ONLY = 0.4


def rrf(rankings: Sequence[Sequence[int]], weights: Sequence[float] | None = None, k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion: sum of w / (k + rank) over every list an item appears in."""
    scores: dict[int, float] = {}
    for index, ranking in enumerate(rankings):
        weight = weights[index] if weights else 1.0
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
    return scores


def unit_scale(scores: dict[int, float]) -> dict[int, float]:
    """Min-max to [0, 1] so different signals can be blended."""
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    if high - low < 1e-12:
        return {key: 1.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}


def top(scores: dict[int, float], n: int, exclude: Iterable[int] = ()) -> list[int]:
    skip = set(exclude)
    return [key for key, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if key not in skip][:n]


def blend(
    ids: Sequence[int],
    fused: dict[int, float],
    graph: dict[int, float],
    rerank: dict[int, float] | None,
    weights: tuple[float, float, float] | None = None,
) -> dict[int, float]:
    f = unit_scale({i: fused.get(i, 0.0) for i in ids})
    g = unit_scale({i: graph.get(i, 0.0) for i in ids})
    if rerank:
        wr, wg, wf = weights or (W_RERANK, W_GRAPH, W_FUSED)
        return {i: wr * rerank.get(i, 0.0) + wg * g.get(i, 0.0) + wf * f.get(i, 0.0) for i in ids}
    return {i: W_FUSED_ONLY * f.get(i, 0.0) + W_GRAPH_ONLY * g.get(i, 0.0) for i in ids}
