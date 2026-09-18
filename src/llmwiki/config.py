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

    # --- Cost guardrails ---
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
