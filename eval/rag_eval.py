"""Measure retrieval on a live corpus, switch stages off one at a time, and fit the blend.

    python -m eval.rag_eval --questions eval/book_questions.jsonl                 # full pipeline
    python -m eval.rag_eval --questions ... --ablate                             # every stage off in turn
    python -m eval.rag_eval --questions ... --fit                                # best RAG_W_* weights

Questions file, one JSON object per line (write ~200 for the book, ~50 for the chatbot):
    {"question": "What does Figure 3 show?", "expect": ["continual learning"]}
    {"question": "...", "pages": [12, 13]}
A question counts as found when any of the top 5 passages contains one of `expect`
(case-insensitive) or comes from one of `pages`. Runs against the database and gateway in
.env, on the live source of --scope (book | chatbot) unless --source-id is given.
Keep stages that raise recall@5; drop the ones that do not.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
import uuid

from sqlalchemy import text

from app.db.session import PostgresSession
from app.services.rag import fusion
from app.services.rag import retrieve as R

TOP = 5


def _hit(passage: R.Passage, case: dict) -> bool:
    content = f"{passage.heading}\n{passage.content}".lower()
    if any(str(e).lower() in content for e in case.get("expect") or []):
        return True
    return passage.page is not None and passage.page in set(case.get("pages") or [])


def _score(ranked: list[R.Passage], case: dict) -> tuple[bool, float]:
    for rank, passage in enumerate(ranked[:TOP], start=1):
        if _hit(passage, case):
            return True, 1.0 / rank
    return False, 0.0


async def _source(session, scope: str, source_id: str | None) -> tuple[uuid.UUID, int]:
    if source_id:
        row = (await session.execute(text("SELECT id, chunk_count FROM studio.rag_sources WHERE id = :i"), {"i": uuid.UUID(source_id)})).first()
    else:
        row = (await session.execute(text("SELECT id, chunk_count FROM studio.rag_sources WHERE scope = :s AND active"), {"s": scope})).first()
    if row is None:
        raise SystemExit("No such source. Ingest it first.")
    return row[0], int(row[1])


async def run(cases: list[dict], scope: str, source_id: str | None, label: str) -> list[dict]:
    pg = PostgresSession()
    results = []
    started = time.time()
    try:
        async with pg.sessionmaker() as session:
            sid, total = await _source(session, scope, source_id)
            for case in cases:
                found = await R.retrieve(session, [sid], case["question"], total_chunks=total, max_passages=TOP, budget=10_000)
                hit, rr = _score(found.passages, case)
                pool = found.debug.get("pool") or []
                passages = await R._passages(session, pool)
                results.append(
                    {"case": case, "hit": hit, "rr": rr, "best": found.best, "debug": found.debug, "passages": passages}
                )
                await session.rollback()
    finally:
        await pg.dispose()
    n = max(len(results), 1)
    recall = sum(r["hit"] for r in results) / n
    mrr = sum(r["rr"] for r in results) / n
    print(f"{label:<22} recall@{TOP}={recall:.3f}  MRR={mrr:.3f}  ({len(results)} questions, {time.time() - started:.0f}s)")
    return results


def fit(results: list[dict]) -> None:
    """Grid-search the blend weights on the recorded signals (no new model calls)."""
    best = (-1.0, None)
    steps = [x / 20 for x in range(21)]
    for wr, wg in itertools.product(steps, steps):
        wf = 1.0 - wr - wg
        if wf < -1e-9:
            continue
        total = 0.0
        for r in results:
            d = r["debug"]
            if not d.get("ce"):
                continue
            final = fusion.blend(d["pool"], d["fused"], d["ppr"], d["ce"], weights=(wr, wg, wf))
            ranked = [r["passages"][i] for i in fusion.top(final, TOP) if i in r["passages"]]
            total += _score(ranked, r["case"])[1]
        if total > best[0]:
            best = (total, (wr, wg, max(wf, 0.0)))
    n = max(len(results), 1)
    if best[1] is None:
        print("No reranker scores recorded; is the gateway's /rerank on?")
        return
    wr, wg, wf = best[1]
    print(f"best MRR={best[0] / n:.3f} with RAG_W_RERANK={wr:.2f} RAG_W_GRAPH={wg:.2f} RAG_W_FUSED={wf:.2f}")


def calibrate(on: list[float], off: list[float], scope: str) -> None:
    """Pick the cut that wrongly refuses the fewest real questions while letting the fewest
    off-topic ones through (each mistake counts the same)."""
    best = (len(on) + len(off) + 1, 0.0)
    for cut in sorted(set(on + off + [0.0, 1.0])):
        mistakes = sum(score < cut for score in on) + sum(score >= cut for score in off)
        if mistakes < best[0]:
            best = (mistakes, cut)
    print(
        f"on-topic best scores {min(on):.3f}-{max(on):.3f}, off-topic {min(off):.3f}-{max(off):.3f}; "
        f"a reranker floor of {best[1]:.3f} makes {best[0]} mistakes out of {len(on) + len(off)}. Set "
        f"RAG_REFUSE_RERANK_{scope.upper()} BELOW the lowest on-topic score: off-topic questions above it "
        "are refused by the model itself, a wrongly refused real question is worse"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", required=True)
    parser.add_argument("--scope", default="book", choices=["book", "chatbot"])
    parser.add_argument("--source-id")
    parser.add_argument("--ablate", action="store_true")
    parser.add_argument("--fit", action="store_true")
    parser.add_argument(
        "--calibrate",
        metavar="OFF_TOPIC.jsonl",
        help="questions the document cannot answer; prints the refusal threshold that separates them best",
    )
    args = parser.parse_args()
    with open(args.questions, encoding="utf-8") as handle:
        cases = [json.loads(line) for line in handle if line.strip()]

    results = asyncio.run(run(cases, args.scope, args.source_id, "full"))
    if args.ablate:
        for stage in R.STAGES:
            R.STAGES[stage] = False
            asyncio.run(run(cases, args.scope, args.source_id, f"without {stage}"))
            R.STAGES[stage] = True
    if args.fit:
        fit(results)
    if args.calibrate:
        with open(args.calibrate, encoding="utf-8") as handle:
            off = [json.loads(line) for line in handle if line.strip()]
        off_results = asyncio.run(run(off, args.scope, args.source_id, "off-topic"))
        calibrate([r["best"] for r in results], [r["best"] for r in off_results], args.scope)


if __name__ == "__main__":
    main()
