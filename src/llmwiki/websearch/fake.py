"""Offline web-search double: canned results, records every query."""

from __future__ import annotations

from llmwiki.models.plan import ExternalRef


class FakeWebSearcher:
    """Returns the configured results for any query. No network, no key."""

    def __init__(self, results: list[ExternalRef] | None = None) -> None:
        self.results = results if results is not None else [
            ExternalRef(
                title="Offline web result",
                url="https://example.org/offline-result",
                snippet="A canned external result from FakeWebSearcher.",
            )
        ]
        self.queries: list[str] = []

    def search(self, query: str, k: int = 5) -> list[ExternalRef]:
        self.queries.append(query)
        return self.results[:k]
