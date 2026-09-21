"""Cloudflare Workers AI embeddings (``@cf/baai/bge-base-en-v1.5``, 768-dim).

Not an Anthropic capability, so this is a plain REST client - the rule against
raw HTTP applies to the Messages API, not to Cloudflare.
"""

from __future__ import annotations

import httpx

BATCH_SIZE = 100
TIMEOUT_S = 60.0


class WorkersAIEmbedder:
    """Embeddings via ``POST /accounts/{id}/ai/run/{model}``."""

    def __init__(self, account_id: str, api_token: str, model: str, dim: int) -> None:
        self.account_id = account_id
        self.model = model
        self._dim = dim
        self._client = httpx.Client(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=TIMEOUT_S,
        )

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            response = self._client.post(f"/ai/run/{self.model}", json={"text": batch})
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success", True):
                raise RuntimeError(f"Workers AI error: {payload.get('errors')}")
            data = payload["result"]["data"]
            for vector in data:
                if len(vector) != self._dim:
                    raise RuntimeError(
                        f"embedding dimension mismatch: model returned {len(vector)}, "
                        f"EMBEDDING_DIM is {self._dim}. Recreate the Vectorize indexes "
                        "or correct the setting (see implement-plan.md Part I section 6.3)."
                    )
            vectors.extend(data)
        return vectors

    def close(self) -> None:
        self._client.close()
