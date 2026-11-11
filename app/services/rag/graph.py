"""The passage graph and what we do with it (numpy only).

Nodes: chunks, sections (chunks under the same heading), entities (names that
appear in two or more chunks). Edges:

  chunk - next chunk          reading order (stronger inside one section)
  chunk - figure chunk        "see Figure 3", and a figure to the passage before it
  chunk - section             siblings reach each other in two steps
  chunk - entity              weighted 1/log(df): rare names connect strongly

Two uses:
  ppr()     Personalized PageRank from the query's best candidates (HippoRAG 2 style):
            pulls in passages that are connected to the hits but share no words with
            the question - the next step of a procedure, the figure, the definition.
  smooth()  one round of message passing over the same edges gives every chunk a
            "graph embedding" (its vector blended with its neighbours'), which stays in
            the query's vector space and is searched as its own channel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SAME_SECTION_NEXT = 1.0
OTHER_SECTION_NEXT = 0.4
FIGURE_LINK = 1.5
SECTION_LINK = 0.5


@dataclass
class ChunkMeta:
    id: int
    ord: int
    heading: str
    kind: str
    ref_ords: list[int]


@dataclass
class SourceGraph:
    chunk_ids: np.ndarray  # node index -> chunk id (chunk nodes come first)
    n_chunks: int
    n_nodes: int
    src: np.ndarray
    dst: np.ndarray
    weight: np.ndarray  # row-normalised transition weight src -> dst

    def index_of(self) -> dict[int, int]:
        return {int(cid): i for i, cid in enumerate(self.chunk_ids.tolist())}

    def ppr(self, seeds: dict[int, float], alpha: float = 0.5, iters: int = 30) -> dict[int, float]:
        """Personalized PageRank from seed chunk ids. Returns chunk id -> score."""
        if self.n_nodes == 0 or not seeds:
            return {}
        lookup = self.index_of()
        restart = np.zeros(self.n_nodes, dtype=np.float64)
        for chunk_id, value in seeds.items():
            index = lookup.get(int(chunk_id))
            if index is not None and value > 0:
                restart[index] += value
        total = restart.sum()
        if total <= 0:
            return {}
        restart /= total
        scores = restart.copy()
        for _ in range(iters):
            spread = np.bincount(self.dst, weights=scores[self.src] * self.weight, minlength=self.n_nodes)
            lost = 1.0 - spread.sum()  # mass stuck on nodes without edges goes back to the seeds
            nxt = (1 - alpha) * restart + alpha * (spread + max(lost, 0.0) * restart)
            if np.abs(nxt - scores).sum() < 1e-6:
                scores = nxt
                break
            scores = nxt
        chunk_scores = scores[: self.n_chunks]
        return {int(self.chunk_ids[i]): float(chunk_scores[i]) for i in np.nonzero(chunk_scores > 0)[0]}

    def smooth(self, vectors: np.ndarray, self_weight: float = 0.65, batch: int = 20000) -> np.ndarray:
        """vectors: (n_chunks, d) unit rows in node order. Returns blended unit rows."""
        n, dim = vectors.shape
        feats = np.zeros((self.n_nodes, dim), dtype=np.float32)
        feats[:n] = vectors
        # Section/entity nodes take the mean of their chunks.
        hub = np.zeros((self.n_nodes, dim), dtype=np.float32)
        counts = np.zeros(self.n_nodes, dtype=np.float32)
        to_hub = (self.src < n) & (self.dst >= n)
        hub_src, hub_dst = self.src[to_hub], self.dst[to_hub]
        for start in range(0, hub_src.shape[0], batch):
            s, d = hub_src[start : start + batch], hub_dst[start : start + batch]
            np.add.at(hub, d, feats[s])
            np.add.at(counts, d, 1.0)
        nonzero = counts > 0
        hub[nonzero] /= counts[nonzero, None]
        feats[n:] = hub[n:]
        # Each chunk: weighted mean over its edges.
        neighbour = np.zeros((n, dim), dtype=np.float32)
        weights = np.zeros(n, dtype=np.float32)
        from_chunk = self.src < n
        c_src, c_dst, c_w = self.src[from_chunk], self.dst[from_chunk], self.weight[from_chunk]
        for start in range(0, c_src.shape[0], batch):
            s, d, w = c_src[start : start + batch], c_dst[start : start + batch], c_w[start : start + batch]
            np.add.at(neighbour, s, feats[d] * w[:, None].astype(np.float32))
            np.add.at(weights, s, w.astype(np.float32))
        has = weights > 0
        neighbour[has] /= weights[has, None]
        out = vectors.astype(np.float32).copy()
        out[has] = self_weight * vectors[has] + (1 - self_weight) * neighbour[has]
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


def entity_cap(n_chunks: int) -> int:
    """Names in more chunks than this are too common to mean anything ("the system")."""
    return max(30, int(0.05 * n_chunks))


def build(chunks: list[ChunkMeta], memberships: list[tuple[int, int, int]]) -> SourceGraph:
    """chunks in reading order; memberships: (chunk_id, entity_id, df)."""
    chunks = sorted(chunks, key=lambda c: c.ord)
    n = len(chunks)
    chunk_ids = np.array([c.id for c in chunks], dtype=np.int64)
    by_ord = {c.ord: i for i, c in enumerate(chunks)}
    by_id = {c.id: i for i, c in enumerate(chunks)}
    edges: dict[tuple[int, int], float] = {}

    def link(a: int, b: int, w: float) -> None:
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        edges[key] = max(edges.get(key, 0.0), w)

    for i in range(n - 1):
        same = chunks[i].heading == chunks[i + 1].heading
        link(i, i + 1, SAME_SECTION_NEXT if same else OTHER_SECTION_NEXT)
    for i, chunk in enumerate(chunks):
        for ref in chunk.ref_ords or []:
            j = by_ord.get(ref)
            if j is not None:
                link(i, j, FIGURE_LINK)

    next_node = n
    sections: dict[str, list[int]] = {}
    for i, chunk in enumerate(chunks):
        if chunk.heading:
            sections.setdefault(chunk.heading, []).append(i)
    for members in sections.values():
        if len(members) < 3:  # two chunks are already joined by reading order
            continue
        node = next_node
        next_node += 1
        for i in members:
            link(i, node, SECTION_LINK)

    cap = entity_cap(n)
    entity_nodes: dict[int, int] = {}
    for chunk_id, entity_id, df in memberships:
        if df < 2 or df > cap:
            continue
        i = by_id.get(chunk_id)
        if i is None:
            continue
        node = entity_nodes.get(entity_id)
        if node is None:
            node = next_node
            next_node += 1
            entity_nodes[entity_id] = node
        link(i, node, 1.0 / math.log2(1 + df))

    n_nodes = next_node
    if not edges:
        empty = np.zeros(0, dtype=np.int64)
        return SourceGraph(chunk_ids, n, n_nodes, empty, empty, np.zeros(0))
    pairs = np.array(list(edges.keys()), dtype=np.int64)
    w = np.array(list(edges.values()), dtype=np.float64)
    src = np.concatenate([pairs[:, 0], pairs[:, 1]])
    dst = np.concatenate([pairs[:, 1], pairs[:, 0]])
    weight = np.concatenate([w, w])
    out_sum = np.bincount(src, weights=weight, minlength=n_nodes)
    weight = weight / out_sum[src]
    return SourceGraph(chunk_ids, n, n_nodes, src, dst, weight)
