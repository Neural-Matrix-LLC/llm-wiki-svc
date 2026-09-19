"""Query-side domain scoping (Phase 2, plan §21.2 A6, §21.6.2) and the retrieval seam.

Two guards from plan §21.10 live here: a query reads only the manifests of the
domains its hits came from, and fan-out is bounded by the policy (`all` =
one query per domain per layer; `routed` ≤ QUERY_MAX_DOMAINS). Everything
else pins that a general-only registry is byte-for-byte the pre-Phase-2
query path.
"""

from __future__ import annotations

import pytest
from tests.doubles import ScriptedLLM, SpyObjectStore
from tests.factories import make_extracted_doc

from llmwiki.agent.query import QueryAgent
from llmwiki.agent.retrieval import gate_score, retrieve_layer
from llmwiki.llm.fake import FakeLLM
from llmwiki.models.page import Domain, DomainRegistry
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import GENERAL, GISTS_KEY, gists_key, raw_meta
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import DomainScope, UnknownDomain, resolve_scopes, upsert_domain


class RecordingVectors:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.queried: list[str] = []

    def upsert(self, index, ids, vectors, metadata):
        self.inner.upsert(index, ids, vectors, metadata)

    def query(self, index, vector, k=5, where=None):
        self.queried.append(index)
        return self.inner.query(index, vector, k=k, where=where)

    def delete_by_source(self, index, source_id):
        return self.inner.delete_by_source(index, source_id)

    def ensure_index(self, index):
        return self.inner.ensure_index(index)


def _compile(store, vectors, embedder, llm, settings, source_id: str, title: str, domain: str):
    doc = make_extracted_doc(source_id=source_id, title=title)
    store.put(raw_meta(source_id),
              SourceMeta(source_id=source_id, modality="web", sha256="0" * 64, url=doc.url,
                         title=title).model_dump_json().encode(), "application/json")
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc, domain=domain)
    return doc


# --- resolve_scopes ----------------------------------------------------------------------


def _registry(*names: str) -> DomainRegistry:
    return DomainRegistry(domains={n: Domain(name=n) for n in names})


def test_resolve_scopes_matrix() -> None:
    empty, two = _registry(), _registry("ml", "bio")
    names = lambda scopes: [s.name for s in scopes]  # noqa: E731

    assert names(resolve_scopes(None, "all", empty)) == [GENERAL]
    assert names(resolve_scopes(None, "routed", empty, route=lambda: ["ml"])) == [GENERAL]
    assert names(resolve_scopes("general", "all", two)) == [GENERAL]
    assert names(resolve_scopes("ml", "general", two)) == ["ml"]
    assert names(resolve_scopes(None, "general", two)) == [GENERAL]
    assert names(resolve_scopes(None, "all", two)) == [GENERAL, "bio", "ml"]
    assert names(resolve_scopes(None, "routed", two, route=lambda: ["bio", "nope"])) == ["bio"]
    assert names(resolve_scopes(None, "routed", two, route=lambda: [])) == [GENERAL, "bio", "ml"]
    with pytest.raises(UnknownDomain):
        resolve_scopes("nope", "all", two)


# --- retrieve_layer ----------------------------------------------------------------------


def test_single_scope_is_the_pre_phase2_call_and_tags_hits(vectors, embedder, settings) -> None:
    recording = RecordingVectors(vectors)
    vec = embedder.embed(["retrieval"])[0]
    vectors.upsert(settings.vectorize_gists_index, ["rag"], [vec], [{"slug": "rag", "text": "x"}])

    hits = retrieve_layer("gists", "retrieval", vec, [DomainScope(GENERAL)], 5,
                          vectors=recording, settings=settings)

    assert recording.queried == [settings.vectorize_gists_index]
    assert [h.slug for h in hits] == ["rag"]
    assert hits[0].domain == GENERAL and hits[0].dense_score == hits[0].score
    assert gate_score(hits[0]) == hits[0].score


