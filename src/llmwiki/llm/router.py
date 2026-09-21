"""Routes each op to its configured provider - implement-plan.md Part II §19.3.

Built only when ``config/providers.py`` + ``config/ops.py`` are both present
(``factory._build_routed_llm_client``); the domain layer sees an ordinary
``LLMClient`` either way, so ``agent/query.py`` and ``wiki/compiler.py`` do not
know or care whether they are talking to this or to a single provider adapter.
"""

from __future__ import annotations

from llmwiki.llm.base import LLMClient, LLMResponse
from llmwiki.llm.routing_config import RoutingConfig


class RoutingLLMClient:
    """Dispatches ``complete(op=...)`` to the provider the op is configured for."""

    def __init__(self, routing: RoutingConfig, clients: dict[str, LLMClient]) -> None:
        self._routing = routing
        self._clients = clients

    def complete(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        # A KeyError here is a bug, not a config error: load_routing_config()
        # already validated that every known op has a row before this client
        # was ever built.
        route = self._routing.ops[op]
        client = self._clients[route.provider]
        return client.complete(
            op=op,
            system=system,
            prompt=prompt,
            schema=schema,
            model=model or route.model,
            max_tokens=max_tokens if max_tokens is not None else route.max_tokens,
            temperature=route.temperature if temperature is None else temperature,
        )
