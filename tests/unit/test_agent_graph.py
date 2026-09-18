"""The query graph's bounded tool loop - Phase 1-D, design §4.9.

Every test here opts *into* the loop with ``agent_max_tool_calls > 0``;
``conftest.py``'s ``_isolate_query_tool_loop`` keeps the rest of the suite on
the pre-graph single-call path. The scripted LLM hands out ``agent_step``
decisions in order, so a test states exactly which tools the model "asks
for" and asserts what the graph did about it.

``test_tool_loop_is_bounded_by_agent_max_tool_calls`` is load-bearing
(CLAUDE.md): a question's LLM cost must be bounded by configuration, never
by the model's own judgement.
"""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.agent.graph import NO_ANSWER_TEXT, STEP_ATTEMPTS, recursion_limit
from llmwiki.agent.query import MAX_CONTEXT_CHARS, QueryAgent
from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord, ExternalRef
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_meta
from llmwiki.websearch.fake import FakeWebSearcher
from llmwiki.wiki.compiler import Compiler


class StepLLM:
    """Scripted per op: ``agent_step`` decisions in order, a fixed answer otherwise."""

    def __init__(self, decisions: list[dict], answer: str = "SCRIPTED ANSWER") -> None:
        self.decisions = decisions
        self.answer = answer
        self.calls: list[dict] = []

    def complete(self, *, op, system, prompt, schema=None, model=None,
                 max_tokens=None, temperature=None) -> LLMResponse:
        self.calls.append({"op": op, "system": system, "prompt": prompt, "schema": schema})
        usage = CostRecord(op=op, model="scripted", input_tokens=len(prompt) // 4, output_tokens=8)
        if op == "agent_step":
            index = len([c for c in self.calls if c["op"] == "agent_step"]) - 1
            data = self.decisions[index] if index < len(self.decisions) else {"action": "answer"}
            return LLMResponse(text="", data=data, usage=usage)
        return LLMResponse(text=self.answer, data={"text": self.answer}, usage=usage)

    def steps(self) -> list[dict]:
        return [c for c in self.calls if c["op"] == "agent_step"]


@pytest.fixture
def populated(store, vectors, embedder, llm, settings):
    """A wiki with one compiled source and its raw meta on disk (mirrors test_agent.py)."""
    doc = make_extracted_doc()
    store.put(
        raw_meta(doc.source_id),
        SourceMeta(source_id=doc.source_id, modality="web", sha256="0" * 64,
                   url=doc.url, title=doc.title).model_dump_json().encode(),
        "application/json",
    )
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc)
    return doc


def _loop(settings, n: int = 4, **overrides):
    return settings.model_copy(update={"agent_max_tool_calls": n, **overrides})


ALWAYS_SEARCH = {"action": "search_chunks", "args": {"query": "more detail"}, "reason": "dig"}


# --- bounds ------------------------------------------------------------------


def test_tool_loop_is_bounded_by_agent_max_tool_calls(
    populated, store, vectors, embedder, settings
) -> None:
    """LOAD-BEARING: a model that always asks for another tool gets exactly N.

    Distinct arguments each time, so the repeat-call guard does not end the
    loop early - only the cap does.
    """
    decisions = [
        {"action": "search_chunks", "args": {"query": f"angle {i}"}, "reason": "more"}
        for i in range(20)
    ]
    scripted = StepLLM(decisions)
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=3))

    result = agent.answer("retrieval augmented generation chunking")

    assert len(result.steps) == 3, "the cap is the configuration, not the model's appetite"
    # 3 tool decisions + 0 further calls: the cap short-circuits the 4th
    # agent_step without an LLM call, then one generation call.
    assert len(scripted.steps()) == 3
    assert [c["op"] for c in scripted.calls][-1] == "answer_query"
    assert result.text == "SCRIPTED ANSWER"


def test_zero_max_tool_calls_is_exactly_the_pre_graph_call_sequence(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([ALWAYS_SEARCH])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=0))

    result = agent.answer("retrieval augmented generation chunking")

    assert [c["op"] for c in scripted.calls] == ["answer_query"]
    assert scripted.calls[0]["system"] == load_prompt("answer_query")
    assert scripted.calls[0]["prompt"].startswith("# Question\n\nretrieval augmented")
    assert result.steps == []


def test_the_model_can_stop_early_by_answering(populated, store, vectors, embedder, settings):
    scripted = StepLLM([{"action": "answer", "reason": "enough"}])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert result.steps == []
    assert [c["op"] for c in scripted.calls] == ["agent_step", "answer_query"]


def test_an_invalid_action_is_retried_once_then_ends_the_loop(
    populated, store, vectors, embedder, settings, caplog
) -> None:
    """R5 posture: one nudge, then answer - never a hard failure, never a spin."""
    bad = {"action": "launch_missiles", "args": {}, "reason": "?"}
    scripted = StepLLM([bad, bad, ALWAYS_SEARCH])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert result.text == "SCRIPTED ANSWER"
    assert result.steps == []
    assert len(scripted.steps()) == STEP_ATTEMPTS, "exactly one retry, then stop"
    assert "# Reminder" in scripted.steps()[1]["prompt"]
    assert "no usable action" in caplog.text


