"""Scheduled global lint. Never runs on the ingest path.

This is the one place allowed to look at the whole wiki, which is exactly why it
is a cron job (plan 6.8) and not part of compilation.

Phase 2 (plan §21.2 A1): the wiki is partitioned by domain, so lint runs per
domain - every registered one by default, or the one asked for - attributing
each stored key to its domain and checking it against that domain's manifest.
A ``wiki/domains/{d}/`` folder whose ``d`` is not registered is itself a
finding (``unknown_domain``).
"""

from __future__ import annotations

import re

from llmwiki.models.page import DomainRegistry, LintFinding, LintReport, PageGist
from llmwiki.storage.base import ObjectStore
from llmwiki.storage.layout import GENERAL, WIKI_PREFIX, domain_of_key, index_key
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.domains import load_registry, require_domain
from llmwiki.wiki.pages import parse_page

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def lint_wiki(store: ObjectStore, dry_run: bool = True, domain: str | None = None) -> LintReport:
    """Check each domain's manifest against storage and its link graph against both."""
    registry = load_registry(store)
    domains = registry.names() if domain is None else [require_domain(registry, domain)]
    report = LintReport(dry_run=dry_run, domains=list(domains))

    # The one full listing lint is allowed; every domain is carved out of it.
    all_keys = [key for key in store.list(WIKI_PREFIX) if key.endswith(".md")]

    if domain is None:
        present = {name for key in all_keys if (name := domain_of_key(key)) is not None}
        for name in sorted(present - set(registry.names())):
            report.findings.append(LintFinding(
                kind="unknown_domain", slug=name, domain=name,
                detail=f"wiki/domains/{name}/ holds pages but {name!r} is not registered",
            ))

    for name in domains:
        _lint_domain(store, name, all_keys, registry, report, dry_run)
    return report


def _lint_domain(
    store: ObjectStore,
    domain: str,
    all_keys: list[str],
    registry: DomainRegistry,
    report: LintReport,
    dry_run: bool,
) -> None:
    manifest = gists_mod.load_gists(store, domain)
    report.page_count += len(manifest)

    keys = [
        key for key in all_keys
        if domain_of_key(key) == domain
        and "/_meta/" not in key
        and key != index_key(domain)
    ]
    stored_slugs = {key.rsplit("/", 1)[1][:-3] for key in keys}

    for slug in sorted(stored_slugs - set(manifest)):
        report.findings.append(LintFinding(
            kind="orphan", slug=slug, domain=domain,
            detail="page exists but is absent from gists.json",
        ))
    for slug in sorted(set(manifest) - stored_slugs):
        report.findings.append(LintFinding(
            kind="stale_source", slug=slug, domain=domain,
            detail="gists.json lists a page that is not stored",
        ))
    for slug, gist in sorted(manifest.items()):
        if not gist.gist.strip():
            report.findings.append(LintFinding(
                kind="missing_gist", slug=slug, domain=domain,
                detail="no gist; the page is invisible to the compiler's lookup",
            ))

    known = stored_slugs | set(manifest)
    for key in keys:
        try:
            page = parse_page(store.get(key).decode("utf-8"))
        except Exception as exc:
            report.findings.append(LintFinding(
                kind="orphan", slug=key, domain=domain, detail=f"unparseable page: {exc}",
            ))
            continue
        for target in _WIKILINK.findall(page.body):
            target = target.strip().removeprefix("sources/")
            if target and target not in known:
                report.findings.append(LintFinding(
                    kind="dangling_link", slug=page.slug, domain=domain,
                    detail=f"links to [[{target}]], which does not exist",
                ))

    if not dry_run:
        report.repaired += _repair(store, domain, manifest, report, registry)


def _repair(
    store: ObjectStore,
    domain: str,
    manifest: dict[str, PageGist],
    report: LintReport,
    registry: DomainRegistry,
) -> list[str]:
    """Repair only what is mechanically safe: manifest rows and the index.

    Dangling links are reported, never auto-created - inventing a page to satisfy
    a link is how a wiki fills up with stubs.
    """
    repaired: list[str] = []
    for finding in report.findings:
        if finding.domain != domain:
            continue
        if finding.kind == "stale_source" and finding.slug in manifest:
            del manifest[finding.slug]
            repaired.append(f"removed stale manifest row {finding.slug}"
                            + ("" if domain == GENERAL else f" ({domain})"))
    if repaired:
        gists_mod.save_gists(store, manifest, domain)
    gists_mod.write_index(store, manifest, domain, registry if domain == GENERAL else None)
    repaired.append(f"regenerated {index_key(domain)}")
    return repaired
