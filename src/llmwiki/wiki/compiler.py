"""The incremental wiki compiler - the core of Phase 0.

Design doc 4.4 states the constraint this module exists to satisfy:
**compilation must never scan the full wiki.**  Cost per ingest has to stay flat
as the corpus grows, or the whole idea stops being affordable.

The five stages below are what keep it flat:

1. **summarize** - one cheap call over the source text; nothing downstream ever
   sees the source again.
2. **locate** - semantic lookup against the *gist* index.  Loads one-line gists,
   never page bodies, so this step's cost is fixed by ``k``.
3. **plan** - one cheap call over ``{summary, candidate gists}``, capped at
   ``COMPILE_MAX_PAGES`` operations.  Input size is bounded by the cap, not by
   wiki size.
4. **execute** - loads only the page bodies the plan actually names.
5. **record** - manifest, index, source note, cost ledger.  No LLM calls.

Global lint and cross-page synthesis are deliberately *not* here; they run on a
schedule from :mod:`llmwiki.wiki.lint`.

``tests/unit/test_compiler_no_full_scan.py`` asserts stages 2-4 hold, with a spy
object store that counts page-body reads and forbids prefix listing.  That test
guards the design's central constraint and must never be weakened.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.llm.base import LLMClient, TokenBudgetExceeded
from llmwiki.models.page import PageFrontMatter, PageGist, WikiPage
from llmwiki.models.plan import (
    CompileOp,
    CompilePlan,
    CompileResult,
    CostRecord,
    SourceSummary,
)
from llmwiki.models.source import ExtractedDoc
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import COST_KEY, slugify, wiki_source_note
from llmwiki.vector.base import VectorStore
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import (
    VersionConflict,
    append_sources_section,
    read_page,
    render_page,
    write_page,
)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "summary": {"type": "string"},
        "concepts": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["gist", "summary"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["create_page", "patch_page", "add_backlink", "flag_contradiction"],
                    },
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                    "type": {"type": "string", "enum": ["concept", "entity"]},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "slug"],
            },
        }
    },
    "required": ["ops"],
}

PAGE_SCHEMA = {
    "type": "object",
    "properties": {"gist": {"type": "string"}, "body": {"type": "string"}},
    "required": ["gist", "body"],
}

# How much of a source the summarizer sees. Long sources are summarized from
# their opening; full-document handling for very long inputs is Phase 1.
MAX_SUMMARY_CHARS = 24_000

logger = logging.getLogger(__name__)


class Compiler:
    """Compiles one extracted source into the wiki, touching as little as possible.

    Takes protocols, never constructs adapters and never reads the environment -
    which is why the tests can hand it a spy store and a scripted LLM.
    """

    def __init__(
        self,
        store: ObjectStore,
        vectors: VectorStore,
        embedder: Embedder,
        llm: LLMClient,
        settings: Settings,
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.settings = settings
        self._page_reads = 0
        self._costs: list[CostRecord] = []

    # -- public API ---------------------------------------------------------

    def compile_source(self, doc: ExtractedDoc, force: bool = False) -> CompileResult:
        """Run the full five-stage pass for one source."""
        logger.info("compile start: source_id=%s title=%r", doc.source_id, doc.title)
        self._page_reads = 0
        self._costs = []
        result = CompileResult(source_id=doc.source_id)

        manifest = gists_mod.load_gists(self.store)
        if not force and self._already_compiled(manifest, doc.source_id):
            result.reason = "source already compiled; nothing to do"
            result.skipped = [
                slug for slug, gist in manifest.items() if doc.source_id in gist.sources
            ]
            logger.info("compile skip: source_id=%s already compiled", doc.source_id)
            return result

        try:
            summary = self._summarize(doc)
            candidates = self._locate(summary, manifest)
            plan = self._plan(summary, candidates)
            self._execute(doc, summary, plan, manifest, result)
        except TokenBudgetExceeded as exc:
            result.aborted = True
            result.reason = str(exc)
            logger.warning("compile aborted: source_id=%s %s", doc.source_id, exc)
        finally:
            self._record(doc, result, manifest)

        logger.info(
            "compile done: source_id=%s created=%d patched=%d skipped=%d aborted=%s",
            doc.source_id, len(result.created), len(result.patched), len(result.skipped),
            result.aborted,
        )
        return result

    # -- stage 1: summarize -------------------------------------------------

    def _summarize(self, doc: ExtractedDoc) -> SourceSummary:
        text = doc.text[:MAX_SUMMARY_CHARS]
        logger.debug("summarize_source: source_id=%s", doc.source_id)
        response = self.llm.complete(
            op="summarize_source",
            system=load_prompt("summarize_source"),
            prompt=f"# Source: {doc.title}\nURL: {doc.url or 'n/a'}\n\n{text}",
            schema=SUMMARY_SCHEMA,
        )
        self._account(response.usage)
        data = response.data or {}
        return SourceSummary(
            title=data.get("title") or doc.title,
            gist=data.get("gist", ""),
            summary=data.get("summary", ""),
            concepts=[slugify(c) for c in data.get("concepts", []) if c],
            entities=[e for e in data.get("entities", []) if e],
        )

    # -- stage 2: locate ----------------------------------------------------

    def _locate(self, summary: SourceSummary, manifest: dict[str, PageGist]) -> list[PageGist]:
        """Find the pages this source might affect, using gists only.

        No page body is loaded here and no wiki prefix is listed.  When the gist
        index has not caught up (it is eventually consistent on Vectorize), the
        manifest supplies exact-slug matches so a fresh wiki still compiles.
        """
        if not manifest:
            return []

        probes = [summary.gist or summary.summary] + summary.concepts + summary.entities
        probes = [probe for probe in probes if probe][:8]
        if not probes:
            return []

        found: dict[str, PageGist] = {}
        vectors = self.embedder.embed(probes)
        for vector in vectors:
            hits = self.vectors.query(
                self.settings.vectorize_gists_index,
                vector,
                k=self.settings.compile_candidate_pages,
            )
            for hit in hits:
                slug = hit.slug or hit.id
                if slug in manifest and slug not in found:
                    found[slug] = manifest[slug]

        # Exact-name matches the vector index may not have indexed yet.
        for name in summary.concepts + summary.entities:
            slug = slugify(name)
            if slug in manifest and slug not in found:
                found[slug] = manifest[slug]

        return list(found.values())[: self.settings.compile_candidate_pages]

    # -- stage 3: plan ------------------------------------------------------

    def _plan(self, summary: SourceSummary, candidates: list[PageGist]) -> CompilePlan:
        cap = self.settings.compile_max_pages
        listing = (
            "\n".join(f"- {gist.slug}: {gist.gist or gist.title}" for gist in candidates)
            or "(the wiki is empty)"
        )
        prompt = (
            f"## New source\n\nTitle: {summary.title}\nGist: {summary.gist}\n\n"
            f"{summary.summary}\n\n"
            f"Concepts: {', '.join(summary.concepts) or 'none identified'}\n"
            f"Entities: {', '.join(summary.entities) or 'none identified'}\n\n"
            f"## Existing candidate pages (gists only)\n\n{listing}\n\n"
            f"## Constraint\n\nEmit at most {cap} operations."
        )
        logger.debug("plan_compile: title=%r candidates=%d", summary.title, len(candidates))
        response = self.llm.complete(
            op="plan_compile",
            system=load_prompt("plan_compile"),
            prompt=prompt,
            schema=PLAN_SCHEMA,
        )
        self._account(response.usage)
        raw_ops = (response.data or {}).get("ops", [])

        ops: list[CompileOp] = []
        known = {gist.slug for gist in candidates}
        for row in raw_ops[:cap]:
            try:
                op = CompileOp(**row)
            except Exception:
                continue
            op.slug = slugify(op.slug)
            # A "create" for a page that already exists is a patch; the model
            # does not always respect that rule and the cost of trusting it is a
            # silently overwritten page.
            if op.kind == "create_page" and op.slug in known:
                op.kind = "patch_page"
            ops.append(op)
        return CompilePlan(ops=ops)

    # -- stage 4: execute ---------------------------------------------------

    def _execute(
        self,
        doc: ExtractedDoc,
        summary: SourceSummary,
        plan: CompilePlan,
        manifest: dict[str, PageGist],
        result: CompileResult,
    ) -> None:
        for op in plan.ops:
            if op.kind in ("add_backlink", "flag_contradiction"):
                # Both are expressed as a patch of the target page; the prompt
                # carries the intent. Kept as distinct op kinds because the
                # planner reasons about them differently.
                op = op.model_copy(update={"kind": "patch_page"})

            self._check_budget()
            try:
                if op.kind == "create_page":
                    self._create_page(doc, summary, op, manifest)
                    result.created.append(op.slug)
                else:
                    patched = self._patch_page(doc, summary, op, manifest)
                    if patched:
                        result.patched.append(op.slug)
                    else:
                        result.skipped.append(op.slug)
            except VersionConflict as exc:
                # Another writer won. Skipping is correct: the next source that
                # touches this page will fold the content in.
                result.skipped.append(op.slug)
                result.reason = f"version conflict on {op.slug}: {exc}"
                logger.warning("version conflict: source_id=%s slug=%s %s",
                               doc.source_id, op.slug, exc)

        result.page_bodies_read = self._page_reads

    def _create_page(
        self,
        doc: ExtractedDoc,
        summary: SourceSummary,
        op: CompileOp,
        manifest: dict[str, PageGist],
    ) -> None:
        logger.debug("create_page: source_id=%s slug=%s", doc.source_id, op.slug)
        response = self.llm.complete(
            op="create_page",
            system=load_prompt("create_page"),
            prompt=(
                f"# Page to write\n\nTitle: {op.title or op.slug}\nSlug: {op.slug}\n"
                f"Why: {op.reason}\n\n## Source material\n\n{summary.summary}\n"
            ),
            schema=PAGE_SCHEMA,
        )
        self._account(response.usage)
        data = response.data or {}
        page = WikiPage(
            front_matter=PageFrontMatter(
                title=op.title or op.slug.replace("-", " ").title(),
                slug=op.slug,
                type=op.type,
                gist=data.get("gist", summary.gist)[:200],
                sources=[doc.source_id],
                updated=date.today(),
                version=0,
            ),
            body=append_sources_section(
                data.get("body", ""), [doc.source_id], {doc.source_id: doc.title}
            ),
        )
        write_page(self.store, page, expected_version=0)
        self._sync_gist(page, manifest)

    def _patch_page(
        self,
        doc: ExtractedDoc,
        summary: SourceSummary,
        op: CompileOp,
        manifest: dict[str, PageGist],
    ) -> bool:
        page = self._read_page(op.slug, manifest)
        if page is None:
            # The manifest and storage disagree; treat it as a create so the
            # source is not silently dropped.
            self._create_page(doc, summary, op, manifest)
            return True
        if doc.source_id in page.front_matter.sources:
            return False

        logger.debug("patch_page: source_id=%s slug=%s", doc.source_id, op.slug)
        response = self.llm.complete(
            op="patch_page",
            system=load_prompt("patch_page"),
            prompt=(
                f"# Current page\n\n{render_page(page)}\n\n"
                f"# New source: {doc.title}\n\nGist: {summary.gist}\n\n{summary.summary}\n\n"
                f"# Why this page\n\n{op.reason}\n"
            ),
            schema=PAGE_SCHEMA,
        )
        self._account(response.usage)
        data = response.data or {}
        body = data.get("body") or page.body
        sources = [*page.front_matter.sources, doc.source_id]

        page.front_matter.gist = (data.get("gist") or page.front_matter.gist)[:200]
        page.front_matter.sources = sources
        page.body = append_sources_section(body, sources, {doc.source_id: doc.title})
        write_page(self.store, page, expected_version=page.front_matter.version)
        self._sync_gist(page, manifest)
        return True

    # -- stage 5: record ----------------------------------------------------

    def _record(
        self, doc: ExtractedDoc, result: CompileResult, manifest: dict[str, PageGist]
    ) -> None:
        """Persist the manifest, index, source note and cost ledger. No LLM calls."""
        note = self._source_note(doc, result)
        self.store.put(wiki_source_note(doc.source_id), note.encode("utf-8"), "text/markdown")

        # The source note is a page, so it belongs in the manifest: a page that
        # storage has but the manifest does not is exactly what lint calls an
        # orphan. It gets no gist vector, so it never becomes a patch candidate.
        gists_mod.upsert_gist(
            manifest,
            PageGist(
                slug=doc.source_id,
                title=doc.title,
                type="source",
                gist=f"Captured source ({doc.modality})",
                sources=[doc.source_id],
                updated=date.today(),
                version=1,
            ),
        )
        gists_mod.save_gists(self.store, manifest)
        gists_mod.write_index(self.store, manifest)

        result.cost_usd = sum(record.cost_usd for record in self._costs)
        result.page_bodies_read = self._page_reads
        self._append_costs(doc.source_id)

    def _source_note(self, doc: ExtractedDoc, result: CompileResult) -> str:
        touched = sorted(set(result.created + result.patched))
        links = "\n".join(f"- [[{slug}]]" for slug in touched) or "- (no pages touched)"
        return (
            "---\n"
            f"title: {doc.title}\n"
            f"slug: {doc.source_id}\n"
            "type: source\n"
            f"gist: Captured source {doc.source_id} ({doc.modality}).\n"
            f"sources: [{doc.source_id}]\n"
            f"updated: {date.today().isoformat()}\n"
            "version: 1\n"
            "---\n\n"
            f"# {doc.title}\n\n"
            f"- Modality: {doc.modality}\n"
            f"- URL: {doc.url or 'n/a'}\n"
            f"- Raw object: `raw/{doc.source_id}/`\n\n"
            "## Pages compiled from this source\n\n"
            f"{links}\n"
        )

    # -- helpers ------------------------------------------------------------

    def _read_page(self, slug: str, manifest: dict[str, PageGist]) -> WikiPage | None:
        """The single counted path to a page body. Everything else uses gists."""
        page_type = manifest[slug].type if slug in manifest else "concept"
        self._page_reads += 1
        return read_page(self.store, slug, page_type)

    def _sync_gist(self, page: WikiPage, manifest: dict[str, PageGist]) -> None:
        """Refresh the manifest row and the page's gist vector, together."""
        fm = page.front_matter
        gist = PageGist(
            slug=fm.slug,
            title=fm.title,
            type=fm.type,
            gist=fm.gist,
            sources=fm.sources,
            updated=fm.updated,
            version=fm.version,
        )
        gists_mod.upsert_gist(manifest, gist)
        vector = self.embedder.embed([f"{gist.title}. {gist.gist}"])[0]
        self.vectors.upsert(
            self.settings.vectorize_gists_index,
            [gist.slug],
            [vector],
            [gists_mod.gist_vector_metadata(gist)],
        )

    @staticmethod
    def _already_compiled(manifest: dict[str, PageGist], source_id: str) -> bool:
        return any(source_id in gist.sources for gist in manifest.values())

    def _account(self, usage: CostRecord | None) -> None:
        if usage is not None:
            self._costs.append(usage)

    def _check_budget(self) -> None:
        spent = sum(
            record.input_tokens + record.output_tokens + record.cache_read_tokens
            for record in self._costs
        )
        if spent > self.settings.ingest_token_budget:
            raise TokenBudgetExceeded(
                f"ingest exceeded INGEST_TOKEN_BUDGET ({spent} > "
                f"{self.settings.ingest_token_budget} tokens); source marked needs_review"
            )

    def _append_costs(self, source_id: str) -> None:
        """Append to ``wiki/_meta/cost.jsonl``.

        Object stores have no append, so this is read-modify-write.  Fine for a
        single-writer Phase 0; a concurrent writer would need per-day ledger
        objects instead.
        """
        if not self._costs:
            return
        try:
            existing = self.store.get(COST_KEY).decode("utf-8")
        except ObjectNotFound:
            existing = ""
        lines = [
            record.model_copy(update={"source_id": source_id}).model_dump_json()
            for record in self._costs
        ]
        payload = existing + ("" if existing.endswith("\n") or not existing else "\n")
        payload += "\n".join(lines) + "\n"
        self.store.put(COST_KEY, payload.encode("utf-8"), "application/x-ndjson")


def read_cost_ledger(store: ObjectStore) -> list[CostRecord]:
    """Parse the cost ledger. Malformed lines are skipped, not fatal."""
    try:
        raw = store.get(COST_KEY).decode("utf-8")
    except ObjectNotFound:
        return []
    records = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            records.append(CostRecord(**json.loads(line)))
        except Exception:
            continue
    return records
