"""Full pipeline against real backends: capture a URL, compile it, answer from it."""

from __future__ import annotations

import pytest

from llmwiki.config import Settings

pytestmark = pytest.mark.integration

LIVE_URL = "https://en.wikipedia.org/wiki/Retrieval-augmented_generation"


@pytest.fixture(scope="module")
def cfg() -> Settings:
    settings = Settings()
    if settings.llm_backend == "fake":
        pytest.skip("integration tests need the real backends configured in .env")
    return settings


def test_capture_compile_and_answer(cfg: Settings) -> None:
    from llmwiki import factory, tools

    factory.reset()
    status = tools.ingest_now(url=LIVE_URL, cfg=cfg)
    assert status.state == "done", status.error
    assert status.chunk_count > 0

    concepts = tools.list_concepts(cfg=cfg)
    assert concepts, "compilation produced no pages"

    answer = tools.answer("What is retrieval-augmented generation?", cfg=cfg)
    assert answer.citations
    for citation in answer.citations:
        assert tools.source_exists(citation.source_id, cfg=cfg)

    cost = tools.cost_summary(cfg=cfg)
    assert cost.total_usd > 0, "real backends recorded zero cost; the ledger is not wired up"
