"""Pure parts of the RAG engine: parsing, chunking, the graph, fusion, links."""

from __future__ import annotations

import time
import uuid

import numpy as np

from app.services.rag import answer, entities, fusion, media, portfolio
from app.services.rag import graph as graphs
from app.services.rag.chunk import count_tokens, embed_text, make_chunks
from app.services.rag.parse import Block, FigureDraft, caption_number, parse_markdown
from app.services.rag.retrieve import tsquery_terms, vector_literal


def _figure(number: int, caption: str) -> FigureDraft:
    return FigureDraft(number=number, image_png=b"x", width=400, height=300, caption=caption,
                       doc_number=caption_number(caption))


def test_markdown_keeps_heading_paths():
    doc = parse_markdown("# Book\n\n## Cells\n\nCells are small.\n\n### Parts\n\nThe nucleus.\n", "b.md")
    assert doc.title == "Book"
    assert doc.blocks[0].headings == ["Book", "Cells"]
    assert doc.blocks[1].headings == ["Book", "Cells", "Parts"]


def test_chunks_respect_size_and_split_on_headings():
    long = " ".join(["word"] * 1000)
    blocks = [Block("text", "Intro text.", ["A"]), Block("text", long, ["B"]), Block("text", "Tail.", ["B"])]
    chunks = make_chunks(blocks)
    assert chunks[0].heading == "A" and chunks[0].content == "Intro text."
    assert all(c.tokens <= 430 for c in chunks)
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_figures_are_their_own_chunks_and_linked_by_reference():
    blocks = [
        Block("text", "The heart pumps blood.", ["Heart"]),
        Block("figure", "Figure 2: The heart", ["Heart"], figure=_figure(1, "Figure 2: The heart")),
        Block("text", "As Figure 2 shows, there are four chambers. " + "x " * 300, ["Heart"]),
    ]
    chunks = make_chunks(blocks)
    fig = next(c for c in chunks if c.kind == "figure")
    assert fig.content.startswith("Figure 2: The heart")
    assert make_chunks([Block("figure", "", ["H"], page=7, figure=FigureDraft(number=1, image_png=b"x", width=9, height=9))])[0].content == "Figure on page 7"
    assert fig.ord in chunks[0].ref_ords  # passage before the figure
    assert any(fig.ord in c.ref_ords for c in chunks if c.ord > fig.ord)  # "As Figure 2 shows"


def test_tables_split_with_header_repeated():
    rows = "\n".join(f"| r{i} | {'v ' * 20}|" for i in range(80))
    table = "| name | value |\n|---|---|\n" + rows
    chunks = make_chunks([Block("table", table, ["T"])])
    assert len(chunks) > 1
    assert all(c.content.startswith("| name | value |") for c in chunks)


def test_embed_text_carries_document_and_section():
    assert embed_text("Biology", "Cells > Parts", "The nucleus.") == (
        "Document: Biology\nSection: Cells > Parts\nThe nucleus."
    )


def _line_graph(n: int, entity_pairs=()):
    metas = [graphs.ChunkMeta(id=100 + i, ord=i, heading="S" if i < n // 2 else "T", kind="text", ref_ords=[])
             for i in range(n)]
    members = [(100 + c, e, df) for c, e, df in entity_pairs]
    return graphs.build(metas, members)


def test_ppr_spreads_to_neighbours_and_through_rare_entities():
    # chunk 0 and chunk 9 share a rare entity; nothing else connects them closely.
    graph = _line_graph(10, [(0, 1, 2), (9, 1, 2)])
    scores = graph.ppr({100: 1.0})
    assert scores[100] == max(scores.values())
    assert scores[109] > scores[105]  # the entity bridge beats distance in reading order
    assert scores[101] > 0


def test_common_entities_are_ignored():
    n = 40
    pairs = [(i, 7, n) for i in range(n)]  # in every chunk: too common to mean anything
    graph = _line_graph(n, pairs)
    assert graph.n_nodes < n + 3


def test_smoothing_moves_vectors_toward_neighbours_and_keeps_unit_length():
    graph = _line_graph(3)
    vectors = np.eye(3, 4, dtype=np.float32)
    smoothed = graph.smooth(vectors)
    assert np.allclose(np.linalg.norm(smoothed, axis=1), 1.0, atol=1e-5)
    assert smoothed[0, 1] > 0  # picked up some of chunk 1
    assert smoothed[0, 0] > smoothed[0, 1]  # but stays itself


def test_rrf_and_blend():
    scores = fusion.rrf([[1, 2, 3], [3, 1]], weights=[1.0, 1.0])
    assert fusion.top(scores, 3)[0] == 1
    final = fusion.blend([1, 2, 3], scores, {3: 0.9}, {1: 0.1, 2: 0.2, 3: 0.95})
    assert fusion.top(final, 1) == [3]
    assert fusion.unit_scale({1: 5.0}) == {1: 1.0}


def test_tsquery_terms_are_safe():
    assert tsquery_terms("What's the heart's role? DROP TABLE; --") == "what | the | heart | role | drop | table"
    assert tsquery_terms("??") == ""


def test_vector_literal():
    assert vector_literal([0.5, -1.0]) == "[0.500000,-1.000000]"


def test_media_links_verify_and_expire():
    figure_id = uuid.uuid4()
    token = media.sign(figure_id, seconds=60)
    assert media.verify(token) == figure_id
    assert media.verify(token, now=time.time() + 120) is None
    assert media.verify(token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]) is None
    assert media.verify("garbage") is None


