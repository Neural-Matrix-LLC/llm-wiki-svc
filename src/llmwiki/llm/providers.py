"""Which LangChain chat model backs each ``LLM_PROVIDER`` value.

The registry is data.  Every provider import happens inside :func:`build`, so
importing this module pulls in no provider SDK at all: ``pip install llmwiki``
stays free of them, and a missing extra can only break the provider that needs
it.  ``tests/unit/test_providers.py`` asserts both properties.

All five integrations accept the same constructor keywords - ``model``,
``api_key``, ``base_url`` and a token cap - which is why the spec is nearly
free of per-provider spellings.  That was verified against the installed
classes rather than assumed, and ``tests/unit/test_providers.py`` re-checks it
for whichever integrations happen to be present.

Anthropic is deliberately absent: it keeps its native adapter in
``anthropic_client.py``, which is where prompt caching, forced-tool structured
output and measured cost accounting live (implement-plan-v1.4.md 7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The keywords every registered chat model accepts.
MODEL_ARG = "model"
API_KEY_ARG = "api_key"
BASE_URL_ARG = "base_url"
TEMPERATURE_ARG = "temperature"


@dataclass(frozen=True)
class ProviderSpec:
    """How to import one provider's LangChain chat model."""

    module: str  # import package the integration ships
    cls: str  # the BaseChatModel subclass inside it
    extra: str  # pip install "llmwiki[<extra>]"
    distribution: str  # what that extra installs, named in the error
    # ChatOpenAI deprecated ``max_tokens`` in favour of the OpenAI spelling;
    # every other integration still wants ``max_tokens``.
    max_tokens_arg: str = "max_tokens"


REGISTRY: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        "langchain_openai",
        "ChatOpenAI",
        "openai",
        "langchain-openai",
        max_tokens_arg="max_completion_tokens",
    ),
    "google": ProviderSpec(
        "langchain_google_genai",
        "ChatGoogleGenerativeAI",
        "google",
        "langchain-google-genai",
    ),
    "nvidia": ProviderSpec(
        "langchain_nvidia_ai_endpoints",
        "ChatNVIDIA",
        "nvidia",
        "langchain-nvidia-ai-endpoints",
    ),
    "deepseek": ProviderSpec(
        "langchain_deepseek", "ChatDeepSeek", "deepseek", "langchain-deepseek"
    ),
    "openrouter": ProviderSpec(
        "langchain_openrouter", "ChatOpenRouter", "openrouter", "langchain-openrouter"
    ),
}


def load_class(provider: str) -> type:
    """Import one provider's chat model class, or explain which extra is missing."""
    spec = REGISTRY[provider]
    try:
        module = __import__(spec.module, fromlist=[spec.cls])
    except ImportError as exc:  # the extra was not installed
        raise RuntimeError(
            f"LLM_PROVIDER={provider!r} needs {spec.distribution}, which is not "
            f'installed. Run: pip install "llmwiki[{spec.extra}]"'
        ) from exc
    return getattr(module, spec.cls)  # type: ignore[no-any-return]


def build(
    provider: str,
    *,
    model: str,
    api_key: str,
    base_url: str = "",
    max_tokens: int = 2048,
    temperature: float = 1.0,
) -> Any:
    """Construct the chat model for ``provider``.

    Returns a ``BaseChatModel``; typed as ``Any`` so that neither this module
    nor its importers need langchain-core present to be imported.  The token
    cap and temperature are constructor arguments rather than per-call ones
    because the integrations disagree about invoke-time keywords but all
    accept them here - ``temperature`` unlike ``max_tokens`` needs no
    per-provider spelling override (checked by
    ``test_registry_keywords_match_the_installed_class``).
    """
    spec = REGISTRY[provider]
    cls = load_class(provider)
    kwargs: dict[str, Any] = {
        MODEL_ARG: model,
        API_KEY_ARG: api_key,
        spec.max_tokens_arg: max_tokens,
        TEMPERATURE_ARG: temperature,
    }
    if base_url:
        kwargs[BASE_URL_ARG] = base_url
    return cls(**kwargs)
