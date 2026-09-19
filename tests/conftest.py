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


@pytest.fixture(autouse=True)
def _isolate_query_tool_loop(monkeypatch) -> None:
    """No test enters the query graph's tool loop unless it opts in.

    Third instance of the same pattern (Phase 1-D, design §4.9).
    ``AGENT_MAX_TOOL_CALLS=0`` reproduces the pre-graph call sequence exactly:
    the ``agent`` node is skipped, so a scripted double that hands out
    responses by call index (``test_agent_skill_invocation.SequencedLLM``)
    still sees its first response consumed by the call it was written for.
    ``tests/unit/test_agent_graph.py`` opts back in per test with
    ``settings.model_copy(update={"agent_max_tool_calls": n})``.
    """
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "0")


@pytest.fixture(autouse=True)
def _isolate_ingest_worker(monkeypatch) -> None:
    """No test runs the threaded ingest worker unless it opts in.

    Fourth instance of the pattern (Phase 2, plan §21.2 C3). ``WORKER_MODE=
    inline`` makes ``tools.enqueue_source`` process in the caller, which is
    what keeps ``TestClient``'s run-background-tasks-before-returning
    semantics - and every route test written against them - deterministic.
    ``tests/unit/test_worker.py`` opts back in with ``worker_mode="threads"``.
    """
    monkeypatch.setenv("WORKER_MODE", "inline")


@pytest.fixture(autouse=True)
def _isolate_hybrid_retrieval(monkeypatch) -> None:
    """No test gets hybrid retrieval unless it opts in (Phase 2, plan §21.2 B1/X2).

    ``LEXICAL_BACKEND=none`` keeps the retrieval seam dense-only - byte for
    byte the Phase 0/1 path the wiki-first and citation tests were written
    against. ``tests/unit/test_lexical.py`` and ``test_retrieval.py`` opt in
    with ``lexical_backend="memory"`` (or a temp-dir ``sqlite``).
    """
    monkeypatch.setenv("LEXICAL_BACKEND", "none")
    monkeypatch.setenv("RERANKER_BACKEND", "none")


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
        worker_mode="inline",
        lexical_backend="none",
        reranker_backend="none",
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
