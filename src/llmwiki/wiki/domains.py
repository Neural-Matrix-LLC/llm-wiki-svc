"""The curated domain registry - ``wiki/_meta/domains.json`` (Phase 2, plan §21.2 A3).

A domain is a *partition key*, not a tenant: it names a folder under
``wiki/domains/{d}/`` with its own gist manifest, index page and vector/lexical
indexes, so that compiling into one domain never reads another's manifest and a
query loads only the manifests of the domains its hits came from (design
v1.4 §4.10.1). ``general`` is the Phase 0/1 layout under a name: it is implied,
always present, never a row here and never removable.

The registry is written only by an administrator (CLI/REST) and only *read*
by the compiler, the router and the query path - so there are never two
writers, and the root index's ``## Domains`` section can be rendered from this
one object without touching any manifest.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from llmwiki.models.page import Domain, DomainRegistry
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    DOMAINS_KEY,
    GENERAL,
    check_domain,
    domain_index_name,
    domain_prefix,
    gists_key,
    index_key,
    wiki_page,
)

logger = logging.getLogger(__name__)


class UnknownDomain(KeyError):
    """A caller named a domain that is not ``general`` and not registered."""


def load_registry(store: ObjectStore) -> DomainRegistry:
    """The registry, or an empty one (= general only) when the file is absent."""
    try:
        raw = store.get(DOMAINS_KEY)
    except ObjectNotFound:
        return DomainRegistry()
    data = json.loads(raw.decode("utf-8"))
    return DomainRegistry.model_validate(data)


def save_registry(store: ObjectStore, registry: DomainRegistry) -> None:
    """Persist the registry, sorted so diffs stay readable."""
    payload = registry.model_dump(mode="json")
    payload["domains"] = dict(sorted(payload["domains"].items()))
    store.put(DOMAINS_KEY, json.dumps(payload, indent=2).encode("utf-8"), "application/json")


def require_domain(registry: DomainRegistry, name: str | None) -> str:
    """Resolve a caller-supplied domain: ``None`` means general; unknown names raise."""
    if name is None or name == GENERAL:
        return GENERAL
    check_domain(name)
    if not registry.has(name):
        raise UnknownDomain(name)
    return name


def upsert_domain(store: ObjectStore, name: str, description: str = "") -> Domain:
    """Register ``name`` (or update its description). ``general`` cannot be registered."""
    check_domain(name, allow_general=False)
    registry = load_registry(store)
    existing = registry.domains.get(name)
    domain = Domain(
        name=name,
        description=description.strip() or (existing.description if existing else ""),
        created=existing.created if existing else date.today(),
    )
    registry.domains[name] = domain
    save_registry(store, registry)
    logger.info("domains: %s %s", "updated" if existing else "added", name)
    return domain


def remove_domain(store: ObjectStore, name: str, *, force: bool = False) -> bool:
    """Unregister ``name``. Refuses when the domain still holds pages unless ``force``.

    Removes the registry row only - never the pages, manifest or indexes. A
    forced removal leaves those objects orphaned under ``wiki/domains/{d}/``,
    which ``lint`` reports as ``unknown_domain``.
    """
    check_domain(name, allow_general=False)
    registry = load_registry(store)
    if name not in registry.domains:
        return False
    if not force and store.exists(gists_key(name)):
        from llmwiki.wiki import gists as gists_mod

        if gists_mod.load_gists(store, name):
            raise ValueError(
                f"domain {name!r} still has pages; pass force=True to unregister it anyway"
            )
    del registry.domains[name]
    save_registry(store, registry)
    logger.info("domains: removed %s (force=%s)", name, force)
    return True


@dataclass(frozen=True)
class DomainScope:
    """Every key and index name one domain uses - the compiler's and the query path's view."""

    name: str

    @property
    def is_general(self) -> bool:
        return self.name == GENERAL

    @property
    def prefix(self) -> str:
        return domain_prefix(self.name)

    @property
    def gists_key(self) -> str:
        return gists_key(self.name)

    @property
    def index_key(self) -> str:
        return index_key(self.name)

    def page_key(self, slug: str, page_type: str = "concept") -> str:
        return wiki_page(slug, page_type, self.name)

    def index_name(self, base: str) -> str:
        """The Vectorize/lexical index for ``base`` (the gists or chunks index) in this domain."""
        return domain_index_name(base, self.name)


def render_domains_section(registry: DomainRegistry) -> list[str]:
    """The root index's ``## Domains`` block - empty when only general exists."""
    if registry.is_general_only:
        return []
    lines = ["## Domains", ""]
    for name in registry.names():
        if name == GENERAL:
            continue
        description = registry.domains[name].description
        summary = f" - {description}" if description else ""
        lines.append(f"- [[domains/{name}/index|{name}]]{summary}")
    lines.append("")
    return lines


def resolve_scopes(
    explicit: str | None,
    policy: str,
    registry: DomainRegistry,
    *,
    route: Callable[[], list[str]] | None = None,
) -> list[DomainScope]:
    """Which domains a query searches (plan §21.2 A6).

    An explicit domain wins. A general-only registry, or ``policy="general"``,
    is today's single scope. ``all`` fans out over every registered domain
    with no model call. ``routed`` asks ``route()`` (one ``route_domain``
    call, supplied by the caller so this module stays LLM-free) and falls
    back to ``all`` when it names nothing usable - a bad decision costs
    recall, never an answer.
    """
    if explicit is not None:
        return [DomainScope(require_domain(registry, explicit))]
    if registry.is_general_only or policy == "general":
        return [DomainScope(GENERAL)]
    if policy == "routed" and route is not None:
        chosen = [name for name in route() if registry.has(name)]
        if chosen:
            return [DomainScope(name) for name in chosen]
    return [DomainScope(name) for name in registry.names()]
