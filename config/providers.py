"""Providers manifest - design v1.4 §4.8.1, implement-plan-v1.4.md §19.2.

This is the **tracked, active** providers manifest (2026-09-10): it is
committed, and the ``Dockerfile`` copies ``config/`` into the image, so every
deployment gets it from a ``docker compose pull`` instead of an scp. That is
safe because this file holds **no secrets** - only which environment variable
*names* carry each provider's real credentials. The values themselves stay in
``.env``, which is never committed and never baked into an image.

Every supported provider is listed below, so this file is also the reference:
a row whose ``api_key_env`` is unset in the environment is silently inactive,
which is what lets them all stay uncommented. (The former
``providers.py.example`` was deleted on 2026-09-10 - it had become a second
copy of this file that could drift out of step with it.)

Trade-off worth knowing: because the routing table now ships *in the image*,
changing a provider or model is a rebuild-and-push, not an edit on the box.
``docker-compose.yml`` carries a commented-out ``./config:/app/config:ro``
override for deployments that need to diverge without a rebuild.

Every ``provider`` name here must be either ``"anthropic"``, ``"fake"``, or a
key in ``llmwiki.llm.providers.REGISTRY`` (currently: openai, google, nvidia,
deepseek, openrouter) - anything else fails loudly at startup.

A provider whose ``api_key_env`` is unset or empty in the environment is
**not an error** - it is silently dropped from the active set. Only an op in
``config/ops.py`` that names an inactive provider is an error.

WARNING: this file's existence changes the default for anything that builds a
plain ``Settings()`` from the repo root or from ``/app`` - routing is consulted
*before* ``LLM_PROVIDER``/``LLM_BACKEND`` in ``factory._build_llm_client`` and
returns first, so ``LLM_BACKEND=fake`` no longer selects the offline double
while this file is present. Verified on 2026-09-10: ``pytest`` and
``scripts/smoke_flow.py --offline`` are unaffected (both construct explicit
``Settings`` with these fields set), but the ``api-offline`` compose service
was, and now pins ``LLMWIKI_PROVIDERS_CONFIG``/``LLMWIKI_OPS_CONFIG`` at a
nonexistent path to force the fallback back on. Any other offline entry point
needs the same treatment - an unset env var will not do it, only a path that
does not exist.
"""

PROVIDERS = [
    # Native adapter: prompt caching + measured USD cost (llm/anthropic_client.py).
    # No extra install needed - anthropic is a hard dependency of this package.
    {
        "provider": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        # optional: gateway/proxy; leave the var unset for the default endpoint
        "base_url_env": "ANTHROPIC_BASE_URL",
    },

    # Everything below goes through LangChain (llm/langchain_client.py) - no
    # prompt caching, cost priced only for models in llm/pricing.py. Uncomment
    # a row and `pip install "llmwiki[<extra>]"` to use it.

    {
        "provider": "openai",  # pip install "llmwiki[openai]"
        "api_key_env": "OPENAI_API_KEY",
        # self-hosted (vLLM/Ollama/LM Studio): point this at its OpenAI-compatible endpoint
        "base_url_env": "OPENAI_BASE_URL",
    },
    {
        "provider": "google",  # pip install "llmwiki[google]"
        "api_key_env": "GOOGLE_API_KEY",
        "base_url_env": "GOOGLE_BASE_URL",
    },
    {
        "provider": "nvidia",  # pip install "llmwiki[nvidia]"
        "api_key_env": "NVIDIA_API_KEY",
        "base_url_env": "NVIDIA_BASE_URL",
    },
    {
        "provider": "deepseek",  # pip install "llmwiki[deepseek]"
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
    },
    {
        "provider": "openrouter",  # pip install "llmwiki[openrouter]"
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
    },

    # Offline double - no credentials, no network, no cost. Always active
    # regardless of the environment (needs no api_key_env). Handy for routing
    # one specific op (e.g. a bulk/dev op) to the fake adapter while the rest
    # use a real provider.
    {"provider": "fake"},
]
