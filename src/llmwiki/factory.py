"""Builds concrete adapters from settings.

Imports of concrete adapters are function-local on purpose: an offline run must
never import ``boto3`` or ``anthropic`` at all, and a missing optional dependency
must not break an unrelated command.

Sits outside the layer ladder - only ``tools.py`` and the entry points call it.
Domain classes take protocols and never reach in here (plan 4.3).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from llmwiki.config import Settings
from llmwiki.config import settings as default_settings
from llmwiki.embedding.base import Embedder
from llmwiki.llm.base import LLMClient
from llmwiki.storage.base import ObjectStore
from llmwiki.vector.base import VectorStore
from llmwiki.websearch.base import WebSearcher

logger = logging.getLogger(__name__)

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
    logger.info("object store: backend=%s", cfg.storage_backend)
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
    logger.info("vector store: backend=%s", cfg.vector_backend)
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
    logger.info("embedder: backend=%s model=%s", cfg.embedding_backend, cfg.embedding_model)
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


def web_searcher(cfg: Settings | None = None) -> WebSearcher | None:
    """Return the configured web searcher, or ``None`` when the backend is ``none``.

    ``None`` is a real value here, not a failure: the query graph then never
    constructs its ``search_web`` tool (Phase 1-D, design §4.9), so a
    deployment without a search key is byte-for-byte the pre-web-search agent.
    """
    cfg = cfg or default_settings
    if cfg.web_search_backend == "none":
        return None
    key = ("websearch", cfg.web_search_backend)
    return _cached(key, lambda: _build_web_searcher(cfg))


def _build_web_searcher(cfg: Settings) -> WebSearcher:
    logger.info("web searcher: backend=%s", cfg.web_search_backend)
    if cfg.web_search_backend == "fake":
        from llmwiki.websearch.fake import FakeWebSearcher

        return FakeWebSearcher()

    cfg.require("tavily_api_key")
    from llmwiki.websearch.tavily import TavilyWebSearcher

    return TavilyWebSearcher(api_key=cfg.tavily_api_key.get_secret_value())


def lexical_index(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """The lexical half of hybrid retrieval, or ``None`` for ``LEXICAL_BACKEND=none``.

    ``None`` is a real value (Phase 2, plan §21.2 B1): the pipeline then
    writes no keyword rows and the retrieval seam is dense-only, byte for byte
    the pre-Phase-2 path.
    """
    cfg = cfg or default_settings
    if cfg.lexical_backend == "none":
        return None
    key = ("lexical", cfg.lexical_backend, str(cfg.lexical_root))
    return _cached(key, lambda: _build_lexical_index(cfg))


def _build_lexical_index(cfg: Settings):  # type: ignore[no-untyped-def]
    logger.info("lexical index: backend=%s", cfg.lexical_backend)
    if cfg.lexical_backend == "memory":
        from llmwiki.lexical.memory import MemoryLexicalIndex

        return MemoryLexicalIndex()

    from llmwiki.lexical.sqlite import SqliteLexicalIndex, fts5_available

    if not fts5_available():
        raise RuntimeError(
            "LEXICAL_BACKEND=sqlite needs an sqlite3 built with FTS5, and this interpreter's "
            "is not; set LEXICAL_BACKEND=none (dense-only) or use a Python with FTS5"
        )
    return SqliteLexicalIndex(cfg.lexical_root)


def reranker(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """The reranker, or ``None`` for ``RERANKER_BACKEND=none`` (Phase 2, plan §21.2 B5)."""
    cfg = cfg or default_settings
    if cfg.reranker_backend == "none":
        return None
    key = ("reranker", cfg.reranker_backend, cfg.reranker_model, cfg.cf_account_id)
    return _cached(key, lambda: _build_reranker(cfg))


def _build_reranker(cfg: Settings):  # type: ignore[no-untyped-def]
    logger.info("reranker: backend=%s model=%s", cfg.reranker_backend, cfg.reranker_model)
    if cfg.reranker_backend == "fake":
        from llmwiki.rerank.fake import FakeReranker

        return FakeReranker()
    cfg.require("cf_account_id", "cf_api_token")
    from llmwiki.rerank.workers_ai import WorkersAIReranker

    return WorkersAIReranker(
        account_id=cfg.cf_account_id,
        api_token=cfg.cf_api_token.get_secret_value(),
        model=cfg.reranker_model,
    )


def llm_client(cfg: Settings | None = None) -> LLMClient:
    """Return the configured LLM client, wrapped for query-side cost capture.

    The wrapper (``llm/metering.py``, Phase 2 plan §21.2 C2) is transparent
    unless a ``collect_usage()`` block is open, which only ``tools.answer`` and
    ``tools.judge_answer`` do - the compiler keeps its own accounting.
    """
    cfg = cfg or default_settings
    key = ("llm", cfg.llm_provider, cfg.llm_model, cfg.llm_base_url,
           str(cfg.llm_providers_config), str(cfg.llm_ops_config))
    return _cached(key, lambda: _metered(_build_llm_client(cfg)))


def _metered(client: LLMClient) -> LLMClient:
    from llmwiki.llm.metering import MeteredLLM

    return MeteredLLM(client)


def ledger(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """The cost ledger for this process's writer name (Phase 2, plan §21.2 C1)."""
    cfg = cfg or default_settings
    from llmwiki.wiki.ledger import CostLedger

    return CostLedger(object_store(cfg), writer=cfg.cost_writer)


