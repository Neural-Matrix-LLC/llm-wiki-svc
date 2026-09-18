"""Live LangSmith: push the fixture golden set and run one experiment over it.

Needs only ``LANGSMITH_API_KEY`` - the answers come from the offline fake
adapters, so this costs LangSmith quota, not LLM tokens. Uses a throwaway
dataset name so the real ``LANGSMITH_EVAL_DATASET`` is never touched.
"""

from __future__ import annotations

import uuid

import pytest

from llmwiki import factory
from llmwiki.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def offline_with_langsmith(tmp_path, monkeypatch) -> Settings:
    base = Settings()
    if not base.langsmith_api_key.get_secret_value():
        pytest.skip("needs LANGSMITH_API_KEY")
    cfg = Settings(
        _env_file=None,
        storage_backend="local", vector_backend="memory", embedding_backend="fake",
        llm_provider="fake", web_search_backend="none",
        local_storage_path=tmp_path / "data", embedding_dim=64,
        langsmith_api_key=base.langsmith_api_key.get_secret_value(),
        langsmith_endpoint=base.langsmith_endpoint,
        langsmith_eval_dataset=f"llmwiki-integration-{uuid.uuid4().hex[:8]}",
    )
    monkeypatch.setattr("llmwiki.config.settings", cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
    factory.reset()
    yield cfg
    from langsmith import Client

    client = Client(api_key=cfg.langsmith_api_key.get_secret_value(),
                    api_url=cfg.langsmith_endpoint or None)
    if client.has_dataset(dataset_name=cfg.langsmith_eval_dataset):
        client.delete_dataset(dataset_name=cfg.langsmith_eval_dataset)
    factory.reset()


def test_push_then_evaluate_the_fixture_set(offline_with_langsmith) -> None:
    from pathlib import Path

    from llmwiki import tools
    from llmwiki.eval import evaluators as ev
    from llmwiki.eval.dataset import FIXTURE_DATASET, load_examples, push_dataset
    from llmwiki.eval.run import run_experiment

    cfg = offline_with_langsmith
    repo = Path(__file__).resolve().parents[2]
    for name, mime in (("sample.pdf", "application/pdf"), ("sample.html", "text/html")):
        ref = tools.ingest_source(file=(repo / "tests/fixtures" / name).read_bytes(),
                                  filename=name, mime=mime, cfg=cfg)
        assert tools.process_source(ref.source_id, cfg=cfg).state == "done"

    examples = load_examples(repo / FIXTURE_DATASET)
    dataset_id = push_dataset(examples, cfg.langsmith_eval_dataset,
                              api_key=cfg.langsmith_api_key.get_secret_value(),
                              endpoint=cfg.langsmith_endpoint)
    assert dataset_id

    results = run_experiment(cfg.langsmith_eval_dataset, list(ev.DETERMINISTIC),
                             experiment_prefix="integration", cfg=cfg)
    rows = list(results)
    assert len(rows) == len(examples)
    for row in rows:
        scores = {r.key: r.score for r in row["evaluation_results"]["results"]}
        assert scores["citations_resolve"] == 1.0, scores
