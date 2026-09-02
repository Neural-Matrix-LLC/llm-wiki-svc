"""Live Anthropic calls: structured output and prompt caching. Costs real money."""

from __future__ import annotations

import pytest

from llmwiki.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def llm():
    cfg = Settings()
    if cfg.llm_backend != "anthropic":
        pytest.skip("integration tests need LLM_BACKEND=anthropic")
    cfg.require("anthropic_api_key")
    from llmwiki.llm.anthropic_client import AnthropicLLM

    return AnthropicLLM(cfg.anthropic_api_key.get_secret_value(), cfg.llm_default_model)


def test_structured_output_comes_back_as_data(llm) -> None:
    from llmwiki.wiki.compiler import SUMMARY_SCHEMA

    response = llm.complete(
        op="summarize_source",
        system="You summarize sources for a research knowledge base.",
        prompt="Retrieval augmented generation grounds answers in retrieved documents.",
        schema=SUMMARY_SCHEMA,
    )

    assert response.data is not None, "forced tool use should always yield structured data"
    assert response.data.get("gist")
    assert response.usage is not None and response.usage.cost_usd > 0


def test_prompt_caching_actually_hits(llm) -> None:
    """A silent cache invalidator - a timestamp, an unsorted dict - makes caching do nothing.

    The only way to know it works is to assert a cache read on the second call.
    """
    system = ("You are a careful research assistant. " * 400).strip()

    llm.complete(op="probe", system=system, prompt="First call.", max_tokens=16)
    second = llm.complete(op="probe", system=system, prompt="Second call.", max_tokens=16)

    assert second.usage is not None
    assert second.usage.cache_read_tokens > 0, (
        "no cache read on the second identical prefix; prompt caching is not working"
    )
