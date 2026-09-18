"""The web-search backend seam (Phase 1-D): protocol, fake, and lazy loading."""

from __future__ import annotations

import sys

from llmwiki import factory
from llmwiki.config import Settings
from llmwiki.models.plan import ExternalRef
from llmwiki.websearch.base import WebSearcher
from llmwiki.websearch.fake import FakeWebSearcher


def test_fake_searcher_satisfies_the_protocol_and_records_queries() -> None:
    searcher = FakeWebSearcher([ExternalRef(title="a", url="https://a", snippet="x")] * 3)
    assert isinstance(searcher, WebSearcher)
    assert len(searcher.search("q", k=2)) == 2
    assert searcher.queries == ["q"]


def test_backend_none_builds_no_searcher_at_all() -> None:
    factory.reset()
    cfg = Settings(_env_file=None, web_search_backend="none")
    assert factory.web_searcher(cfg) is None


def test_backend_fake_builds_the_double() -> None:
    factory.reset()
    cfg = Settings(_env_file=None, web_search_backend="fake")
    assert isinstance(factory.web_searcher(cfg), FakeWebSearcher)


def test_tavily_is_not_imported_unless_selected(monkeypatch) -> None:
    """Same posture as the provider registry: the SDK is on disk, never eagerly loaded."""
    factory.reset()
    monkeypatch.delitem(sys.modules, "langchain_tavily", raising=False)
    import llmwiki.websearch.tavily  # noqa: F401 - importing the adapter module is free

    factory.web_searcher(Settings(_env_file=None, web_search_backend="fake"))
    assert "langchain_tavily" not in sys.modules


def test_tavily_requires_its_key() -> None:
    factory.reset()
    cfg = Settings(_env_file=None, web_search_backend="tavily", tavily_api_key="")
    try:
        factory.web_searcher(cfg)
    except RuntimeError as exc:
        assert "TAVILY_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a missing key must fail by name")


def test_tavily_adapter_maps_results_and_swallows_failures(monkeypatch) -> None:
    from llmwiki.websearch.tavily import TavilyWebSearcher

    searcher = TavilyWebSearcher.__new__(TavilyWebSearcher)

    class Tool:
        def invoke(self, args):
            return {"results": [
                {"title": "One", "url": "https://one", "content": "c1"},
                {"title": "no url", "content": "dropped"},
            ]}

    searcher._tool = Tool()
    refs = searcher.search("q")
    assert [(r.title, r.url, r.snippet) for r in refs] == [("One", "https://one", "c1")]

    class Broken:
        def invoke(self, args):
            raise ConnectionError("down")

    searcher._tool = Broken()
    assert searcher.search("q") == []
