"""Schemas for captured sources. L0: pure pydantic, no I/O, no llmwiki imports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# "paused" (Phase 2, plan §21.2 C5): post-capture processing is parked because
# the monthly hard cap was reached; the source is captured and will be
# processed when the cap lifts.
SourceState = Literal["queued", "extracting", "embedding", "compiling", "done", "failed",
                      "paused"]
Modality = Literal["pdf", "web", "youtube", "image", "text"]

#: The domain every pre-Phase-2 source and page belongs to - the Phase 0/1
#: layout under a name (plan §21.2 A1). Kept here at L0 so every model can
#: default to it without importing the layout module.
GENERAL_DOMAIN = "general"


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
    # Phase 2 (plan §21.2 A4): the domain the *caller* named at capture time,
    # or None when the router decides. Immutable like the rest of meta.json;
    # the router's decision lives in raw/{id}/routing.json instead.
    domain: str | None = None


class ImageInput(BaseModel):
    """One image handed to a vision-capable model (Phase 2, plan §21.2 D1)."""

    media_type: str
    data: bytes


class DomainAssignment(BaseModel):
    """The routing decision for one source - ``raw/{id}/routing.json`` (plan §21.2 A4/A5).

    ``explicit`` records that the caller named the domain (no model call was
    made); ``suggested_domain`` is what the router would have liked to file the
    source under when nothing registered fits - recorded, never acted on.
    """

    domain: str = GENERAL_DOMAIN
    confidence: float = 1.0
    suggested_domain: str = ""
    reason: str = ""
    explicit: bool = False
    routed_at: datetime = Field(default_factory=utcnow)


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
    # Phase 2: where the source was filed (None until routed), what the router
    # would have preferred when it fell back to general, and how many vision
    # calls extraction made (plan §21.2 A5, D4).
    domain: str | None = None
    suggested_domain: str | None = None
    vision_calls: int = 0


class ExtractedDoc(BaseModel):
    """Normalized text produced by an extractor, before chunking."""

    source_id: str
    title: str = ""
    text: str
    modality: Modality
    url: str | None = None
    extra: dict = Field(default_factory=dict)
