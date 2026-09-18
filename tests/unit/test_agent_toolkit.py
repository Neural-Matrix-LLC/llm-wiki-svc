"""The query graph's tools and the action schema they generate (Phase 1-D)."""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.agent import toolkit
from llmwiki.agent.query import QueryAgent
from llmwiki.models.plan import ExternalRef
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_meta
from llmwiki.websearch.fake import FakeWebSearcher
from llmwiki.wiki.compiler import Compiler


@pytest.fixture
def agent(store, vectors, embedder, llm, settings):
    doc = make_extracted_doc()
    store.put(
        raw_meta(doc.source_id),
        SourceMeta(source_id=doc.source_id, modality="web", sha256="0" * 64,
                   url=doc.url, title=doc.title).model_dump_json().encode(),
        "application/json",
    )
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc)
    vectors.upsert(
        settings.vectorize_chunks_index, [f"{doc.source_id}:0"], embedder.embed(["chunk"]),
        [{"source_id": doc.source_id, "title": doc.title, "url": doc.url,
          "text": "verbatim chunk"}],
    )
    return QueryAgent(store, vectors, embedder, llm, settings)


def _by_name(tools):
    return {t.name: t for t in tools}


def test_the_three_local_tools_exist_and_web_is_absent_without_a_searcher(agent) -> None:
    names = [t.name for t in toolkit.build_tools(agent)]
    assert names == ["search_wiki", "search_chunks", "get_page"]


def test_search_web_exists_only_when_a_searcher_is_given(agent) -> None:
    agent.web_searcher = FakeWebSearcher()
    assert [t.name for t in toolkit.build_tools(agent)][-1] == "search_web"


def test_search_wiki_returns_page_blocks_and_page_citations(agent) -> None:
    result = toolkit.dispatch(toolkit.build_tools(agent), "search_wiki",
                              {"query": "retrieval augmented generation"})
    assert "## Wiki page [[" in result.context_block
    assert result.observation == result.context_block
    assert {c.source_id for c in result.citations} == {"a1b2c3d4e5f60718"}
    assert all(c.slug for c in result.citations)


def test_search_chunks_returns_chunk_blocks_and_url_citations(agent) -> None:
    result = toolkit.dispatch(toolkit.build_tools(agent), "search_chunks", {"query": "chunk"})
    assert "## Source chunk a1b2c3d4e5f60718" in result.context_block
    assert "verbatim chunk" in result.context_block
    assert result.citations[0].url == "https://example.org/rag"


def test_get_page_reads_one_page_by_slug(agent) -> None:
    result = toolkit.dispatch(toolkit.build_tools(agent), "get_page", {"slug": "documents"})
    assert result.context_block.startswith("## Wiki page [[documents]]")
    assert result.citations


def test_get_page_unknown_slug_is_an_observation(agent) -> None:
    result = toolkit.dispatch(toolkit.build_tools(agent), "get_page", {"slug": "nope"})
    assert result.context_block == ""
    assert "No wiki page with slug 'nope'" in result.observation


def test_dispatch_never_raises_on_bad_arguments_or_unknown_tools(agent) -> None:
    tools = toolkit.build_tools(agent)
    assert "failed" in toolkit.dispatch(tools, "get_page", {"bogus": 1}).observation
    assert "No such tool" in toolkit.dispatch(tools, "teleport", {}).observation


def test_search_web_yields_external_refs_never_citations_or_context(agent) -> None:
    agent.web_searcher = FakeWebSearcher([ExternalRef(title="T", url="https://x.org/a",
                                                      snippet="s")])
    result = toolkit.dispatch(toolkit.build_tools(agent), "search_web", {"query": "q"})
    assert result.citations == []
    assert result.context_block == ""
    assert [r.url for r in result.external_refs] == ["https://x.org/a"]
    assert "https://x.org/a" in result.observation


# --- offered_tools: the policy gate --------------------------------------------


def test_off_policy_withholds_web_even_with_a_searcher(agent) -> None:
    agent.web_searcher = FakeWebSearcher()
    tools = toolkit.build_tools(agent)
    offered = toolkit.offered_tools(tools, used_rag_fallback=True, web_calls=0,
                                    policy="off", max_web=1)
    assert "search_web" not in _by_name(offered)


def test_weak_policy_offers_web_only_after_the_fallback(agent) -> None:
    agent.web_searcher = FakeWebSearcher()
    tools = toolkit.build_tools(agent)
    strong = toolkit.offered_tools(tools, used_rag_fallback=False, web_calls=0,
                                   policy="weak", max_web=1)
    weak = toolkit.offered_tools(tools, used_rag_fallback=True, web_calls=0,
                                 policy="weak", max_web=1)
    assert "search_web" not in _by_name(strong)
    assert "search_web" in _by_name(weak)


def test_always_policy_offers_web_until_the_cap(agent) -> None:
    agent.web_searcher = FakeWebSearcher()
    tools = toolkit.build_tools(agent)
    first = toolkit.offered_tools(tools, used_rag_fallback=False, web_calls=0,
                                  policy="always", max_web=1)
    capped = toolkit.offered_tools(tools, used_rag_fallback=False, web_calls=1,
                                   policy="always", max_web=1)
    assert "search_web" in _by_name(first)
    assert "search_web" not in _by_name(capped)
    assert len(capped) == 3, "the local tools are always offered"


# --- the action schema --------------------------------------------------------


def test_action_schema_enumerates_offered_tools_plus_answer(agent) -> None:
    tools = toolkit.build_tools(agent)
    schema = toolkit.action_schema(tools)
    assert schema["properties"]["action"]["enum"] == [
        "search_wiki", "search_chunks", "get_page", "answer",
    ]
    assert set(schema["properties"]["args"]["properties"]) == {"query", "k", "slug"}
    assert schema["required"] == ["action", "reason"]


def test_describe_tools_lists_each_tool_with_its_arguments(agent) -> None:
    text = toolkit.describe_tools(toolkit.build_tools(agent))
    assert "- search_wiki(query: string, k: integer)" in text
    assert "- get_page(slug: string)" in text
    assert text.endswith("answer from what was retrieved")
