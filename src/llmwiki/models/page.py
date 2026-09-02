"""Wiki page schemas. L0."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

PageType = Literal["concept", "entity", "index", "source"]


class PageFrontMatter(BaseModel):
    """The YAML block at the top of every wiki page (plan 5.2)."""

    title: str
    slug: str
    type: PageType = "concept"
    gist: str = ""
    sources: list[str] = Field(default_factory=list)
    updated: date | None = None
    version: int = 1


class WikiPage(BaseModel):
    """Front matter plus the markdown body below it."""

    front_matter: PageFrontMatter
    body: str = ""

    @property
    def slug(self) -> str:
        return self.front_matter.slug


class PageGist(BaseModel):
    """One row of ``wiki/_meta/gists.json`` - the progressive-disclosure index.

    This is what the compiler and ``list_concepts`` read instead of page bodies.
    """

    slug: str
    title: str
    type: PageType = "concept"
    gist: str = ""
    sources: list[str] = Field(default_factory=list)
    updated: date | None = None
    version: int = 1


class LintFinding(BaseModel):
    """One issue found by the scheduled global lint."""

    kind: Literal["orphan", "dangling_link", "missing_gist", "stale_source", "duplicate_slug"]
    slug: str
    detail: str = ""


class LintReport(BaseModel):
    """Result of ``lint_wiki``."""

    dry_run: bool = True
    page_count: int = 0
    findings: list[LintFinding] = Field(default_factory=list)
    repaired: list[str] = Field(default_factory=list)
