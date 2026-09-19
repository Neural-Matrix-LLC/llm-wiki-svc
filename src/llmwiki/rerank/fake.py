"""Deterministic reranker double: term overlap, for tests and offline runs."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def tokenize(text: str) -> set[str]:
    """Lower-cased word tokens. Deliberately local: L1 peers never import each other."""
    return {token.lower().strip("._-") for token in _WORD.findall(text) if token.strip("._-")}


class FakeReranker:
    """Ranks by the share of query tokens each document contains; ties keep input order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        self.calls.append((query, len(documents), top_n))
        terms = tokenize(query)
        scored = []
        for position, document in enumerate(documents):
            words = tokenize(document)
            score = len(terms & words) / len(terms) if terms else 0.0
            scored.append((position, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:top_n]
