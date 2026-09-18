"""The eval package (Phase 1-D): golden set, evaluators, local runner, feedback promotion.

No LangSmith, no network: ``run_local`` and every evaluator are exercised
against the offline adapters through ``tools`` with a per-test Settings, and
one test asserts ``import llmwiki.eval`` never imports ``langsmith``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from llmwiki import factory, tools
from llmwiki.config import Settings
from llmwiki.eval import evaluators as ev
from llmwiki.eval.dataset import DatasetError, Example, append_examples, load_examples
from llmwiki.eval.feedback import example_from_correction
from llmwiki.eval.run import Row, answer_target, experiment_metadata, run_local

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "eval" / "answer_quality.jsonl"


@pytest.fixture
def offline(tmp_path, monkeypatch) -> Settings:
    """Offline adapters reached *through the factory*, as tools.* does.

    Not the shared ``settings`` fixture: that one reads a real ``.env``, whose
    explicitly-set LLM_PROVIDER outranks the fixture's ``llm_backend="fake"``
    alias - harmless for tests that construct FakeLLM themselves, fatal (a real
    network call) for anything that goes through ``factory.llm_client``.
    """
    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_provider="fake", web_search_backend="none",
        local_storage_path=tmp_path / "data", embedding_dim=64,
    )
    monkeypatch.setattr("llmwiki.config.settings", cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
    factory.reset()
    yield cfg
    factory.reset()


# --- dataset -------------------------------------------------------------------


def test_the_shipped_golden_set_loads_and_skips_comments() -> None:
    examples = load_examples(FIXTURE)
    assert len(examples) >= 4
    assert all(ex.question for ex in examples)
    assert examples[0].inputs == {"question": examples[0].question}
    assert set(examples[0].outputs) == {"expected_sources", "must_mention"}


def test_a_malformed_line_is_reported_with_its_line_number(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"question": "ok"}\n{"no_question": 1}\n', encoding="utf-8")
    with pytest.raises(DatasetError) as excinfo:
        load_examples(path)
    assert ":2:" in str(excinfo.value)


def test_append_examples_writes_valid_jsonl(tmp_path) -> None:
    path = tmp_path / "set.jsonl"
    n = append_examples(path, [Example(question="q1"), Example(question="q2", must_mention=["x"])])
    assert n == 2
    assert [ex.question for ex in load_examples(path)] == ["q1", "q2"]


# --- evaluators (pure) ----------------------------------------------------------


def test_expected_source_cited_is_the_fraction_cited() -> None:
    score = ev.expected_source_cited({}, {"citations": ["a"]}, {"expected_sources": ["a", "b"]})
    assert score["score"] == 0.5 and "b" in score["comment"]
    assert ev.expected_source_cited({}, {"citations": []}, {})["score"] == 1.0


def test_must_mention_is_case_insensitive() -> None:
    score = ev.must_mention({}, {"text": "Chunking matters."},
                            {"must_mention": ["chunking", "RAG"]})
    assert score["score"] == 0.5 and "RAG" in score["comment"]


def test_tool_calls_is_a_metric_counting_steps() -> None:
    score = ev.tool_calls({}, {"steps": [{"tool": "get_page"}, {"tool": "search_chunks"}]}, {})
    assert score["score"] == 2.0 and "get_page" in score["comment"]


def test_citations_resolve_uses_the_real_store(offline) -> None:
    assert ev.citations_resolve({}, {"citations": ["0000000000000000-nope"]}, {})["score"] == 0.0
    assert ev.citations_resolve({}, {"citations": []}, {})["score"] == 1.0


def test_gated_thresholds_cover_exactly_the_deterministic_pass_fail_evaluators() -> None:
    assert set(ev.GATED) == {"citations_resolve", "expected_source_cited", "must_mention"}
    assert {e.__name__ for e in ev.DETERMINISTIC} >= set(ev.GATED)


# --- local runner over the offline adapters --------------------------------------


@pytest.fixture
def offline_corpus(offline):
    """Ingest the two fixture documents through the real pipeline, offline."""
    settings = offline
    fixtures = FIXTURE.parents[1]
    for name, mime in (("sample.pdf", "application/pdf"), ("sample.html", "text/html")):
        ref = tools.ingest_source(file=(fixtures / name).read_bytes(), filename=name,
                                  mime=mime, cfg=settings)
        assert tools.process_source(ref.source_id, cfg=settings).state == "done"
    return settings


def test_answer_target_flattens_the_answer(offline_corpus) -> None:
    out = answer_target({"question": "What is retrieval-augmented generation?"}, cfg=offline_corpus)
    assert set(out) >= {"text", "citations", "used_rag_fallback", "context", "steps", "run_id"}
    assert out["citations"] and all(isinstance(c, str) for c in out["citations"])
    assert out["run_id"] is None, "tracing is off in tests"


def test_run_local_scores_the_shipped_golden_set_green_offline(offline_corpus) -> None:
    result = run_local(load_examples(FIXTURE), list(ev.DETERMINISTIC), cfg=offline_corpus)
    assert [row.failed for row in result.rows] == [[] for _ in result.rows]
    assert result.failures == []
    assert result.mean("citations_resolve") == 1.0
    assert result.mean("tool_calls") == 0.0, "the offline double never enters the loop"


def test_run_local_reports_a_failing_example(offline_corpus) -> None:
    wrong = Example(question="What is retrieval-augmented generation?",
                    expected_sources=["0000000000000000-not-a-real-source"])
    result = run_local([wrong], list(ev.DETERMINISTIC), cfg=offline_corpus)
    assert result.failures and result.failures[0].failed == ["expected_source_cited"]


def test_row_failed_only_names_gated_evaluators() -> None:
    row = Row(example=Example(question="q"), outputs={}, scores=[
        {"key": "tool_calls", "score": 9.0, "comment": ""},
        {"key": "must_mention", "score": 0.0, "comment": ""},
    ])
    assert row.failed == ["must_mention"]


def test_experiment_metadata_names_the_bounds_and_version(offline) -> None:
    meta = experiment_metadata(offline.model_copy(update={"agent_max_tool_calls": 2}))
    assert meta["agent_max_tool_calls"] == 2
    assert meta["llmwiki_version"] and "git_sha" in meta


# --- feedback promotion (pure half) -------------------------------------------------


def test_example_from_correction_extracts_sources_and_terms(offline) -> None:
    settings = offline
    real = "7b2f6aed523349f5-sample"
    from llmwiki.models.source import SourceMeta
    from llmwiki.storage.layout import raw_meta
    factory.object_store(settings).put(
        raw_meta(real), SourceMeta(source_id=real, modality="pdf", sha256="0" * 64,
                                   title="s").model_dump_json().encode(), "application/json",
    )
    comment = f"Should cite {real} and 1111111111111111-ghost. must mention: chunking, recall"
    example = example_from_correction("the question", comment)
    assert example.expected_sources == [real], "a source id that does not exist is dropped"
    assert example.must_mention == ["chunking", "recall"]
    assert example.notes.startswith("promoted from feedback")


# --- import hygiene ---------------------------------------------------------------


def test_importing_the_eval_package_does_not_import_langsmith(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "langsmith" or name.startswith("langsmith."):
            monkeypatch.delitem(sys.modules, name)
    for name in list(sys.modules):
        if name.startswith("llmwiki.eval"):
            monkeypatch.delitem(sys.modules, name)
    import llmwiki.eval.dataset  # noqa: F401
    import llmwiki.eval.evaluators  # noqa: F401
    import llmwiki.eval.feedback  # noqa: F401
    import llmwiki.eval.run  # noqa: F401
    assert "langsmith" not in sys.modules