def _configure_langsmith(cfg: Settings) -> None:
    """Export LangSmith's env vars so every provider's tracing sees them.

    The native Anthropic adapter is wrapped explicitly (below); every
    LangChain-routed provider auto-instruments off these same env vars, which
    is why this exports rather than passes arguments. A no-op unless
    ``LANGSMITH_TRACING`` is set - importing or configuring nothing is the
    default cost.
    """
    if not cfg.langsmith_tracing:
        return
    cfg.require("langsmith_api_key")
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = cfg.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = cfg.langsmith_project
    if cfg.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = cfg.langsmith_endpoint


def _construct_provider_client(
    provider: str,
    *,
    api_key: str,
    base_url: str,
    default_model: str,
    default_max_tokens: int,
    default_temperature: float,
    tracing: bool,
) -> LLMClient:
    """Build one concrete adapter for ``provider``.

    No logging, no ``os.environ`` reads, no ``Settings`` - shared by the
    single-provider fallback path below and the per-op router
    (``_build_routed_llm_client``, implement-plan-v1.4.md §19.3).
    """
    if provider == "fake":
        from llmwiki.llm.fake import FakeLLM

        return FakeLLM()

    from llmwiki import __version__

    if provider == "anthropic":
        # Native adapter: prompt caching and measured cost live here, so
        # Anthropic does not go through LangChain (plan-v1.4 7.5).
        from llmwiki.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(
            api_key=api_key,
            default_model=default_model,
            base_url=base_url or None,
            version=__version__,
            tracing=tracing,
            default_max_tokens=default_max_tokens,
            default_temperature=default_temperature,
        )

    from llmwiki.llm import providers

    if provider not in providers.REGISTRY:
        raise RuntimeError(
            f"provider {provider!r} has no adapter in this package. "
            f"Available: anthropic, fake, {', '.join(sorted(providers.REGISTRY))}."
        )

    from llmwiki.llm.langchain_client import LangChainLLM

    def build(model: str, max_tokens: int, temperature: float) -> Any:
        return providers.build(
            provider, model=model, api_key=api_key, base_url=base_url,
            max_tokens=max_tokens, temperature=temperature,
        )

    return LangChainLLM(
        build, default_model=default_model, version=__version__,
        default_max_tokens=default_max_tokens, default_temperature=default_temperature,
    )


def _build_routed_llm_client(cfg: Settings, routing: Any) -> LLMClient:
    """Multi-provider, per-op path - built only when config/{providers,ops}.py
    both exist (design v1.4 §4.8.1, plan §19.2-19.3)."""
    used = sorted(routing.providers_in_use())
    logger.info("llm client: routed mode ops=%d providers=%s", len(routing.ops), used)
    _configure_langsmith(cfg)

    clients: dict[str, LLMClient] = {}
    for provider in used:
        creds = routing.providers[provider]
        clients[provider] = _construct_provider_client(
            provider,
            api_key=creds.api_key,
            base_url=creds.base_url,
            # The router (llm/router.py) always passes model=/max_tokens=/
            # temperature= explicitly from the matching config/ops.py row, so
            # these construction-time defaults are never actually read.
            default_model="",
            default_max_tokens=2048,
            default_temperature=1.0,
            tracing=cfg.langsmith_tracing,
        )

    from llmwiki.llm.router import RoutingLLMClient

    router = RoutingLLMClient(routing, clients)
    # Phase 2 (plan §21.2 D1): fail at startup, by name, if the vision op is
    # routed to an adapter that cannot take images.
    if "describe_image" in routing.ops and not router.supports_vision("describe_image"):
        raise RuntimeError(
            f"config/ops.py routes describe_image to provider "
            f"{routing.ops['describe_image'].provider!r}, whose adapter has no describe(); "
            "route it to a vision-capable provider or set VISION_MODE=off"
        )
    return router


