"""Lexical index protocol - the same shape as ``VectorStore``, keyed by the same index names.

Phase 2 (design v1.4 §4.10.2, plan §21.2 B1). Dense retrieval misses the
exact-term question - an identifier, a rare token, a product name - that a
keyword index answers trivially. This protocol mirrors :class:`VectorStore`
so one index *name* (``llmwiki-chunks``, ``llmwiki-gists-ml`` ...) addresses
both halves of a scope, and the retrieval seam fuses the two lists by rank.

Backends: ``sqlite`` (FTS5, one file per index, the default), ``memory`` (a
small BM25 for tests and offline runs) and ``none`` (no index is built;
retrieval is dense-only, byte for byte the pre-Phase-2 path).
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from llmwiki.models.chunk import SearchHit

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens; punctuation inside identifiers (``bge-m3``, ``v2.1``) is kept."""
    return [token.lower().strip("._-") for token in _TOKEN.findall(text)
            if token.strip("._-")]


def hit_from_row(vector_id: str, score: float, text: str, meta: dict) -> SearchHit:
    """Build the retrieval-seam hit shape: lexical score kept, no dense score yet."""
    return SearchHit(
        id=vector_id,
        score=score,
        origin="wiki" if meta.get("slug") else "chunk",
        text=text,
        slug=meta.get("slug"),
        source_id=meta.get("source_id"),
        metadata=dict(meta),
        lexical_score=score,
    )


@runtime_checkable
class LexicalIndex(Protocol):
    """Keyword search over the same ids the vector store holds."""

    def upsert(self, index: str, ids: list[str], texts: list[str], metadata: list[dict]) -> None:
        """Insert or replace documents. ``texts`` is the full searchable text."""
        ...

    def query(self, index: str, text: str, k: int = 5) -> list[SearchHit]:
        """Best-``k`` documents for a free-text query, most relevant first.

        An index that was never built returns ``[]`` - never an error - so a
        fresh box serves dense-only results until ``llmwiki lexical rebuild``.
        """
        ...

    def delete_by_source(self, index: str, source_id: str) -> int:
        """Remove every document of one source. Returns how many were removed."""
        ...

    def reset(self, index: str) -> None:
        """Drop the index's contents (a rebuild starts here)."""
        ...

    def count(self, index: str) -> int:
        """How many documents the index holds; 0 for one that does not exist."""
        ...
