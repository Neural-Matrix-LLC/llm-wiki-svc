"""Compiler plan schemas and the cost ledger record. L0."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from llmwiki.models.source import utcnow

OpKind = Literal["create_page", "patch_page", "add_backlink", "flag_contradiction"]


class CompileOp(BaseModel):
    """A single planned mutation. The planner sees gists only, never page bodies."""

    kind: OpKind
    slug: str
    title: str = ""
    type: Literal["concept", "entity"] = "concept"
    reason: str = ""


class CompilePlan(BaseModel):
    """Output of the cheap planning model, capped at ``COMPILE_MAX_PAGES`` ops."""

    ops: list[CompileOp] = Field(default_factory=list)


class SourceSummary(BaseModel):
    """Structured summary of a source: the compiler's only view of the raw text."""

    title: str = ""
    gist: str = ""
    summary: str = ""
    concepts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


class CompileResult(BaseModel):
    """What one compile pass did, returned by ``compile_update``."""

    source_id: str
    created: list[str] = Field(default_factory=list)
    patched: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    page_bodies_read: int = 0
    cost_usd: float = 0.0
    aborted: bool = False
    reason: str = ""

    @property
    def pages_touched(self) -> int:
        return len(self.created) + len(self.patched)


class CostRecord(BaseModel):
    """One line of ``wiki/_meta/cost.jsonl``. Cost is measured, never estimated."""

    op: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    version: str = ""
    source_id: str | None = None
    at: datetime = Field(default_factory=utcnow)


class CostSummary(BaseModel):
    """Aggregation of the ledger, for ``llmwiki cost`` and the smoke script."""

    total_usd: float = 0.0
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    by_model: dict[str, float] = Field(default_factory=dict)


class Citation(BaseModel):
    """A claim traced back to a real object under ``raw/``."""

    source_id: str
    title: str = ""
    url: str | None = None
    slug: str | None = None


class Answer(BaseModel):
    """Result of the query agent: text plus citations that must resolve."""

    text: str
    citations: list[Citation] = Field(default_factory=list)
    used_rag_fallback: bool = False
