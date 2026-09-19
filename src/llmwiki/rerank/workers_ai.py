"""Cloudflare Workers AI reranker: bge-reranker-base on the embedder's CF_* credentials."""

from __future__ import annotations

import httpx

TIMEOUT_S = 30.0


class WorkersAIReranker:
    """``POST /accounts/{id}/ai/run/{model}`` with ``{"query", "contexts", "top_k"}``."""

    def __init__(self, account_id: str, api_token: str, model: str) -> None:
        self.account_id = account_id
        self.model = model
        self._client = httpx.Client(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=TIMEOUT_S,
        )

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        response = self._client.post(
            f"/ai/run/{self.model}",
            json={"query": query, "contexts": [{"text": text} for text in documents],
                  "top_k": min(top_n, len(documents))},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise RuntimeError(f"Workers AI error: {payload.get('errors')}")
        rows = payload["result"]["response"]
        ranked = [(int(row["id"]), float(row.get("score", 0.0))) for row in rows]
        ranked.sort(key=lambda pair: -pair[1])
        return ranked[:top_n]

    def close(self) -> None:
        self._client.close()