def test_multi_scope_queries_each_index_once_and_merges_by_score(vectors, embedder, settings):
    recording = RecordingVectors(vectors)
    base = settings.vectorize_gists_index
    q = embedder.embed(["retrieval"])[0]
    near = embedder.embed(["retrieval augmented"])[0]
    far = embedder.embed(["unrelated cooking"])[0]
    vectors.upsert(base, ["g"], [far], [{"slug": "g", "text": "x"}])
    vectors.upsert(f"{base}-ml", ["m"], [near], [{"slug": "m", "text": "x"}])

    hits = retrieve_layer("gists", "retrieval", q, [DomainScope(GENERAL), DomainScope("ml")], 5,
                          vectors=recording, settings=settings)

    assert recording.queried == [base, f"{base}-ml"]
    assert [(h.slug, h.domain) for h in hits] == [("m", "ml"), ("g", GENERAL)]
    assert hits[0].score >= hits[1].score


def test_no_scopes_means_no_hits_and_no_queries(vectors, embedder, settings) -> None:
    recording = RecordingVectors(vectors)
    assert retrieve_layer("chunks", "q", embedder.embed(["q"])[0], [], 5,
                          vectors=recording, settings=settings) == []
    assert recording.queried == []


# --- QueryAgent over domains ------------------------------------------------------------


def test_general_only_registry_queries_exactly_the_phase1_indexes(store, vectors, embedder,
                                                                   llm, settings) -> None:
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Retrieval-Augmented Generation",
             GENERAL)
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    answer = agent.answer("retrieval augmented generation")

    assert recording.queried[0] == settings.vectorize_gists_index
    assert answer.domains == [GENERAL]
    assert all(c.domain == GENERAL for c in answer.citations)


def test_policy_all_fans_out_once_per_domain_per_layer_and_tags_citations(
    store, vectors, embedder, llm, settings,
) -> None:
    upsert_domain(store, "ml", "Machine learning")
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Chunking Strategies", GENERAL)
    _compile(store, vectors, embedder, llm, settings, "b" * 16, "Paged Attention", "ml")
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    answer = agent.answer("paged attention kv cache")

    base = settings.vectorize_gists_index
    gist_queries = [q for q in recording.queried if q.startswith(base)]
    assert sorted(gist_queries[:2]) == sorted([base, f"{base}-ml"]), "one gists query per domain"
    assert answer.domains == [GENERAL, "ml"]
    assert {c.domain for c in answer.citations} <= {GENERAL, "ml"}
    assert any(c.domain == "ml" for c in answer.citations)
    assert "(domain: ml)" in answer.context


def test_explicit_domain_searches_only_that_domain(store, vectors, embedder, llm, settings):
    upsert_domain(store, "ml")
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Chunking Strategies", GENERAL)
    _compile(store, vectors, embedder, llm, settings, "b" * 16, "Paged Attention", "ml")
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    answer = agent.answer("paged attention", domain="ml")

    assert all(q.endswith("-ml") for q in recording.queried), recording.queried
    assert answer.domains == ["ml"]
    with pytest.raises(UnknownDomain):
        agent.answer("x", domain="nope")


def test_query_reads_only_the_manifests_of_hit_domains(store, vectors, embedder, llm, settings):
    """Load-bearing scale guard (plan §21.10): an explicit-domain query never opens
    another domain's manifest, however many are registered."""
    for name in ("ml", "bio", "chem"):
        upsert_domain(store, name)
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Chunking Strategies", GENERAL)
    _compile(store, vectors, embedder, llm, settings, "b" * 16, "Paged Attention", "ml")
    spy = SpyObjectStore(store)
    agent = QueryAgent(spy, vectors, embedder, llm, settings)

    agent.answer("paged attention", domain="ml")

    manifests = {k for k in spy.get_keys if k.endswith("gists.json")}
    assert manifests == {gists_key("ml")}, manifests
    assert GISTS_KEY not in spy.get_keys


