"""The only module in the package that reads the environment.

Everything else receives a :class:`Settings` instance.  Secrets are typed as
``SecretStr`` so a stray log line or repr cannot leak a key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Providers the generic LLM contract accepts. "anthropic" has a native adapter
# (prompt caching, measured cost); the rest are reached through LangChain and
# need their extra installed - see llmwiki.llm.providers.REGISTRY, which must
# stay in step with this list. "fake" is the offline double.
# See implement-plan-v1.4.md 7.5 and 7.6.
Provider = Literal[
    "anthropic",
    "fake",
    "openai",
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

    # Wiki-specific, deliberately NOT part of the shared contract: the compiler
    # escalates patch generation to a stronger model (plan-1.1 D5).
    compile_executor_model: str = "claude-haiku-4-5"

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
