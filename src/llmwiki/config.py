"""The only module in the package that reads the environment.

Everything else receives a :class:`Settings` instance.  Secrets are typed as
``SecretStr`` so a stray log line or repr cannot leak a key.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s"
_LOG_DATEFMT = "%H:%M:%S"
_logging_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Attach one stream handler to the root logger, at ``level``.

    Lives here rather than in its own module because the transport layers
    (api/, cli.py) may only import ``tools``/``models``/``config``/``wiki``/
    ``factory`` (``test_layering.py::test_transport_layer_only_calls_tools``)
    - both call this once, at process start, with ``Settings.log_level``.

    Idempotent: a second call (a second ``Settings`` built by a test, or
    ``serve --reload`` re-importing the app) only adjusts the level, it never
    stacks a second handler or duplicates log lines.
    """
    global _logging_configured
    resolved = getattr(logging, level.upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"LOG_LEVEL={level!r} is not a recognized logging level")

    root = logging.getLogger()
    root.setLevel(resolved)
    if not _logging_configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        root.addHandler(handler)
        _logging_configured = True
    else:
        for existing_handler in root.handlers:
            existing_handler.setLevel(resolved)


# Providers the generic LLM contract accepts. "anthropic" has a native adapter
# (prompt caching, measured cost); the rest are reached through LangChain and
# need their extra installed - see llmwiki.llm.providers.REGISTRY, which must
# stay in step with this list. "fake" is the offline double.
# See implement-plan-v1.4.md 7.5 and 7.6.
Provider = Literal[
    "anthropic",
    "fake",
    "openai",
    "vllm",
    "llamacpp",
    "google",
    "nvidia",
    "deepseek",
    "openrouter",
]


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM (provider-generic: the agentkit-llm env contract, plan-v1.4 7.6) ---
    llm_provider: Provider = "anthropic"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-haiku-4-5"
    llm_base_url: str = ""
    # Applied uniformly by every adapter (native Anthropic and every
    # LangChain-routed provider) and passed explicitly at every call site -
    # same idiom as llm_model, never a hidden per-adapter default.
    llm_max_tokens: int = 2048
    llm_temperature: float = 1.0

    # --- Application-specific LLM routing (design v1.4 §4.8, plan §19) --------
    # Deliberately outside the shared agentkit-llm contract above: multi-
    # provider credentials and per-op model routing are this application's
    # concern, not the reusable LLM layer's. Absence of the *file* at
    # llm_providers_config (not of this setting) is what selects the
    # single-provider fallback path built from the fields above - see
    # factory._build_llm_client. Replaces the old, single-purpose
    # COMPILE_EXECUTOR_MODEL: a stronger model for create_page/patch_page is
    # now just those ops' own rows in config/ops.py.
    #
    # ``validation_alias`` is required here: pydantic-settings would otherwise
    # auto-derive the env var name from the field name alone
    # (LLM_PROVIDERS_CONFIG, no "WIKI") - the deliberately-prefixed
    # LLMWIKI_PROVIDERS_CONFIG/LLMWIKI_OPS_CONFIG documented in .env.example
    # and implement-plan-v1.4.md §19.8 would otherwise be silently ignored.
    # The plain field name is kept as a second alias so direct construction
    # (``Settings(llm_providers_config=...)``, used throughout the test suite)
    # is unaffected.
    llm_providers_config: Path = Field(
        default=Path("./config/providers.py"),
        validation_alias=AliasChoices("LLMWIKI_PROVIDERS_CONFIG", "llm_providers_config"),
    )
    llm_ops_config: Path = Field(
        default=Path("./config/ops.py"),
        validation_alias=AliasChoices("LLMWIKI_OPS_CONFIG", "llm_ops_config"),
    )

    # Query-agent skill discovery directory (design v1.4 §4.8.2, plan §19.5),
    # outside src/llmwiki like the two paths above. Not yet read by any code
    # path - lands with R5; declared now so the location is locked in .env
    # rather than improvised later.
    agent_skills_dir: Path = Field(default=Path("./skills"))

    # --- Query graph bounds (Phase 1-D, design §4.9) -------------------------
    # The query agent is a bounded ReAct loop (agent/graph.py). Every bound is
    # enforced in code, never by the prompt: 0 disables the tool loop entirely
    # and reproduces the pre-graph single-call sequence exactly.
    agent_max_tool_calls: int = 4
    # Whether the model is ever *offered* the search_web tool: "off" (never;
    # no key needed), "weak" (only when the wiki had no strong hit and the
    # chunk fallback ran), "always". Gate is applied when the action schema is
    # built, so the model cannot pick a tool the policy withholds.
    agent_web_search_policy: Literal["off", "weak", "always"] = "off"
    agent_max_web_searches: int = 1

    # --- Web search backend (Phase 1-D) -----------------------------------
    # "none" builds no searcher at all (the tool is not even constructed);
    # "tavily" needs TAVILY_API_KEY; "fake" is the offline double.
    web_search_backend: Literal["none", "tavily", "fake"] = "none"
    tavily_api_key: SecretStr = SecretStr("")

    # --- Cloudflare ---
    cf_account_id: str = ""
    cf_api_token: SecretStr = SecretStr("")
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "llmwiki"
    r2_endpoint_url: str = ""
    vectorize_chunks_index: str = "llmwiki-chunks"
    vectorize_gists_index: str = "llmwiki-gists"
    embedding_model: str = "@cf/baai/bge-base-en-v1.5"
    embedding_dim: int = 768

    # --- Service ---
    ingest_api_token: SecretStr = SecretStr("changeme")
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Capture channels (Phase 1, KB design §5): each is optional and
    # independently disabled by leaving its secret unset - api/app.py mounts
    # a channel's router only when llmwiki.channels.<name>.build_router(cfg)
    # returns non-None, the same degrade-gracefully posture as the MCP mount. ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_webhook_secret: SecretStr = SecretStr("")
    mailgun_signing_key: SecretStr = SecretStr("")

    # --- Observability: stdlib logging ---
    # Consumed by llmwiki.logging_config.configure_logging, called once by each
    # entry point (cli.main, api/app.py). Independent of LangSmith tracing
    # below - this is process-local log output, not a shipped trace.
    log_level: str = "INFO"

    # --- Observability: LangSmith tracing (optional, off by default) ---
    # Independent of LLM_PROVIDER: factory.py wraps the native Anthropic client
    # directly and exports these for LangChain's own auto-instrumentation to
    # pick up on every other provider. Complements, not replaces, the measured
    # cost ledger (models/plan.py CostRecord -> wiki/_meta/cost.jsonl).
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "llmwiki"
    langsmith_endpoint: str = ""
    # The LangSmith dataset scripts/eval_answer.py pushes to and evaluates
    # against (Phase 1-D). Tracing is not required for local (--offline) eval.
    langsmith_eval_dataset: str = "llmwiki-answer-quality"

    # --- Domains (Phase 2, design §4.10.1, plan §21.2 A4/A6) -------------------
    # "auto": a source with no explicit domain is routed by one route_domain
    # call - but only when at least one domain is registered; a general-only
    # registry makes no call at all. "off": never call the router; everything
    # not explicitly filed goes to general.
    domain_routing: Literal["auto", "off"] = "auto"
    # Below this confidence the router's pick is demoted to general and kept
    # as suggested_domain.
    domain_route_min_confidence: float = 0.6

    # Query scope when no domain= is given (plan §21.2 A6): "all" fans out over
    # every registered domain with no model call; "routed" spends one
    # route_domain call to pick at most QUERY_MAX_DOMAINS; "general" searches
    # only the general wiki. With nothing registered all three are identical.
    query_domain_policy: Literal["all", "routed", "general"] = "all"
    query_max_domains: int = 2
    # The scheduled per-domain overview page reads at most this many page
    # bodies (plan §21.2 A8) - one strong-model call per domain per run.
    synthesis_max_pages: int = 10

    # --- Hybrid retrieval (Phase 2, design §4.10.2, plan §21.2 B1-B4) -----------
    # A lexical (keyword) index beside the dense one, fused by reciprocal rank.
    # "sqlite": FTS5, one file per index under LEXICAL_DB_PATH, rebuildable from
    # raw/ (`llmwiki lexical rebuild`); "memory": tests/offline; "none": dense
    # only - byte for byte the pre-Phase-2 retrieval.
    lexical_backend: Literal["sqlite", "memory", "none"] = "sqlite"
    lexical_db_path: Path | None = None  # default: {LOCAL_STORAGE_PATH}/lexical
    # Candidates taken from each list (dense, lexical) per scope before fusion.
    hybrid_pool_k: int = 20
    # A cross-encoder reorders the fused pool (at most RERANK_MAX_CANDIDATES)
    # inside each layer; the wiki-confidence gate is unaffected. "workers_ai"
    # reuses the CF_* credentials; "fake" is the offline double; "none" skips.
    reranker_backend: Literal["workers_ai", "fake", "none"] = "workers_ai"
    reranker_model: str = "@cf/baai/bge-reranker-base"
    rerank_max_candidates: int = 40

    # --- Selective multimodal (Phase 2, design §4.10.4, plan §21.2 D1-D4) --------
    # "off": today's behaviour - images fail at extraction, scanned PDFs raise.
    # "auto": PDF pages with fewer than VISION_MIN_CHARS_PER_PAGE chars of text
    # layer, or (figure-heavy) pages whose images cover VISION_IMAGE_AREA_RATIO
    # of the page, plus uploaded images, are described by the describe_image
    # op - at most VISION_MAX_PAGES_PER_SOURCE per source. "always": every
    # PDF page, up to the cap. The op must route to a vision-capable model.
    vision_mode: Literal["off", "auto", "always"] = "off"
    vision_max_pages_per_source: int = 8
    vision_min_chars_per_page: int = 200
    vision_image_area_ratio: float = 0.25

    # --- Ingest worker (Phase 2, plan §21.2 C3/C5) ------------------------------
    # "threads": a bounded pool of WORKER_THREADS drains what REST and the
    # capture channels submit, one domain compiling at a time (a lock per
    # domain). "inline": run in the caller - the CLI, the MCP tool and the test
    # suite. WORKER_THREADS also bounds concurrent LLM calls from ingest;
    # providers with tight rate limits want 2.
    worker_mode: Literal["threads", "inline"] = "threads"
    worker_threads: int = 4
    # How often a source parked under the monthly hard cap is retried.
    worker_resume_interval_s: float = 60.0

    # --- Cost alerts and the hard cap (Phase 2, plan §21.2 C4/C5) -----------------
    # USD thresholds over the measured ledger; 0 = off. The two alerts log a
    # WARNING and notify once per day/month. The hard cap also pauses
    # post-capture processing (extract/route/embed/compile) until the month
    # rolls over or the cap is raised - capture, search and answers keep working.
    cost_alert_daily_usd: float = 0.0
    cost_alert_monthly_usd: float = 0.0
    cost_hard_cap_monthly_usd: float = 0.0
    # Where alerts go besides the log: "telegram" reuses TELEGRAM_BOT_TOKEN and
    # needs ALERT_TELEGRAM_CHAT_ID; "fake" is the offline double.
    notify_backend: Literal["log", "telegram", "fake"] = "log"
    alert_telegram_chat_id: str = ""

    # --- Cost guardrails ---
    # Phase 2 (plan §21.2 C1): which process is writing the cost ledger. Each
    # writer gets its own daily key under wiki/_meta/cost/, so the API, the
    # CLI and the cron jobs never read-modify-write the same object. The CLI
    # and the scripts set this themselves (COST_WRITER=cli, backfill, eval...);
    # "api" is what the service process is.
    cost_writer: str = "api"
    compile_max_pages: int = 5
    compile_candidate_pages: int = 8
    ingest_token_budget: int = 60_000
    chunk_size_chars: int = 3200
    chunk_overlap_chars: int = 400

    # --- Backend selection ---
    storage_backend: Literal["r2", "local"] = "r2"
    vector_backend: Literal["vectorize", "memory"] = "vectorize"
    embedding_backend: Literal["workers_ai", "fake"] = "workers_ai"
    local_storage_path: Path = Field(default=Path("./.data"))

    # --- Deprecated LLM aliases, removed at N4 (plan-v1.4 7.6) ---
    # Real fields, not properties, so that both a pre-rename ``.env`` and a
    # pre-rename constructor kwarg keep working.  ``extra="ignore"`` would
    # silently swallow an unknown kwarg, which is exactly how a test fixture
    # asking for the fake backend would end up building a real client.
    llm_backend: Provider | None = None
    anthropic_api_key: SecretStr | None = None
    llm_default_model: str | None = None

    @model_validator(mode="after")
    def _resolve_deprecated_aliases(self) -> Settings:
        """Map the old LLM variable names onto the generic ones, then mirror back.

        An explicitly-set canonical name always wins over its deprecated alias;
        anything a settings source supplied counts as explicitly set.  After
        resolution both spellings hold the same value, so call sites and tests
        written against either one keep reading the truth.
        """
        chosen = self.model_fields_set
        if self.llm_backend is not None and "llm_provider" not in chosen:
            self.llm_provider = self.llm_backend
        if self.anthropic_api_key is not None and "llm_api_key" not in chosen:
            self.llm_api_key = self.anthropic_api_key
        if self.llm_default_model is not None and "llm_model" not in chosen:
            self.llm_model = self.llm_default_model

        self.llm_backend = self.llm_provider
        self.anthropic_api_key = self.llm_api_key
        self.llm_default_model = self.llm_model
        return self

    @property
    def lexical_root(self) -> Path:
        """Where the FTS5 files live: ``LEXICAL_DB_PATH`` or ``{LOCAL_STORAGE_PATH}/lexical``."""
        return self.lexical_db_path or (self.local_storage_path / "lexical")

    def require(self, *fields: str) -> None:
        """Fail loudly, and by name, when a backend's credentials are absent.

        Adapters call this in their constructor so a misconfigured deployment
        reports ``R2_ACCESS_KEY_ID is not set`` rather than a 403 from boto3.
        """
        missing = []
        for field in fields:
            value = getattr(self, field)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value or value == "changeme":
                missing.append(field.upper())
        if missing:
            raise RuntimeError(
                f"missing required configuration: {', '.join(missing)}. "
                "Copy .env.example to .env and fill these in (see implement-plan.md section 6)."
            )


def load_settings() -> Settings:
    """Build a fresh Settings from the current environment.

    Used by tests and by the CLI after it has adjusted ``os.environ``; ordinary
    code should use the module-level ``settings``.
    """
    return Settings()


settings = load_settings()
