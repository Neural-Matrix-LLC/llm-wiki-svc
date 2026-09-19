"""Query-side cost capture: a transparent wrapper plus a context-scoped collector.

Phase 2 (design v1.4 §4.10.3, plan §21.2 C2). The compiler collects the
``usage`` of every call it makes and writes it to the ledger itself; the query
path never did, because its calls are spread over several graph nodes and
helpers (``agent_step``, skill selection, the skill chain, the fixed prompt).
Rather than thread a sink through every call site, the client the factory
hands out is wrapped once in :class:`MeteredLLM`, and whoever wants the
records opens :func:`collect_usage` around the work::

    with collect_usage() as records:
        answer = agent.answer(question)
    ledger.append(records, kind="query")

The collector is a ``contextvars.ContextVar``: nothing is recorded outside a
``collect_usage()`` block, so the compiler's own calls (already ledgered) are
never counted twice. LangGraph copies the calling context into the threads it
runs nodes on, so a collector opened before ``graph.invoke`` sees every node's
call (``tests/unit/test_metering.py`` pins this against the real graph).
Blocks nest: an inner block's records also reach the enclosing one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from llmwiki.llm.base import LLMClient, LLMResponse
from llmwiki.models.plan import CostRecord

_collector: ContextVar[list[CostRecord] | None] = ContextVar(
    "llmwiki_usage_collector", default=None
)


@contextmanager
def collect_usage() -> Iterator[list[CostRecord]]:
    """Collect the ``usage`` of every metered call made inside the block."""
    records: list[CostRecord] = []
    outer = _collector.get()
    token = _collector.set(records)
    try:
        yield records
    finally:
        _collector.reset(token)
        if outer is not None:
            outer.extend(records)


def record_usage(usage: CostRecord | None) -> None:
    """Hand one record to the active collector, if any. Cheap no-op otherwise."""
    if usage is None:
        return
    sink = _collector.get()
    if sink is not None:
        sink.append(usage)


class MeteredLLM:
    """Forwards every call to ``inner`` and reports its ``usage`` to the active collector.

    Adds no behaviour of its own: the same protocol, the same response, the same
    exceptions. Attributes it does not define (a double's ``calls`` list, a
    future ``describe()``) resolve on the wrapped client, so wrapping is
    invisible to callers and tests alike.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner

    @property
    def inner(self) -> LLMClient:
        return self._inner

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
        response = self._inner.complete(
            op=op, system=system, prompt=prompt, schema=schema,
            model=model, max_tokens=max_tokens, temperature=temperature,
        )
        record_usage(response.usage)
        return response

    def describe(self, **kwargs: Any) -> LLMResponse:
        """Vision form (Phase 2): forwarded when the wrapped client has it, metered the same."""
        inner_describe = getattr(self._inner, "describe", None)
        if inner_describe is None:
            raise AttributeError("the configured LLM client cannot take images")
        response: LLMResponse = inner_describe(**kwargs)
        record_usage(response.usage)
        return response

    def __getattr__(self, name: str) -> Any:
        # Only reached for names this class does not define.
        return getattr(self._inner, name)
