"""The load-bearing cost guard (design doc 4.4, plan 8.3).

Compilation must never scan the full wiki. If this test is failing, the fix is
in the compiler, not here: per-ingest cost that grows with corpus size is the one
failure mode that makes the whole project uneconomic.

Permanent test. Do not weaken it.
"""

from __future__ import annotations

import pytest
from tests.doubles import ScriptedLLM
from tests.factories import make_extracted_doc, seed_gists

from llmwiki.storage.layout import WIKI_PREFIX
from llmwiki.wiki.compiler import Compiler


def _compiler(spy_store, vectors, embedder, llm, settings) -> Compiler:
    return Compiler(spy_store, vectors, embedder, llm, settings)


@pytest.mark.parametrize("wiki_size", [10, 200])
def test_compile_reads_a_bounded_number_of_pages(
    wiki_size, spy_store, vectors, embedder, llm, settings
) -> None:
    seed_gists(spy_store.inner, wiki_size, embedder=embedder, vectors=vectors,
               index=settings.vectorize_gists_index)
    spy_store.reset()

    _compiler(spy_store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    pages_read = spy_store.distinct_gets(prefix=f"{WIKI_PREFIX}concepts/")
    assert len(pages_read) <= settings.compile_max_pages, (
        f"compiling one source touched {len(pages_read)} distinct page bodies from a "
        f"{wiki_size}-page wiki; the cap is COMPILE_MAX_PAGES={settings.compile_max_pages}"
    )
    assert not spy_store.lists(prefix=WIKI_PREFIX), (
        f"the compiler listed {spy_store.lists(prefix=WIKI_PREFIX)}; listing a wiki prefix is a "
        "full scan by another name"
    )


def test_page_reads_do_not_grow_with_wiki_size(
    tmp_path, vectors, embedder, llm, settings
) -> None:
    """The sharper form: identical work at 10 pages and at 200."""
    from tests.doubles import SpyObjectStore

    from llmwiki.storage.local import LocalObjectStore

    counts = []
    for size in (10, 200):
        root = tmp_path / f"wiki-{size}"
        spy = SpyObjectStore(LocalObjectStore(root=root))
        store_vectors = type(vectors)(dim=settings.embedding_dim)
        seed_gists(spy.inner, size, embedder=embedder, vectors=store_vectors,
                   index=settings.vectorize_gists_index)
        spy.reset()

        Compiler(spy, store_vectors, embedder, llm, settings).compile_source(make_extracted_doc())
        counts.append(len(spy.distinct_gets(prefix=f"{WIKI_PREFIX}concepts/")))

    assert counts[0] == counts[1], (
        f"page-body reads grew from {counts[0]} to {counts[1]} as the wiki grew 20x; "
        "compilation cost must be independent of corpus size"
    )


def test_planner_prompt_size_is_bounded_by_the_candidate_cap(
    spy_store, vectors, embedder, settings
) -> None:
    """Plan 8.3-2: planner input scales with COMPILE_CANDIDATE_PAGES, not wiki size."""
    lengths = []
    for size in (10, 200):
        scripted = ScriptedLLM({
            "summarize_source": {"title": "RAG", "gist": "About retrieval.",
                                 "summary": "Retrieval augmented generation.",
                                 "concepts": ["retrieval"], "entities": []},
            "plan_compile": {"ops": []},
        })
        store = spy_store.inner
        store_vectors = type(vectors)(dim=settings.embedding_dim)
        seed_gists(store, size, embedder=embedder, vectors=store_vectors,
                   index=settings.vectorize_gists_index)

        Compiler(store, store_vectors, embedder, scripted, settings).compile_source(
            make_extracted_doc()
        )
        lengths.append(len(scripted.prompts_for("plan_compile")[0]))

    growth = lengths[1] / max(lengths[0], 1)
    assert growth < 1.5, (
        f"planner prompt grew {growth:.1f}x when the wiki grew 20x; the planner is seeing "
        "more than COMPILE_CANDIDATE_PAGES candidates"
    )


def test_recompiling_the_same_source_is_a_no_op(
    store, vectors, embedder, llm, settings
) -> None:
    """Plan 8.3-3: idempotence. The second pass must not spend a single LLM call."""
    doc = make_extracted_doc()
    compiler = Compiler(store, vectors, embedder, llm, settings)
    first = compiler.compile_source(doc)
    assert first.pages_touched > 0, "the first compile should have built something"

    calls_before = len(llm.calls)
    second = Compiler(store, vectors, embedder, llm, settings).compile_source(doc)

    assert second.pages_touched == 0
    assert len(llm.calls) == calls_before, "re-compiling an unchanged source called the LLM again"
