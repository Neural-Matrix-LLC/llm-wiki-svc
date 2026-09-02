"""Scheduled global lint. Never runs on the ingest path.

This is the one place allowed to look at the whole wiki, which is exactly why it
is a cron job (plan 6.8) and not part of compilation.
"""

from __future__ import annotations

import re

from llmwiki.models.page import LintFinding, LintReport, PageGist
from llmwiki.storage.base import ObjectStore
from llmwiki.storage.layout import WIKI_PREFIX
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import parse_page

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def lint_wiki(store: ObjectStore, dry_run: bool = True) -> LintReport:
    """Check the manifest against storage and the link graph against both."""
    manifest = gists_mod.load_gists(store)
    report = LintReport(dry_run=dry_run, page_count=len(manifest))

    keys = [
        key
        for key in store.list(WIKI_PREFIX)
        if key.endswith(".md") and "/_meta/" not in key
        and not key.endswith("index.md")
    ]
    stored_slugs = {key.rsplit("/", 1)[1][:-3] for key in keys}

    for slug in sorted(stored_slugs - set(manifest)):
        report.findings.append(
            LintFinding(
                kind="orphan", slug=slug, detail="page exists but is absent from gists.json"
            )
        )
    for slug in sorted(set(manifest) - stored_slugs):
        report.findings.append(
            LintFinding(
                kind="stale_source", slug=slug, detail="gists.json lists a page that is not stored"
            )
        )
    for slug, gist in sorted(manifest.items()):
        if not gist.gist.strip():
            report.findings.append(
                LintFinding(
                    kind="missing_gist",
                    slug=slug,
                    detail="no gist; the page is invisible to the compiler's lookup",
                )
            )

    known = stored_slugs | set(manifest)
    for key in keys:
        try:
            page = parse_page(store.get(key).decode("utf-8"))
        except Exception as exc:
            report.findings.append(
                LintFinding(kind="orphan", slug=key, detail=f"unparseable page: {exc}")
            )
            continue
        for target in _WIKILINK.findall(page.body):
            target = target.strip().removeprefix("sources/")
            if target and target not in known:
                report.findings.append(
                    LintFinding(
                        kind="dangling_link",
                        slug=page.slug,
                        detail=f"links to [[{target}]], which does not exist",
                    )
                )

    if not dry_run:
        report.repaired = _repair(store, manifest, stored_slugs, report)
    return report


def _repair(
    store: ObjectStore,
    manifest: dict[str, PageGist],
    stored_slugs: set[str],
    report: LintReport,
) -> list[str]:
    """Repair only what is mechanically safe: manifest rows and the index.

    Dangling links are reported, never auto-created - inventing a page to satisfy
    a link is how a wiki fills up with stubs.
    """
    repaired: list[str] = []
    for finding in report.findings:
        if finding.kind == "stale_source" and finding.slug in manifest:
            del manifest[finding.slug]
            repaired.append(f"removed stale manifest row {finding.slug}")
    if repaired:
        gists_mod.save_gists(store, manifest)
    gists_mod.write_index(store, manifest)
    repaired.append("regenerated wiki/index.md")
    return repaired
