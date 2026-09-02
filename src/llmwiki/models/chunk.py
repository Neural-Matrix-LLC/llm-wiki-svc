"""Chunk and retrieval schemas. L0."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from llmwiki.models.source import utcnow


class ChunkMetadata(BaseModel):
    """Stored on the vector so a citation needs no second fetch (plan 5.3)."""

    source_id: str
    chunk_index: int
    title: str = ""
    url: str | None = None
    section: str = ""
    char_start: int = 0
    char_end: int = 0
    ingested_at: datetime = Field(default_factory=utcnow)


class Chunk(BaseModel):
    """A slice of an extracted document, sized for embedding."""

    id: str
    text: str
    metadata: ChunkMetadata

    @staticmethod
    def make_id(source_id: str, chunk_index: int) -> str:
        """Deterministic id so re-ingesting a source overwrites rather than duplicates."""
        return f"{source_id}:{chunk_index}"


class SearchHit(BaseModel):
    """One retrieval result, from either the gist index or the chunk index."""

    id: str
    score: float
    origin: Literal["wiki", "chunk"] = "chunk"
    text: str = ""
    slug: str | None = None
    source_id: str | None = None
    metadata: dict = Field(default_factory=dict)
