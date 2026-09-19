"""Query-side cost capture (Phase 2, plan §21.2 C2): ``MeteredLLM`` + ``collect_usage``.

The load-bearing claim is the last test: a collector opened around the real
query graph sees every node's call, because LangGraph copies the calling
context into the threads it runs nodes on.
"""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.config import Settings
from llmwiki.llm.fake import FakeLLM
from llmwiki.llm.metering import MeteredLLM, collect_usage, record_usage
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_meta
from llmwiki.wiki.compiler import Compiler


def test_nothing_is_recorded_outside_a_collector() -> None:
    llm = MeteredLLM(FakeLLM())
    llm.complete(op="answer_query", system="s", prompt="p")
    record_usage(CostRecord(op="x", model="m"))  # no sink open: silently dropped
    with collect_usage() as records:
        pass
    assert records == []


def test_calls_inside_a_collector_are_recorded_with_their_usage() -> None:
    llm = MeteredLLM(FakeLLM())
    with collect_usage() as records:
        llm.complete(op="summarize_source", system="s", prompt="p" * 40)
        llm.complete(op="plan_compile", system="s", prompt="p")
    assert [r.op for r in records] == ["summarize_source", "plan_compile"]
    assert records[0].input_tokens > 0


def test_the_wrapper_is_transparent() -> None:
    inner = FakeLLM({"agent_step": {"action": "answer", "args": {}}})
    llm = MeteredLLM(inner)
    response = llm.complete(op="agent_step", system="s", prompt="p")
    assert response.data["action"] == "answer"
    assert llm.calls is inner.calls, "unknown attributes resolve on the wrapped client"
    assert llm.inner is inner


def test_nested_collectors_both_see_the_inner_calls() -> None:
    llm = MeteredLLM(FakeLLM())
    with collect_usage() as outer:
        llm.complete(op="a", system="s", prompt="p")
        with collect_usage() as inner:
            llm.complete(op="b", system="s", prompt="p")
        llm.complete(op="c", system="s", prompt="p")
    assert [r.op for r in inner] == ["b"]
    assert [r.op for r in outer] == ["a", "b", "c"]


def test_a_collector_scope_ends_cleanly_after_an_exception() -> None:
    llm = MeteredLLM(FakeLLM())
    with pytest.raises(RuntimeError):
        with collect_usage():
            raise RuntimeError("boom")
    with collect_usage() as records:
        llm.complete(op="a", system="s", prompt="p")
    assert len(records) == 1


# --- through tools.answer, against the real graph --------------------------------


@pytest.fixture
def offline_cfg(tmp_path, monkeypatch) -> Settings:
    from llmwiki import factory

    cfg = Settings(
        _env_file=None,
        storage_backend="local",
        vector_backend="memory",
        embedding_backend="fake",
        llm_backend="fake",
        local_storage_path=tmp_path / "data",
        embedding_dim=64,
    )
    factory.reset()
    yield cfg
    factory.reset()


def _populate(cfg: Settings) -> str:
    from llmwiki import factory

    store, vectors = factory.object_store(cfg), factory.vector_store(cfg)
    doc = make_extracted_doc()
    store.put(
        raw_meta(doc.source_id),
        SourceMeta(source_id=doc.source_id, modality="web", sha256="0" * 64,
                   url=doc.url, title=doc.title).model_dump_json().encode(),
        "application/json",
    )
    compiler = Compiler(store, vectors, factory.embedder(cfg), factory.llm_client(cfg), cfg)
    compiler.compile_source(doc)
    return doc.source_id


def test_tools_answer_records_query_cost_through_the_real_graph(offline_cfg) -> None:
    """Every LLM call the graph makes lands in the ledger as kind="query", and the
    compile that populated the wiki is *not* counted again."""
    from llmwiki import factory, tools

    _populate(offline_cfg)
    before = factory.ledger(offline_cfg).read()
    assert before and all(r.kind == "compile" for r in before)

    result = tools.answer("retrieval augmented generation chunking", cfg=offline_cfg)

    after = factory.ledger(offline_cfg).read()
    query_records = [r for r in after if r.kind == "query"]
    assert query_records, "the answer_query call made inside the graph must be metered"
    assert {r.op for r in query_records} == {"answer_query"}
    assert len([r for r in after if r.kind == "compile"]) == len(before)
    assert result.cost_usd == sum(r.cost_usd for r in query_records)


def test_tools_judge_answer_records_eval_cost(offline_cfg) -> None:
    from llmwiki import factory, tools

    tools.judge_answer("q", "a", "context", cfg=offline_cfg)
    kinds = {r.kind for r in factory.ledger(offline_cfg).read()}
    assert kinds == {"eval"}