def test_policy_routed_asks_the_router_once_and_is_bounded(store, vectors, embedder, settings):
    upsert_domain(store, "ml")
    upsert_domain(store, "bio")
    llm = ScriptedLLM({"route_domain": {"domains": ["ml", "bio", "general"]},
                       "answer_query": {"text": "routed answer"}})
    fake = FakeLLM()
    _compile(store, vectors, embedder, fake, settings, "b" * 16, "Paged Attention", "ml")
    cfg = settings.model_copy(update={"query_domain_policy": "routed", "query_max_domains": 1})
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, cfg)

    answer = agent.answer("paged attention")

    assert [c["op"] for c in llm.calls][0] == "route_domain"
    assert sum(1 for c in llm.calls if c["op"] == "route_domain") == 1
    assert answer.domains == ["ml"], "capped at QUERY_MAX_DOMAINS"
    base = settings.vectorize_gists_index
    assert recording.queried[0] == f"{base}-ml"


def test_policy_general_ignores_registered_domains(store, vectors, embedder, llm, settings):
    upsert_domain(store, "ml")
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Chunking Strategies", GENERAL)
    cfg = settings.model_copy(update={"query_domain_policy": "general"})
    recording = RecordingVectors(vectors)
    answer = QueryAgent(store, recording, embedder, llm, cfg).answer("chunking")
    assert answer.domains == [GENERAL]
    assert not any(q.endswith("-ml") for q in recording.queried)


def test_search_scopes_like_answer(store, vectors, embedder, llm, settings) -> None:
    upsert_domain(store, "ml")
    _compile(store, vectors, embedder, llm, settings, "b" * 16, "Paged Attention", "ml")
    agent = QueryAgent(store, vectors, embedder, llm, settings)
    hits = agent.search("paged attention", domain="ml")
    assert hits and all(h.domain == "ml" for h in hits)
    assert agent.search("paged attention", domain=GENERAL) == []


# --- the toolkit's domain argument ------------------------------------------------------


def test_tools_default_to_the_runs_scopes_and_accept_an_explicit_domain(
    store, vectors, embedder, llm, settings,
) -> None:
    from llmwiki.agent import toolkit

    upsert_domain(store, "ml", "Machine learning")
    _compile(store, vectors, embedder, llm, settings, "a" * 16, "Chunking Strategies", GENERAL)
    _compile(store, vectors, embedder, llm, settings, "b" * 16, "Paged Attention", "ml")
    agent = QueryAgent(store, vectors, embedder, llm, settings)
    agent._run_scopes = [DomainScope("ml")]
    tools = {t.name: t for t in toolkit.build_tools(agent)}

    scoped = toolkit.dispatch(list(tools.values()), "search_wiki", {"query": "chunking"})
    assert all(c.domain == "ml" for c in scoped.citations)

    explicit = toolkit.dispatch(list(tools.values()), "search_wiki",
                                {"query": "chunking", "domain": "general"})
    assert explicit.citations and all(c.domain == GENERAL for c in explicit.citations)

    page = toolkit.dispatch(list(tools.values()), "get_page",
                            {"slug": explicit.citations[0].slug, "domain": "general"})
    assert page.context_block.startswith("## Wiki page")

    unknown = toolkit.dispatch(list(tools.values()), "search_wiki",
                               {"query": "x", "domain": "nope"})
    assert "nope" in unknown.observation and not unknown.citations


def test_step_prompt_lists_domains_only_when_some_are_registered(store, vectors, embedder, llm,
                                                                  settings) -> None:
    from llmwiki.agent.graph import _render_step_prompt

    agent = QueryAgent(store, vectors, embedder, llm, settings)
    state = {"query": "q", "scopes": ["general"], "steps": [], "notes": [], "context": ""}
    assert "# Domains" not in _render_step_prompt(state, [], 4, registry=agent.registry)

    upsert_domain(store, "ml", "Machine learning")
    agent = QueryAgent(store, vectors, embedder, llm, settings)
    text = _render_step_prompt({**state, "scopes": ["ml"]}, [], 4, registry=agent.registry)
    assert "# Domains" in text and "Searching: ml" in text and "- ml: Machine learning" in text