def test_entity_heuristic_and_norm():
    found = dict(entities.heuristic("Karan built Studio with FastAPI and PostgreSQL in Pune. The cell has ATP."))
    keys = {entities.norm(k) for k in found}
    assert {"fastapi", "postgresql", "pune", "atp"} <= keys
    assert entities.query_norm("Who uses FastAPI?") == " who uses fastapi "


def test_portfolio_blocks_skip_phone_and_cover_sections():
    overview = {
        "profile": {"name": "Karan Parmar", "headline": "AI engineer", "about": "<p>Builds things</p>",
                    "email": "k@example.com", "phone": "+91 99999 99999"},
        "work_experience": [{"company": "Acme", "position": "Engineer", "description": ["Shipped RAG"],
                             "start_date": "2024-01", "is_current": True}],
        "education": [{"school": "Uni", "degree": "BE", "field_of_study": "CS"}],
        "skill_categories": [{"name": "AI", "skills": [{"name": "LangChain"}]}],
        "projects": [{"slug": "x", "name": "X", "short_description": "A tool"}],
        "project_details": [{"slug": "x", "name": "X", "long_description": "<ul><li>Fast</li></ul>"}],
        "social_media": [{"platform": "github", "url": "https://github.com/k"}],
    }
    blocks = portfolio.blocks_from(overview)
    text = "\n".join(b.text for b in blocks)
    assert "Karan Parmar — AI engineer" in text and "Builds things" in text
    assert "99999" not in text
    assert "Engineer at Acme" in text and "present" in text
    assert "LangChain" in text and "- Fast" in text and "github.com/k" in text
    assert len([b for b in blocks if b.headings[0] == "Projects"]) == 1


def test_follow_up_questions_search_with_the_previous_one():
    history = [{"role": "user", "content": "What does the heart do?"}, {"role": "assistant", "content": "Pumps."}]
    assert answer.search_query("and the lungs?", history) == "What does the heart do? and the lungs?"
    assert answer.search_query("Explain how the lungs exchange gases in detail", history).startswith("Explain")
    assert answer.search_query("what is supervised learning", history) == "what is supervised learning"
    assert answer.search_query("why is that?", history).startswith("What does the heart do?")


def test_cached_answer_replay_resigns_figure_links():
    figure_id = uuid.uuid4()
    lines = answer.replay({"text": "Hi", "sources": [], "figures": [{"id": str(figure_id), "url": "/old"}]})
    assert '"cached": true' in lines[-1]
    assert media.MEDIA_PATH in lines[0] and "/old" not in lines[0]


def test_question_normalization():
    assert answer.normalize_question("  What IS   Studio?? ") == f"v{answer.CACHE_VERSION}|what is studio"
    assert count_tokens("one two three") == 4


def test_dotted_figure_numbers_link_to_the_right_figure():
    from app.services.rag.parse import caption_number

    assert caption_number("Fig. 11.6.1: Multi-head attention") == "11.6.1"
    assert caption_number("Figure 3. Results") == "3"
    blocks = [
        Block("figure", "Fig. 11.6.1: Multi-head attention", ["Attention"], figure=_figure(5, "Fig. 11.6.1: Multi-head attention")),
        Block("figure", "Fig. 11.6.2: Something else", ["Attention"], figure=_figure(6, "Fig. 11.6.2: Something else")),
        Block("text", "As shown in Fig. 11.6.2, heads are concatenated. " + "x " * 300, ["Attention"]),
    ]
    chunks = make_chunks(blocks)
    figs = {c.figure.doc_number: c.ord for c in chunks if c.kind == "figure"}
    text_chunk = next(c for c in chunks if c.kind == "text")
    assert figs["11.6.2"] in text_chunk.ref_ords
    assert figs["11.6.1"] not in text_chunk.ref_ords
    assert chunks[0].content.startswith("Fig. 11.6.1: Multi-head attention")


def test_contents_and_index_sections_are_not_indexed():
    from app.services.rag.parse import skipped_heading

    doc = parse_markdown("# Book\n\n## Contents\n\n| 2.3 | Vectors | 42 |\n\n## Vectors\n\nA vector is a list.\n\n## Index\n\nvector, 42\n", "b.md")
    assert [b.text for b in doc.blocks] == ["A vector is a list."]
    assert skipped_heading("Table of Contents") and skipped_heading("Bibliography") and not skipped_heading("Attention")


