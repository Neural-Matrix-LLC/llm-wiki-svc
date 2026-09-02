"""Builds concrete adapters from settings.

Imports of concrete adapters are function-local on purpose: an offline run must
never import ``boto3`` or ``anthropic`` at all, and a missing optional dependency
must not break an unrelated command.

Sits outside the layer ladder - only ``tools.py`` and the entry points call it.
Domain classes take protocols and never reach in here (plan 4.3).
"""

from __future__ import annotations

from typing import Any

from llmwiki.config import Settings
from llmwiki.config import settings as default_settings
from llmwiki.embedding.base import Embedder
from llmwiki.llm.base import LLMClient
from llmwiki.storage.base import ObjectStore
from llmwiki.vector.base import VectorStore

# Adapters are cached per distinct configuration, not per Settings instance:
# Settings is mutable (tests adjust caps on it) and therefore unhashable, so the
# cache key is the small set of fields that actually determine which client to
# build. Two Settings that differ only in, say, COMPILE_MAX_PAGES share a client.
_CACHE: dict[tuple, Any] = {}


def _cached(key: tuple, build: Any) -> Any:
    if key not in _CACHE:
        _CACHE[key] = build()
    return _CACHE[key]


def object_store(cfg: Settings | None = None) -> ObjectStore:
    """Return the configured object store. Cached: one client per configuration."""
    cfg = cfg or default_settings
    key = ("store", cfg.storage_backend, str(cfg.local_storage_path), cfg.r2_bucket,
           cfg.r2_endpoint_url)
    return _cached(key, lambda: _build_object_store(cfg))


def _build_object_store(cfg: Settings) -> ObjectStore:
    if cfg.storage_backend == "local":
        from llmwiki.storage.local import LocalObjectStore

        return LocalObjectStore(root=cfg.local_storage_path)

    cfg.require("r2_access_key_id", "r2_secret_access_key", "r2_endpoint_url", "r2_bucket")
    from llmwiki.storage.r2 import R2ObjectStore

    return R2ObjectStore(
        endpoint_url=cfg.r2_endpoint_url,
        bucket=cfg.r2_bucket,
        access_key_id=cfg.r2_access_key_id,
        secret_access_key=cfg.r2_secret_access_key.get_secret_value(),
    )


def vector_store(cfg: Settings | None = None) -> VectorStore:
    """Return the configured vector store."""
    cfg = cfg or default_settings
    key = ("vectors", cfg.vector_backend, cfg.cf_account_id, cfg.embedding_dim)
    return _cached(key, lambda: _build_vector_store(cfg))


def _build_vector_store(cfg: Settings) -> VectorStore:
    if cfg.vector_backend == "memory":
        from llmwiki.vector.memory import MemoryVectorStore

        return MemoryVectorStore(dim=cfg.embedding_dim)

    cfg.require("cf_account_id", "cf_api_token")
    from llmwiki.vector.vectorize import VectorizeStore

    return VectorizeStore(
        account_id=cfg.cf_account_id,
        api_token=cfg.cf_api_token.get_secret_value(),
        probe_dim=cfg.embedding_dim,
    )


def embedder(cfg: Settings | None = None) -> Embedder:
    """Return the configured embedder."""
    cfg = cfg or default_settings
    key = ("embedder", cfg.embedding_backend, cfg.embedding_model, cfg.embedding_dim,
           cfg.cf_account_id)
    return _cached(key, lambda: _build_embedder(cfg))


def _build_embedder(cfg: Settings) -> Embedder:
    if cfg.embedding_backend == "fake":
        from llmwiki.embedding.fake import FakeEmbedder

        return FakeEmbedder(dim=cfg.embedding_dim)

    cfg.require("cf_account_id", "cf_api_token")
    from llmwiki.embedding.workers_ai import WorkersAIEmbedder

    return WorkersAIEmbedder(
        account_id=cfg.cf_account_id,
        api_token=cfg.cf_api_token.get_secret_value(),
        model=cfg.embedding_model,
        dim=cfg.embedding_dim,
    )


def llm_client(cfg: Settings | None = None) -> LLMClient:
    """Return the configured LLM client."""
    cfg = cfg or default_settings
    key = ("llm", cfg.llm_provider, cfg.llm_model, cfg.llm_base_url)
    return _cached(key, lambda: _build_llm_client(cfg))


def _build_llm_client(cfg: Settings) -> LLMClient:
    if cfg.llm_provider == "fake":
        from llmwiki.llm.fake import FakeLLM

        return FakeLLM()

    cfg.require("llm_api_key")
    from llmwiki import __version__

    if cfg.llm_provider == "anthropic":
        # Native adapter: prompt caching and measured cost live here, so
        # Anthropic does not go through LangChain (plan-v1.4 7.5).
        from llmwiki.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(
            api_key=cfg.llm_api_key.get_secret_value(),
            default_model=cfg.llm_model,
            base_url=cfg.llm_base_url or None,
            version=__version__,
        )

    from llmwiki.llm import providers

    if cfg.llm_provider not in providers.REGISTRY:
        raise RuntimeError(
            f"LLM_PROVIDER={cfg.llm_provider!r} has no adapter in this package. "
            f"Available: anthropic, fake, {', '.join(sorted(providers.REGISTRY))}."
        )

    from llmwiki.llm.langchain_client import LangChainLLM

    def build(model: str, max_tokens: int) -> Any:
        return providers.build(
            cfg.llm_provider,
            model=model,
            api_key=cfg.llm_api_key.get_secret_value(),
            base_url=cfg.llm_base_url,
            max_tokens=max_tokens,
        )

    return LangChainLLM(build, default_model=cfg.llm_model, version=__version__)


def reset() -> None:
    """Drop every cached adapter.

    Needed when the environment changes inside a running process - the CLI's
    ``--offline`` flag and the test suite both do this.
    """
    _CACHE.clear()
