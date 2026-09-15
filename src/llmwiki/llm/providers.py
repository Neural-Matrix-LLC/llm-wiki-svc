"""Which LangChain chat model backs each ``LLM_PROVIDER`` value.

The registry is data.  Every provider SDK is a core dependency of llmwiki (see
``pyproject.toml``), but the actual import happens inside :func:`build`, so
merely importing this module never eagerly loads a provider SDK - it stays
cheap even though every SDK is on disk.  ``tests/unit/test_providers.py``
asserts both properties.

All five underlying LangChain integrations accept the same constructor
keywords - ``model``, ``api_key``, ``base_url`` and a token cap - which is why
the spec is nearly free of per-provider spellings.  That was verified against
the installed classes rather than assumed, and ``tests/unit/test_providers.py``
re-checks it for whichever integrations happen to be present.

``vllm`` and ``llamacpp`` are two of those seven registry *entries* rather than
two more integrations: both point at ``langchain_openai.ChatOpenAI``, the same
class ``openai`` uses, since a self-hosted vLLM or llama.cpp server exposes the
same OpenAI-compatible route (2026-09-14, Phase 1 local-LLM routing). Giving
each its own registry key - resolved in ``config/providers.py`` to its own
``*_API_KEY``/``*_BASE_URL`` pair - is what lets a local vLLM endpoint, a local
llama.cpp endpoint, and real cloud OpenAI all be active at once; sharing the
single ``openai`` row (the original 2026-09-11 approach) could only ever point
at one of the three.

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
    distribution: str  # the pip/PyPI package name, named in the error
    # ChatOpenAI deprecated ``max_tokens`` in favour of the OpenAI spelling;
    # every other integration still wants ``max_tokens``.
    max_tokens_arg: str = "max_tokens"


REGISTRY: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        "langchain_openai",
        "ChatOpenAI",
        "langchain-openai",
        max_tokens_arg="max_completion_tokens",
    ),
    # Self-hosted, OpenAI-compatible-route servers (Phase 1 local-LLM routing,
    # RTX 3090 host) - same class as "openai" above, distinct registry key so
    # config/providers.py can give each its own credential/base-url env vars
    # instead of sharing (and fighting over) OPENAI_BASE_URL.
    "vllm": ProviderSpec(
        "langchain_openai",
        "ChatOpenAI",
        "langchain-openai",
        max_tokens_arg="max_completion_tokens",
    ),
    "llamacpp": ProviderSpec(
        "langchain_openai",
        "ChatOpenAI",
        "langchain-openai",
        max_tokens_arg="max_completion_tokens",
    ),
    "google": ProviderSpec(
        "langchain_google_genai",
        "ChatGoogleGenerativeAI",
        "langchain-google-genai",
    ),
    "nvidia": ProviderSpec(
        "langchain_nvidia_ai_endpoints",
        "ChatNVIDIA",
        "langchain-nvidia-ai-endpoints",
    ),
    "deepseek": ProviderSpec("langchain_deepseek", "ChatDeepSeek", "langchain-deepseek"),
    "openrouter": ProviderSpec(
        "langchain_openrouter", "ChatOpenRouter", "langchain-openrouter"
    ),
}


def load_class(provider: str) -> type:
    """Import one provider's chat model class, or explain why it failed.

    Every entry in ``REGISTRY`` is a core dependency (see ``pyproject.toml``),
    so an ``ImportError`` here means the installation itself is broken or
    incomplete, not that an optional extra was skipped.
    """
    spec = REGISTRY[provider]
    try:
        module = __import__(spec.module, fromlist=[spec.cls])
    except ImportError as exc:
        raise RuntimeError(
            f"LLM_PROVIDER={provider!r} needs {spec.distribution}, which failed to "
            "import even though it is a core llmwiki dependency. Reinstall llmwiki "
            '(e.g. `pip install --force-reinstall llmwiki` or `uv sync`) and check '
            "for an environment/import error."
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