def test_prose_on_the_first_attempt_is_recovered_by_the_retry(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([{"text": "I think I should search..."}, ALWAYS_SEARCH,
                        {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert [s.tool for s in result.steps] == ["search_chunks"]


def test_a_repeated_identical_tool_call_is_refused_but_still_counted(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([ALWAYS_SEARCH, ALWAYS_SEARCH, {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert len(result.steps) == 2
    assert result.steps[1].chars == 0, "the refused repeat added nothing"
    prompt = scripted.steps()[2]["prompt"]
    assert "already called with these arguments" in prompt


def test_context_budget_is_shared_and_forces_an_answer_when_exhausted(
    populated, store, vectors, embedder, settings
) -> None:
    """Chunk results big enough to blow the budget are truncated to it, and the
    next agent_step is skipped (no LLM call) because nothing more can fit."""
    # The compiler only fills the gist index; chunks come from the pipeline.
    # Upsert oversized chunks directly, the way pipeline._embed does.
    ids = [f"{populated.source_id}:{i}" for i in range(5)]
    vectors.upsert(
        settings.vectorize_chunks_index, ids, embedder.embed(["huge"] * 5),
        [{"source_id": populated.source_id, "title": "Huge", "text": "huge " * 1000}] * 5,
    )
    scripted = StepLLM([
        {"action": "search_chunks", "args": {"query": "huge"}, "reason": "1"},
        {"action": "search_chunks", "args": {"query": "huge again"}, "reason": "2"},
        {"action": "search_chunks", "args": {"query": "and again"}, "reason": "3"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("huge")

    assert len(result.context) <= MAX_CONTEXT_CHARS + 64, "budget is shared, not per step"
    assert len(scripted.steps()) < 4, "an exhausted budget skips the next decision call"
    assert result.text == "SCRIPTED ANSWER"


def test_recursion_limit_leaves_room_for_the_full_bounded_run() -> None:
    # retrieve + (n+1) agent + n tools + select + generate + resolve = 2n + 5
    for n in (0, 1, 4, 10):
        assert recursion_limit(n) >= 2 * n + 5


# --- what tools contribute ---------------------------------------------------


def test_citations_gathered_by_tools_must_still_resolve(
    populated, store, vectors, embedder, settings
) -> None:
    """The load-bearing contract, exercised through the tool path: a tool that
    surfaces a source id which is not under raw/ cannot cite it."""
    scripted = StepLLM([
        {"action": "get_page", "args": {"slug": "documents"}, "reason": "follow link"},
        {"action": "answer", "reason": "ok"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))
    # Delete the raw meta after compile: the page still names the source.
    store.delete(raw_meta(populated.source_id))

    result = agent.answer("retrieval augmented generation chunking")

    assert result.steps and result.steps[0].tool == "get_page"
    assert all(agent.source_exists(c.source_id) for c in result.citations)
    assert not any(c.source_id == populated.source_id for c in result.citations)


def test_get_page_on_an_unknown_slug_is_an_observation_not_an_error(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([
        {"action": "get_page", "args": {"slug": "no-such-page"}, "reason": "guess"},
        {"action": "answer", "reason": "ok"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert result.steps[0].chars == 0
    assert "No wiki page with slug" in scripted.steps()[1]["prompt"]


def test_a_tool_with_bad_arguments_is_an_observation_not_an_error(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([
        {"action": "get_page", "args": {"wrong": 1}, "reason": "typo"},
        {"action": "answer", "reason": "ok"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("retrieval augmented generation chunking")

    assert result.text == "SCRIPTED ANSWER"
    assert "get_page failed" in scripted.steps()[1]["prompt"]


def test_tool_results_reach_the_generation_prompt(populated, store, vectors, embedder, settings):
    scripted = StepLLM([
        {"action": "get_page", "args": {"slug": "documents"}, "reason": "follow link"},
        {"action": "answer", "reason": "ok"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    agent.answer("retrieval augmented generation chunking")

    generation = [c for c in scripted.calls if c["op"] == "answer_query"][-1]
    assert "## Wiki page [[documents]]" in generation["prompt"]


def test_empty_knowledge_base_never_enters_the_loop(store, vectors, embedder, settings) -> None:
    scripted = StepLLM([ALWAYS_SEARCH])
    agent = QueryAgent(store, vectors, embedder, scripted, _loop(settings, n=4))

    result = agent.answer("anything")

    assert result.text == NO_ANSWER_TEXT
    assert scripted.calls == []


# --- wiki-first survives the loop --------------------------------------------


def test_wiki_first_retrieval_precedes_any_tool_call(
    populated, store, vectors, embedder, settings
) -> None:
    from tests.unit.test_agent import RecordingVectors

    recording = RecordingVectors(vectors)
    scripted = StepLLM([ALWAYS_SEARCH, {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, recording, embedder, scripted, _loop(settings, n=4))

    agent.answer("retrieval augmented generation chunking")

    assert recording.queried[0] == settings.vectorize_gists_index
    # the fixture wiki answers strongly, so the only chunk query is the tool's
    assert recording.queried.count(settings.vectorize_chunks_index) == 1


# --- external search ---------------------------------------------------------


WEB_CALL = {"action": "search_web", "args": {"query": "outside"}, "reason": "not in kb"}


def test_web_search_is_never_offered_when_the_policy_is_off(
    populated, store, vectors, embedder, settings
) -> None:
    searcher = FakeWebSearcher()
    scripted = StepLLM([WEB_CALL, {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted,
                       _loop(settings, n=4, agent_web_search_policy="off"),
                       web_searcher=searcher)

    result = agent.answer("retrieval augmented generation chunking")

    schema = scripted.steps()[0]["schema"]
    assert "search_web" not in schema["properties"]["action"]["enum"]
    assert searcher.queries == []
    assert result.external_refs == []


def test_weak_policy_offers_web_search_only_after_the_rag_fallback(
    populated, store, vectors, embedder, settings
) -> None:
    searcher = FakeWebSearcher()
    scripted = StepLLM([{"action": "answer", "reason": "ok"}])
    strong_settings = _loop(settings, n=4, agent_web_search_policy="weak")
    agent = QueryAgent(store, vectors, embedder, scripted, strong_settings, web_searcher=searcher)

    result = agent.answer("retrieval augmented generation chunking")
    assert not result.used_rag_fallback
    assert "search_web" not in scripted.steps()[0]["schema"]["properties"]["action"]["enum"]

    # Now make the wiki weak: an agent whose confidence threshold nothing reaches.
    scripted2 = StepLLM([{"action": "answer", "reason": "ok"}])
    weak_agent = QueryAgent(store, vectors, embedder, scripted2, strong_settings,
                            web_searcher=searcher)
    weak_agent.wiki_confidence = 2.0
    result2 = weak_agent.answer("retrieval augmented generation chunking")
    assert result2.used_rag_fallback
    assert "search_web" in scripted2.steps()[0]["schema"]["properties"]["action"]["enum"]


def test_web_results_are_external_refs_and_never_citations(
    populated, store, vectors, embedder, settings
) -> None:
    searcher = FakeWebSearcher([ExternalRef(title="Out there", url="https://ex.org/p",
                                            snippet="an external page")])
    scripted = StepLLM([WEB_CALL, {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted,
                       _loop(settings, n=4, agent_web_search_policy="always"),
                       web_searcher=searcher)

    result = agent.answer("retrieval augmented generation chunking")

    assert searcher.queries == ["outside"]
    assert [r.url for r in result.external_refs] == ["https://ex.org/p"]
    assert all(agent.source_exists(c.source_id) for c in result.citations)
    assert not any("ex.org" in (c.url or "") for c in result.citations)
    generation = [c for c in scripted.calls if c["op"] == "answer_query"][-1]
    assert "External web results" in generation["prompt"]
    assert "https://ex.org/p" in generation["prompt"]


def test_web_searches_are_capped_per_question(populated, store, vectors, embedder, settings):
    searcher = FakeWebSearcher()
    second = {"action": "search_web", "args": {"query": "again"}, "reason": "more"}
    scripted = StepLLM([WEB_CALL, second, {"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted,
                       _loop(settings, n=4, agent_web_search_policy="always",
                             agent_max_web_searches=1),
                       web_searcher=searcher)

    agent.answer("retrieval augmented generation chunking")

    assert len(searcher.queries) == 1
    assert "search_web" not in scripted.steps()[1]["schema"]["properties"]["action"]["enum"]


def test_no_searcher_means_no_web_tool_even_when_policy_is_always(
    populated, store, vectors, embedder, settings
) -> None:
    scripted = StepLLM([{"action": "answer", "reason": "ok"}])
    agent = QueryAgent(store, vectors, embedder, scripted,
                       _loop(settings, n=4, agent_web_search_policy="always"))

    agent.answer("retrieval augmented generation chunking")

    assert "search_web" not in scripted.steps()[0]["schema"]["properties"]["action"]["enum"]


# --- run_id -------------------------------------------------------------------


def test_run_id_is_a_uuid_only_when_tracing_is_on(populated, store, vectors, embedder, settings):
    """The id is minted locally and handed to LangGraph as the root run id, so
    it needs no LangSmith round trip - but it is only *meaningful* (findable by
    POST /feedback) when tracing is on, so it is None otherwise."""
    import uuid

    off = QueryAgent(store, vectors, embedder, StepLLM([]), _loop(settings, n=0))
    assert off.answer("retrieval augmented generation chunking").run_id is None

    on_settings = _loop(settings, n=0, langsmith_tracing=True)
    on = QueryAgent(store, vectors, embedder, StepLLM([]), on_settings)
    run_id = on.answer("retrieval augmented generation chunking").run_id
    assert run_id and uuid.UUID(run_id)