def test_loose_caption_paragraph_is_given_to_the_figure():
    from app.services.rag.parse import attach_captions, clean_text

    fig = FigureDraft(number=8, image_png=b"x", width=400, height=300)
    blocks = attach_captions([
        Block("figure", "", ["Attention"], figure=fig),
        Block("text", "Fig. 11.6.1 Multi-head attention.", ["Attention"]),
        Block("text", "Heads run in parallel.", ["Attention"]),
    ])
    assert [b.kind for b in blocks] == ["figure", "text"]
    assert fig.doc_number == "11.6.1" and blocks[0].text.startswith("Fig. 11.6.1")
    assert clean_text("glyph[a116] Fig. 1 x") == "Fig. 1 x"


def test_refusal_threshold_is_per_scope():
    from app.services.rag import retrieve as R

    assert R.floors("book")[0] > R.floors("upload")[0]
    assert R.floors(None) == (0.55, 0.25)
    assert R._FIG_ASKED.findall("what does Fig. 11.5.1 and figure 3 show") == ["11.5.1", "3"]


def test_sources_are_trimmed_to_what_the_answer_cites():
    sources = [{"n": 1}, {"n": 2}, {"n": 3}]
    figures = [{"n": 2, "id": "a"}, {"n": 3, "id": "b"}]
    kept, figs = answer.keep_cited("Attention is weighted pooling [2].", sources, figures)
    assert kept == [{"n": 2}] and figs == [{"n": 2, "id": "a"}]
    assert answer.keep_cited("No citations here.", sources, figures) == (sources, figures)


def test_refusals_in_words_are_recognised_but_partial_answers_are_not():
    assert answer.is_refusal("NOT_FOUND")
    assert answer.is_refusal("The provided passages do not contain information on how to bake sourdough bread. They only discuss GRUs [5].")
    assert answer.is_refusal("I could not find that in the document.")
    assert not answer.is_refusal("The provided passages do not explicitly state why supervised learning is useful, though they give examples [2].")
    assert not answer.is_refusal("The passages do not contain a definition. However, supervised learning maps features to labels [1].")
    assert not answer.is_refusal("Supervised learning maps features to labels [1].")


def test_refusal_that_lists_other_topics_is_still_a_refusal():
    assert answer.is_refusal(
        "The provided passages do not contain information on how to bake sourdough bread. "
        "They discuss SageMaker and GRUs, but none mention baking [5]."
    )


def test_heartbeat_while_waiting_and_errors_pass_through():
    import asyncio

    async def slow():
        await asyncio.sleep(0.12)
        yield "first"
        yield "second"

    async def broken():
        await asyncio.sleep(0.01)
        raise RuntimeError("model down")
        yield  # pragma: no cover

    async def collect(source):
        return [item async for item in answer.with_heartbeat(source, every=0.05)]

    items = asyncio.run(collect(slow()))
    assert items.count(answer.HEARTBEAT) >= 1 and items[-2:] == ["first", "second"]
    try:
        asyncio.run(collect(broken()))
    except RuntimeError as exc:
        assert "model down" in str(exc)
    else:
        raise AssertionError("error was swallowed")


def test_waiting_status_counts_people_ahead(monkeypatch):
    import asyncio
    import json as _json
    import time as _time

    from app.services import llm_client

    async def board():
        return {"lanes": {"chat": {"running": True, "queued": 2}, "rag": {"running": False, "queued": 0},
                          "embed": {"running": True, "queued": 5}}}

    monkeypatch.setattr(llm_client, "queue_snapshot", board)
    line = _json.loads(asyncio.run(answer.waiting_status(_time.monotonic() - 7)))
    assert line["data"] == {"stage": "waiting", "ahead": 2, "waited": 7}  # embed lane is not an answer


def test_embedding_batch_retries_before_failing(monkeypatch):
    import asyncio

    from app.core.errors import ModelOfflineError
    from app.services import llm_client
    from app.services.rag import ingest

    calls = {"n": 0}

    async def flaky(texts, lane="embed", query=False):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ModelOfflineError("busy")
        return [[0.1] * 4 for _ in texts]

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(llm_client, "embed", flaky)
    monkeypatch.setattr(ingest.asyncio, "sleep", no_sleep)
    assert asyncio.run(ingest._embed_with_retry(["a", "b"], "ingest")) == [[0.1] * 4, [0.1] * 4]
    assert calls["n"] == 3

    async def always_down(texts, lane="embed", query=False):
        raise ModelOfflineError("down")

    monkeypatch.setattr(llm_client, "embed", always_down)
    try:
        asyncio.run(ingest._embed_with_retry(["a"], "ingest"))
    except ModelOfflineError:
        pass
    else:
        raise AssertionError("gave up silently")
