"""Live Cloudflare round trips. Opt in with ``pytest -m integration``.

These are the tests that would have caught an embedding-dimension mismatch at M1
instead of at M4, and they are the only place the real API shapes are verified.
They require a populated ``.env`` and they cost money (Workers AI is metered).
"""

from __future__ import annotations

import time
import uuid

import pytest

from llmwiki.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cfg() -> Settings:
    settings = Settings()
    if settings.storage_backend != "r2" or settings.vector_backend != "vectorize":
        pytest.skip("integration tests need the real backends configured in .env")
    return settings


def test_r2_round_trip(cfg: Settings) -> None:
    from llmwiki.storage.r2 import R2ObjectStore

    cfg.require("r2_access_key_id", "r2_secret_access_key", "r2_endpoint_url")
    store = R2ObjectStore(
        endpoint_url=cfg.r2_endpoint_url,
        bucket=cfg.r2_bucket,
        access_key_id=cfg.r2_access_key_id,
        secret_access_key=cfg.r2_secret_access_key.get_secret_value(),
    )
    key = f"_integration/{uuid.uuid4().hex}.txt"
    store.put(key, b"round trip", "text/plain")
    try:
        assert store.get(key) == b"round trip"
        assert store.exists(key)
        assert key in store.list("_integration/")
    finally:
        store.delete(key)
    assert not store.exists(key)


def test_workers_ai_embedding_dimension_matches_settings(cfg: Settings) -> None:
    """EMBEDDING_DIM must equal what the model actually returns.

    A mismatch otherwise surfaces much later as an opaque Vectorize insert error.
    """
    from llmwiki.embedding.workers_ai import WorkersAIEmbedder

    cfg.require("cf_account_id", "cf_api_token")
    embedder = WorkersAIEmbedder(
        account_id=cfg.cf_account_id,
        api_token=cfg.cf_api_token.get_secret_value(),
        model=cfg.embedding_model,
        dim=cfg.embedding_dim,
    )
    vectors = embedder.embed(["retrieval augmented generation", "chunking strategies"])

    assert len(vectors) == 2
    assert len(vectors[0]) == cfg.embedding_dim


def test_vectorize_round_trip_is_eventually_visible(cfg: Settings) -> None:
    """Vectorize writes are eventually consistent, so this polls rather than asserting once."""
    from llmwiki.embedding.workers_ai import WorkersAIEmbedder
    from llmwiki.vector.vectorize import VectorizeStore

    cfg.require("cf_account_id", "cf_api_token")
    token = cfg.cf_api_token.get_secret_value()
    embedder = WorkersAIEmbedder(cfg.cf_account_id, token, cfg.embedding_model, cfg.embedding_dim)
    store = VectorizeStore(cfg.cf_account_id, token, probe_dim=cfg.embedding_dim)

    source_id = uuid.uuid4().hex[:16]
    vector_id = f"{source_id}:0"
    vector = embedder.embed(["a test vector about retrieval"])[0]
    store.upsert(cfg.vectorize_chunks_index, [vector_id], [vector], [{"source_id": source_id}])

    try:
        deadline = time.monotonic() + 60
        hits: list = []
        while time.monotonic() < deadline and not hits:
            hits = store.query(cfg.vectorize_chunks_index, vector, k=5,
                               where={"source_id": source_id})
            if not hits:
                time.sleep(3)
        assert hits, "upserted vector never became visible within 60s"
        assert hits[0].id == vector_id
    finally:
        store.delete_by_ids(cfg.vectorize_chunks_index, [vector_id])
