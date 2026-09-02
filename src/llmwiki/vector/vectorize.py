"""Cloudflare Vectorize v2 REST client.

Three things this adapter has to respect (plan 6.4):

* upsert takes **ndjson**, one JSON object per line, not a JSON array;
* metadata indexes must exist *before* any insert that will be filtered on them,
  so ``scripts/bootstrap_indexes.py`` creates them at setup time;
* writes are eventually consistent - a query immediately after an upsert may not
  see it, which is why the integration test polls.
"""

from __future__ import annotations

import json

import httpx

from llmwiki.models.chunk import SearchHit

UPSERT_BATCH = 500
TIMEOUT_S = 60.0


class VectorizeStore:
    """Vector store backed by Cloudflare Vectorize."""

    def __init__(self, account_id: str, api_token: str, probe_dim: int = 768) -> None:
        self.account_id = account_id
        self.probe_dim = probe_dim
        self._client = httpx.Client(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/v2",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=TIMEOUT_S,
        )

    def _result(self, response: httpx.Response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise RuntimeError(f"Vectorize error: {payload.get('errors')}")
        result: dict = payload.get("result") or {}
        return result

    def upsert(
        self,
        index: str,
        ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict],
    ) -> None:
        for start in range(0, len(ids), UPSERT_BATCH):
            stop = start + UPSERT_BATCH
            lines = [
                json.dumps({"id": vector_id, "values": values, "metadata": meta})
                for vector_id, values, meta in zip(
                    ids[start:stop], vectors[start:stop], metadata[start:stop], strict=True
                )
            ]
            body = "\n".join(lines).encode("utf-8")
            self._result(
                self._client.post(
                    f"/indexes/{index}/upsert",
                    content=body,
                    headers={"Content-Type": "application/x-ndjson"},
                )
            )

    def query(
        self,
        index: str,
        vector: list[float],
        k: int = 5,
        where: dict | None = None,
    ) -> list[SearchHit]:
        body: dict = {"vector": vector, "topK": k, "returnMetadata": "all"}
        if where:
            body["filter"] = {key: {"$eq": value} for key, value in where.items()}
        result = self._result(self._client.post(f"/indexes/{index}/query", json=body))
        hits = []
        for match in result.get("matches", []):
            meta = match.get("metadata") or {}
            hits.append(
                SearchHit(
                    id=match["id"],
                    score=float(match.get("score", 0.0)),
                    origin="wiki" if meta.get("slug") else "chunk",
                    text=meta.get("text", ""),
                    slug=meta.get("slug"),
                    source_id=meta.get("source_id"),
                    metadata=meta,
                )
            )
        return hits

    def delete_by_source(self, index: str, source_id: str) -> int:
        """Delete every vector belonging to a source.

        Vectorize has no delete-by-filter, so ids are resolved first with a
        metadata-filtered query.  The probe vector is arbitrary - the filter, not
        the distance, decides what comes back - and ``topK`` is capped at 100 per
        call, so this loops until a pass returns nothing new.  Filtering on
        ``source_id`` requires the metadata index created in bootstrap (6.4).
        """
        deleted = 0
        seen: set[str] = set()
        probe = [0.0] * self.probe_dim
        while True:
            matches = self.query(index, probe, k=100, where={"source_id": source_id})
            ids = [hit.id for hit in matches if hit.id not in seen]
            if not ids:
                return deleted
            seen.update(ids)
            deleted += self.delete_by_ids(index, ids)

    def delete_by_ids(self, index: str, ids: list[str]) -> int:
        """Delete specific vector ids. Preferred over :meth:`delete_by_source`."""
        if not ids:
            return 0
        self._result(self._client.post(f"/indexes/{index}/delete_by_ids", json={"ids": ids}))
        return len(ids)

    def close(self) -> None:
        self._client.close()
