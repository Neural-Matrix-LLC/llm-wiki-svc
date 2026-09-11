"""Per-op routing table - design v1.4 §4.8.1, implement-plan-v1.4.md §19.2.

This is the **tracked, active** per-op routing table (2026-09-10): committed
alongside ``config/providers.py`` and copied into the image by the
``Dockerfile``. It names providers and models, never credentials. Both files
are required together, or neither - one without the other fails loudly at
startup. (The former ``ops.py.example`` was deleted on 2026-09-10, once this
file became the tracked one: two copies of the same table is one too many.)

Every op the codebase actually calls needs **exactly one** row here - a
missing or duplicate one fails loudly at startup, and so does an op whose
``provider`` is not active in ``config/providers.py``. The five below
(``summarize_source`` -> ``plan_compile`` -> ``create_page``/``patch_page`` are
the incremental compiler's four stages, design doc §4.4; ``answer_query`` is
the query agent) are the complete current set - see
``llmwiki.llm.routing_config.KNOWN_OPS``, which this file is validated against.

This is what replaces the old ``COMPILE_EXECUTOR_MODEL``: `create_page` and
`patch_page` below use a stronger model than the other three, same idea, now
expressed per-op instead of as one wiki-wide setting.
"""

OPS = [
    # Cheap stages: one call over bounded input (design doc §4.4's cost guarantee).
    {"op": "summarize_source", "provider": "openrouter", "model": "z-ai/glm-5.3-flash",
     "temperature": 1.0, "max_tokens": 2048},
    {"op": "plan_compile", "provider": "openrouter", "model": "z-ai/glm-5.3-flash",
     "temperature": 1.0, "max_tokens": 2048},

    # Executor stages: escalated to a stronger model (plan-1.1 D5's reasoning,
    # now a per-op row instead of the old COMPILE_EXECUTOR_MODEL setting).
    {"op": "create_page", "provider": "openrouter", "model": "z-ai/glm-5.3-flash",
     "temperature": 1.0, "max_tokens": 4096},
    {"op": "patch_page", "provider": "openrouter", "model": "z-ai/glm-5.3-flash",
     "temperature": 1.0, "max_tokens": 4096},

    # Query agent - answers a question from retrieved wiki/chunk context.
    {"op": "answer_query", "provider": "openrouter", "model": "z-ai/glm-5.3-flash",
     "temperature": 1.0, "max_tokens": 2048},

    # To route an op through a different provider, change its "provider" and
    # "model" - e.g., to answer queries with OpenAI while everything else
    # stays on Anthropic, first uncomment the openai row in
    # config/providers.py, then replace the answer_query row above with:
    # {"op": "answer_query", "provider": "openai", "model": "gpt-5",
    #  "temperature": 1.0, "max_tokens": 2048},
]
