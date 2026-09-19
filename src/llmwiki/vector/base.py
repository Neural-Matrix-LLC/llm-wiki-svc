"""Vector store protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llmwiki.models.chunk import SearchHit


@runtime_checkable
class VectorStore(Protocol):
    """Upsert and query dense vectors. Implementations must be safe to call concurrently."""

    def upsert(
        self,
        index: str,
        ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict],
    ) -> None:
        """Insert or replace vectors by id. Ids are deterministic, so this is idempotent."""
        ...

    def query(
        self,
        index: str,
        vector: list[float],
        k: int = 5,
        where: dict | None = None,
    ) -> list[SearchHit]:
        """Return the ``k`` nearest neighbours, optionally filtered by metadata."""
        ...

    def delete_by_source(self, index: str, source_id: str) -> int:
        """Remove every vector belonging to a source. Returns the count deleted."""
        ...

    def ensure_index(self, index: str) -> bool:
        """Create ``index`` if it does not exist; return True when it was created.

        Phase 2 (plan §21.2 A2): registering a domain creates its own pair of
        indexes, named by ``layout.domain_index_name``. Idempotent, and must also
        create whatever filterable metadata properties the backend needs
        *before* the first insert - in-memory stores have nothing to do.
        """
        ...
