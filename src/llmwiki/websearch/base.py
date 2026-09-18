"""Web search protocol - the query graph's optional ``search_web`` tool (L1).

Phase 1-D, design §4.9. A web result lives outside ``raw/``, so the domain
layer treats it as an :class:`~llmwiki.models.plan.ExternalRef` shown to the
reader, never as a :class:`~llmwiki.models.plan.Citation`. Capturing one into
the knowledge base is a separate, explicit ``ingest_source(url=...)``.

Same adapter posture as ``vector/`` and ``embedding/``: the protocol here, one
module per backend, the concrete class imported only inside ``factory``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llmwiki.models.plan import ExternalRef


@runtime_checkable
class WebSearcher(Protocol):
    """Query in, a handful of ranked external references out."""

    def search(self, query: str, k: int = 5) -> list[ExternalRef]:
        """Return up to ``k`` results, best first. Never raises on an empty result."""
        ...
