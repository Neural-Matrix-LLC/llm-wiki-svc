"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import pytest
from tests.doubles import SpyObjectStore

from llmwiki.config import Settings
from llmwiki.embedding.fake import FakeEmbedder
from llmwiki.llm.fake import FakeLLM
from llmwiki.storage.local import LocalObjectStore
from llmwiki.vector.memory import MemoryVectorStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pinned to the offline backends and a per-test storage root."""
    return Settings(
        storage_backend="local",
        vector_backend="memory",
        embedding_backend="fake",
        llm_backend="fake",
        local_storage_path=tmp_path / "data",
        embedding_dim=64,
        compile_max_pages=5,
        compile_candidate_pages=8,
        ingest_token_budget=60_000,
    )


@pytest.fixture
def store(settings) -> LocalObjectStore:
    return LocalObjectStore(root=settings.local_storage_path)


@pytest.fixture
def spy_store(store) -> SpyObjectStore:
    """A store that records every read and list, for the no-full-scan guard."""
    return SpyObjectStore(store)


@pytest.fixture
def embedder(settings) -> FakeEmbedder:
    return FakeEmbedder(dim=settings.embedding_dim)


@pytest.fixture
def vectors(settings) -> MemoryVectorStore:
    return MemoryVectorStore(dim=settings.embedding_dim)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