def _build_llm_client(cfg: Settings) -> LLMClient:
    from llmwiki.llm import routing_config

    routing = routing_config.load_routing_config(cfg.llm_providers_config, cfg.llm_ops_config)
    if routing is not None:
        return _build_routed_llm_client(cfg, routing)

    # --- fallback: single provider, driven entirely by Settings ------------
    if cfg.llm_provider == "fake":
        logger.info("llm client: provider=fake (offline double, no key, no cost)")
        return _construct_provider_client(
            "fake", api_key="", base_url="", default_model=cfg.llm_model,
            default_max_tokens=cfg.llm_max_tokens, default_temperature=cfg.llm_temperature,
            tracing=False,
        )

    cfg.require("llm_api_key")
    _configure_langsmith(cfg)

    if cfg.llm_provider == "anthropic":
        logger.info(
            "llm client: provider=anthropic model=%s tracing=%s",
            cfg.llm_model, cfg.langsmith_tracing,
        )
    else:
        logger.info(
            "llm client: provider=%s model=%s (via langchain) tracing=%s",
            cfg.llm_provider, cfg.llm_model, cfg.langsmith_tracing,
        )

    return _construct_provider_client(
        cfg.llm_provider,
        api_key=cfg.llm_api_key.get_secret_value(),
        base_url=cfg.llm_base_url,
        default_model=cfg.llm_model,
        default_max_tokens=cfg.llm_max_tokens,
        default_temperature=cfg.llm_temperature,
        tracing=cfg.langsmith_tracing,
    )


def reset() -> None:
    """Drop every cached adapter.

    Needed when the environment changes inside a running process - the CLI's
    ``--offline`` flag and the test suite both do this.
    """
    _CACHE.clear()


def compile_worker(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """This process's ingest worker (Phase 2, plan §21.2 C3) - one per configuration."""
    cfg = cfg or default_settings
    key = ("worker", cfg.worker_mode, cfg.worker_threads, cfg.storage_backend,
           str(cfg.local_storage_path))
    return _cached(key, lambda: _build_compile_worker(cfg))


def _build_compile_worker(cfg: Settings):  # type: ignore[no-untyped-def]
    from llmwiki import tools
    from llmwiki.pipeline.ingest import IngestPipeline
    from llmwiki.pipeline.worker import CompileWorker

    logger.info("ingest worker: mode=%s threads=%d", cfg.worker_mode, cfg.worker_threads)
    store = object_store(cfg)
    pipeline = IngestPipeline(store, vector_store(cfg), embedder(cfg), llm_client(cfg), cfg,
                              lexical=lexical_index(cfg))
    return CompileWorker(
        cfg, store,
        process=lambda source_id: tools.process_source(source_id, cfg=cfg),
        set_status=pipeline.set_status,
        capped=lambda: tools.processing_capped(cfg=cfg),
    )


def notifier(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """Where cost alerts go (Phase 2, plan §21.2 C4)."""
    cfg = cfg or default_settings
    key = ("notifier", cfg.notify_backend, cfg.alert_telegram_chat_id)
    return _cached(key, lambda: _build_notifier(cfg))


def _build_notifier(cfg: Settings):  # type: ignore[no-untyped-def]
    if cfg.notify_backend == "fake":
        from llmwiki.notify.fake import FakeNotifier

        return FakeNotifier()
    if cfg.notify_backend == "telegram":
        cfg.require("telegram_bot_token", "alert_telegram_chat_id")
        from llmwiki.notify.telegram import TelegramNotifier

        return TelegramNotifier(cfg.telegram_bot_token.get_secret_value(),
                                cfg.alert_telegram_chat_id)
    from llmwiki.notify.log import LogNotifier

    return LogNotifier()


def cost_alerts(cfg: Settings | None = None):  # type: ignore[no-untyped-def]
    """The running-totals guard behind alerts and the hard cap (Phase 2, plan §21.2 C4/C5)."""
    cfg = cfg or default_settings
    key = ("alerts", cfg.storage_backend, str(cfg.local_storage_path), cfg.cost_writer,
           cfg.cost_alert_daily_usd, cfg.cost_alert_monthly_usd, cfg.cost_hard_cap_monthly_usd,
           cfg.notify_backend)
    return _cached(key, lambda: _build_cost_alerts(cfg))


def _build_cost_alerts(cfg: Settings):  # type: ignore[no-untyped-def]
    from llmwiki.wiki.alerts import CostAlerts

    def waiting() -> int:
        status = compile_worker(cfg).status()
        return sum(status.queued.values()) + len(status.parked)

    return CostAlerts(object_store(cfg), ledger(cfg), notifier(cfg), cfg, queued=waiting)
