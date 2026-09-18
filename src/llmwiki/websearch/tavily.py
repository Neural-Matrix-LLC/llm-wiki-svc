"""Tavily web search via the LangChain ``langchain-tavily`` integration.

The key is passed explicitly (never read from ``os.environ`` here - the same
posture as every other adapter: ``Settings`` is the only reader of the
environment). ``langchain_tavily`` is imported inside ``__init__`` so that
importing this module, or ``factory``, costs nothing until the backend is
actually selected; ``tests/unit/test_websearch.py`` asserts that.
"""

from __future__ import annotations

import logging
from typing import Any

from llmwiki.models.plan import ExternalRef

logger = logging.getLogger(__name__)


class TavilyWebSearcher:
    """``WebSearcher`` over ``langchain_tavily.TavilySearch``."""

    def __init__(self, api_key: str, *, max_results: int = 5, search_depth: str = "basic") -> None:
        from langchain_tavily import TavilySearch
        from langchain_tavily._utilities import TavilySearchAPIWrapper
        from pydantic import SecretStr

        self._tool = TavilySearch(
            max_results=max_results,
            search_depth=search_depth,
            include_answer=False,
            include_raw_content=False,
            include_images=False,
            api_wrapper=TavilySearchAPIWrapper(tavily_api_key=SecretStr(api_key)),
        )

    def search(self, query: str, k: int = 5) -> list[ExternalRef]:
        try:
            raw: Any = self._tool.invoke({"query": query})
        except Exception:  # a failed external lookup must not fail the answer
            logger.warning("search_web: Tavily request failed for %r", query, exc_info=True)
            return []
        results = raw.get("results", []) if isinstance(raw, dict) else []
        refs: list[ExternalRef] = []
        for item in results[:k]:
            url = item.get("url")
            if not url:
                continue
            refs.append(
                ExternalRef(
                    title=str(item.get("title", "")),
                    url=str(url),
                    snippet=str(item.get("content", ""))[:600],
                )
            )
        return refs
