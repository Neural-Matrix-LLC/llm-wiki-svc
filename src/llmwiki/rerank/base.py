"""Reranker protocol (Phase 2, design v1.4 §4.10.2, plan §21.2 B3/B5).

A reranker reads the query and a handful of candidate *texts* (gist one-liners,
chunk snippets - metadata that is already in hand, never a page body) and
returns a relevance ordering. It runs after rank fusion, inside each retrieval
layer, on at most ``RERANK_MAX_CANDIDATES`` candidates, and any failure falls
back to the fused order - never a hard failure on the query path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """Score candidates against a query."""

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """``(index_into_documents, score)`` pairs, best first, at most ``top_n``.

        Scores are the backend's own scale (bge-reranker: a sigmoid-ish 0..1);
        only the *order* is used by the retrieval seam.
        """
        ...
