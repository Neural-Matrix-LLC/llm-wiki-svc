"""LLM client protocol.

The domain layer never sees an Anthropic type: it sends a system prompt, a user
prompt and an optional JSON schema, and gets back text plus a usage record.
That is what lets the compiler tests run against a scripted double.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from llmwiki.models.plan import CostRecord


class LLMResponse(BaseModel):
    """A completion plus the measured cost of producing it."""

    text: str = ""
    data: dict | None = None
    usage: CostRecord | None = None


class TokenBudgetExceeded(RuntimeError):
    """Raised when one ingest would exceed ``INGEST_TOKEN_BUDGET``."""


@runtime_checkable
class LLMClient(Protocol):
    """Prompt in, structured response out, with cost accounted per call."""

    def complete(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Run one completion.

        ``system`` is the cache-stable prefix; ``prompt`` carries the volatile
        per-source content and must come after it.  When ``schema`` is given the
        result is returned in ``data``.
        """
        ...
