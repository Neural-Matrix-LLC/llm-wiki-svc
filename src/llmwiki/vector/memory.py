"""In-process cosine-similarity vector store for unit tests and offline runs."""

from __future__ import annotations

import threading

import numpy as np

from llmwiki.models.chunk import SearchHit


class MemoryVectorStore:
    """Exact nearest-neighbour search over a numpy matrix. Fine to a few thousand vectors."""

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict]] = {}

    def _index(self, index: str) -> dict[str, dict]:
        return self._data.setdefault(index, {})

    def ensure_index(self, index: str) -> bool:
        """Indexes spring into existence on first use here; report whether this was it."""
        with self._lock:
            created = index not in self._data
            self._index(index)
        return created

    def index_names(self) -> list[str]:
        """Every index that has been touched - for tests and the offline scripts."""
        return sorted(self._data)

    def upsert(
        self,
        index: str,
        ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict],
    ) -> None:
        if not (len(ids) == len(vectors) == len(metadata)):
            raise ValueError("ids, vectors and metadata must be the same length")
        with self._lock:
            store = self._index(index)
            for vector_id, vector, meta in zip(ids, vectors, metadata, strict=True):
                if len(vector) != self.dim:
                    raise ValueError(
                        f"vector width {len(vector)} does not match index dimension {self.dim}"
                    )
                store[vector_id] = {"vector": np.asarray(vector, dtype=np.float32), "meta": meta}

    def query(
        self,
        index: str,
        vector: list[float],
        k: int = 5,
        where: dict | None = None,
    ) -> list[SearchHit]:
        with self._lock:
            rows = [
                (vector_id, row)
                for vector_id, row in self._index(index).items()
                if _matches(row["meta"], where)
            ]
        if not rows:
            return []
        matrix = np.stack([row["vector"] for _, row in rows])
        probe = np.asarray(vector, dtype=np.float32)
        denom = np.linalg.norm(matrix, axis=1) * np.linalg.norm(probe)
        denom[denom == 0] = 1e-9
        scores = (matrix @ probe) / denom
        order = np.argsort(-scores)[:k]
        hits = []
        for position in order:
            vector_id, row = rows[int(position)]
            meta = dict(row["meta"])
            hits.append(
                SearchHit(
                    id=vector_id,
                    score=float(scores[int(position)]),
                    origin="wiki" if meta.get("slug") else "chunk",
                    text=meta.get("text", ""),
                    slug=meta.get("slug"),
                    source_id=meta.get("source_id"),
                    metadata=meta,
                )
            )
        return hits

    def delete_by_source(self, index: str, source_id: str) -> int:
        with self._lock:
            store = self._index(index)
            doomed = [
                key for key, row in store.items()
                if row["meta"].get("source_id") == source_id
            ]
            for key in doomed:
                del store[key]
            return len(doomed)


def _matches(meta: dict, where: dict | None) -> bool:
    if not where:
        return True
    return all(meta.get(key) == value for key, value in where.items())
