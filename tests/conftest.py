"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import pytest
from tests.doubles import SpyObjectStore

from llmwiki.config import Settings
from llmwiki.embedding.fake import FakeEmbedder
from llmwiki.llm.fake import FakeLLM
from llmwiki.storage.local import LocalObjectStore
from llmwiki.vector.memory import MemoryVectorStore


@pytest.fixture(autouse=True)
def _isolate_llm_routing_config(monkeypatch, tmp_path) -> None:
    """No test may see a real config/providers.py or config/ops.py.

    ``_env_file=None`` (used throughout this suite) isolates a Settings
    instance from a real ``.env`` - but ``llm_providers_config``/
    ``llm_ops_config`` are plain ``Path`` fields whose *default* points at
    ``./config/providers.py``/``./config/ops.py``, checked for existence on
    disk by ``routing_config.load_routing_config`` regardless of
    ``_env_file``. A developer who has set those files up for real, in this
    same checkout (plan §19, R1), would otherwise flip every test that builds
    an unmodified default ``Settings()`` into routed mode - env vars still
    apply under ``_env_file=None``, so pointing them at a guaranteed-absent
    path here restores the isolation. A test that explicitly passes
    ``llm_providers_config=``/``llm_ops_config=`` is unaffected: an init
    kwarg always outranks an env var.
    """
    monkeypatch.setenv("LLMWIKI_PROVIDERS_CONFIG", str(tmp_path / "unused-providers.py"))
    monkeypatch.setenv("LLMWIKI_OPS_CONFIG", str(tmp_path / "unused-ops.py"))


@pytest.fixture(autouse=True)
def _isolate_agent_skills_dir(monkeypatch, tmp_path) -> None:
    """No test may see this repository's real ``skills/`` directory.

    Same reasoning as ``_isolate_llm_routing_config`` above, for the same
    reason: ``Settings.agent_skills_dir`` defaults to ``./skills``, resolved
    against the current working directory regardless of ``_env_file``, and
    this repository ships one (plan §19.5/§19.9, R5). Left unpinned, every
    test in this suite that builds a default ``Settings()`` would silently
    start exercising skill-invocation instead of the fixed-prompt path -
    exactly the regression R4/R5's exit criteria rule out
    (``tests/unit/test_agent_skill_invocation.py`` opts back in explicitly,
    by passing its own ``agent_skills_dir=``).
    """
    monkeypatch.setenv("AGENT_SKILLS_DIR", str(tmp_path / "unused-skills"))


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
