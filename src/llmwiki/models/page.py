"""Wiki page schemas. L0."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from llmwiki.models.source import GENERAL_DOMAIN

# "overview" (Phase 2, plan §21.2 A8): the one page per domain the scheduled
# synthesis job writes; never produced by the per-source compiler.
PageType = Literal["concept", "entity", "index", "source", "overview"]


class PageFrontMatter(BaseModel):
    """The YAML block at the top of every wiki page (plan 5.2)."""

    title: str
    slug: str
    type: PageType = "concept"
    gist: str = ""
    sources: list[str] = Field(default_factory=list)
    updated: date | None = None
    version: int = 1
    # Phase 2 (plan §21.2 A7): which domain's folder the page lives in. Rendered
    # into the front matter only when it is not the default, so every page of a
    # pre-Phase-2 wiki stays byte-identical.
    domain: str = GENERAL_DOMAIN


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

    kind: Literal["orphan", "dangling_link", "missing_gist", "stale_source", "duplicate_slug",
                  "unknown_domain"]
    slug: str
    detail: str = ""
    domain: str = GENERAL_DOMAIN


class LintReport(BaseModel):
    """Result of ``lint_wiki``."""

    dry_run: bool = True
    page_count: int = 0
    findings: list[LintFinding] = Field(default_factory=list)
    repaired: list[str] = Field(default_factory=list)
    # Phase 2: which domains this report covered (all registered ones by default).
    domains: list[str] = Field(default_factory=list)


class Domain(BaseModel):
    """One row of ``wiki/_meta/domains.json`` (Phase 2, plan §21.2 A3).

    ``general`` is never a row: it is implied, always present and irremovable.
    """

    name: str
    description: str = ""
    created: date | None = None


class DomainRegistry(BaseModel):
    """The curated domain registry - admin-written, read by everything else."""

    version: int = 1
    domains: dict[str, Domain] = Field(default_factory=dict)

    def names(self) -> list[str]:
        """Every domain, ``general`` first, then the registered ones in name order."""
        return [GENERAL_DOMAIN, *sorted(name for name in self.domains if name != GENERAL_DOMAIN)]

    def has(self, name: str) -> bool:
        return name == GENERAL_DOMAIN or name in self.domains

    @property
    def is_general_only(self) -> bool:
        """True when nothing is registered - the Phase 2 features are effectively off."""
        return not any(name != GENERAL_DOMAIN for name in self.domains)
