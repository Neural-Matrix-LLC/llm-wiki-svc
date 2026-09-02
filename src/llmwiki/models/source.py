"""Schemas for captured sources. L0: pure pydantic, no I/O, no llmwiki imports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceState = Literal["queued", "extracting", "embedding", "compiling", "done", "failed"]
Modality = Literal["pdf", "web", "youtube", "image", "text"]


def utcnow() -> datetime:
    """Timezone-aware UTC now. One definition, so timestamps never disagree."""
    return datetime.now(UTC)


class SourceMeta(BaseModel):
    """Written once to ``raw/{source_id}/meta.json`` and never mutated."""

    source_id: str
    modality: Modality
    title: str = ""
    url: str | None = None
    filename: str | None = None
    mime: str = "application/octet-stream"
    sha256: str
    byte_size: int = 0
    captured_at: datetime = Field(default_factory=utcnow)


class SourceRef(BaseModel):
    """What ingest returns to the caller immediately."""

    source_id: str
    status: SourceState = "queued"
    duplicate: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class SourceStatus(BaseModel):
    """Progress of one source through the pipeline, polled via ``GET /sources/{id}``."""

    source_id: str
    state: SourceState
    error: str | None = None
    chunk_count: int = 0
    pages_touched: int = 0
    elapsed_s: float = 0.0
    updated_at: datetime = Field(default_factory=utcnow)


class ExtractedDoc(BaseModel):
    """Normalized text produced by an extractor, before chunking."""

    source_id: str
    title: str = ""
    text: str
    modality: Modality
    url: str | None = None
    extra: dict = Field(default_factory=dict)
