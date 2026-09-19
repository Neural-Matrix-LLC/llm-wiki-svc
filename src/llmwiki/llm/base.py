"""LLM client protocol.

The domain layer never sees an Anthropic type: it sends a system prompt, a user
prompt and an optional JSON schema, and gets back text plus a usage record.
That is what lets the compiler tests run against a scripted double.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from llmwiki.models.plan import CostRecord
from llmwiki.models.source import ImageInput


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
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run one completion.

        ``system`` is the cache-stable prefix; ``prompt`` carries the volatile
        per-source content and must come after it.  When ``schema`` is given the
        result is returned in ``data``.

        ``model``/``max_tokens``/``temperature`` are ``None`` by default: the
        call site names only ``op`` and the concrete adapter resolves the rest
        from *its own* configuration - ``Settings`` in the single-provider
        fallback path, or a ``config/ops.py`` row when the application-specific
        multi-provider router is active (design v1.4 §4.8.1, plan §19). Pass an
        explicit value only to override that configuration for one call.
        """
        ...


@runtime_checkable
class VisionLLMClient(Protocol):
    """Optional companion to :class:`LLMClient`: the same call shape, plus images.

    Phase 2 (design v1.4 §4.10.4, plan §21.2 D1). A *separate* protocol so the
    ``complete()`` contract above - the one other repositories and every test
    double implement - is byte-identical: a text-only adapter simply does not
    have ``describe``, and the per-op router refuses at startup to route
    ``describe_image`` to one. Only the ingest pipeline's description step
    calls this; the compiler never passes images.
    """

    def describe(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        images: list[ImageInput],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run one completion over ``images`` plus ``prompt``; text out, cost accounted."""
        ...
