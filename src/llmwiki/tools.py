"""The canonical tool surface - the shared brain (plan 10).

Six tools are exposed to agents over MCP: ``search_wiki``, ``get_page``,
``ingest_source``, ``compile_update``, ``list_concepts``, ``lint_wiki``.

Four more live here for the CLI, ``/healthz`` and the smoke script but are
deliberately *not* MCP tools, keeping the agent-facing surface to the six the
design doc names: ``get_source_status``, ``answer``, ``source_exists``,
``cost_summary``.

Every transport calls these functions and nothing else.  That is what makes the
FastAPI and MCP layers thin enough to be free.
"""

from __future__ import annotations

from datetime import datetime

from llmwiki import factory
from llmwiki.agent.query import QueryAgent
from llmwiki.config import Settings
from llmwiki.config import settings as default_settings
from llmwiki.models.chunk import SearchHit
from llmwiki.models.page import LintReport, PageGist, WikiPage
from llmwiki.models.plan import Answer, CompileResult, CostSummary
from llmwiki.models.source import SourceRef, SourceStatus
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.base import ObjectNotFound
from llmwiki.storage.layout import is_source_id
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki import lint as lint_mod
from llmwiki.wiki.compiler import Compiler, read_cost_ledger
from llmwiki.wiki.pages import read_page


class PageNotFound(KeyError):
    """Raised by :func:`get_page` when a slug has no stored page."""


def _components(cfg: Settings | None = None) -> tuple:
    cfg = cfg or default_settings
    return (
        factory.object_store(cfg),
        factory.vector_store(cfg),
        factory.embedder(cfg),
        factory.llm_client(cfg),
        cfg,
    )


def _pipeline(cfg: Settings | None = None) -> IngestPipeline:
    store, vectors, embedder, llm, cfg = _components(cfg)
    return IngestPipeline(store, vectors, embedder, llm, cfg)


def _agent(cfg: Settings | None = None) -> QueryAgent:
    store, vectors, embedder, llm, cfg = _components(cfg)
    return QueryAgent(store, vectors, embedder, llm, cfg)


# --- the six canonical tools ----------------------------------------------


def search_wiki(query: str, k: int = 5, cfg: Settings | None = None) -> list[SearchHit]:
    """Search the knowledge base: compiled wiki pages first, raw chunks as fallback."""
    return _agent(cfg).search(query, k=k)


def get_page(slug: str, cfg: Settings | None = None) -> WikiPage:
    """Return one wiki page in full, front matter and body."""
    store, _, _, _, cfg = _components(cfg)
    manifest = gists_mod.load_gists(store)
    page_type = manifest[slug].type if slug in manifest else "concept"
    page = read_page(store, slug, page_type)
    if page is None and page_type == "concept":
        # A slug absent from the manifest could still be an entity page or a
        # source note. Source notes are keyed by source_id, so only try that
        # folder when the slug actually looks like one.
        page = read_page(store, slug, "entity")
        if page is None and _looks_like_source_id(slug):
            page = read_page(store, slug, "source")
    if page is None:
        raise PageNotFound(slug)
    return page


def _looks_like_source_id(slug: str) -> bool:
    return is_source_id(slug)


def ingest_source(
    url: str | None = None,
    file: bytes | None = None,
    filename: str | None = None,
    mime: str = "",
    title: str = "",
    text: str | None = None,
    cfg: Settings | None = None,
) -> SourceRef:
    """Capture a source. Returns immediately; compilation happens in the background.

    Exactly one of ``url`` (blog post, YouTube video, or a direct link to a
    PDF), ``file`` (uploaded PDF or text file) or ``text`` (a string stored
    verbatim). The modality is detected, never declared by the caller.
    """
    return _pipeline(cfg).capture(
        url=url, file=file, filename=filename, mime=mime, title=title, text=text
    )


def compile_update(
    source_id: str, force: bool = False, cfg: Settings | None = None
) -> CompileResult:
    """Re-run the incremental compiler for one already-captured source."""
    store, vectors, embedder, llm, cfg = _components(cfg)
    pipeline = IngestPipeline(store, vectors, embedder, llm, cfg)
    meta = pipeline.load_meta(source_id)
    doc = pipeline.extract(meta)
    return Compiler(store, vectors, embedder, llm, cfg).compile_source(doc, force=force)


def list_concepts(prefix: str | None = None, cfg: Settings | None = None) -> list[PageGist]:
    """List page gists. Reads ``gists.json`` only - no LLM call, no page bodies."""
    store, _, _, _, cfg = _components(cfg)
    manifest = gists_mod.load_gists(store)
    rows = sorted(manifest.values(), key=lambda gist: gist.slug)
    if prefix:
        rows = [gist for gist in rows if gist.slug.startswith(prefix)]
    return rows


def lint_wiki(dry_run: bool = True, cfg: Settings | None = None) -> LintReport:
    """Run the global lint. Scheduled or manual only - never on the ingest path."""
    store, _, _, _, cfg = _components(cfg)
    return lint_mod.lint_wiki(store, dry_run=dry_run)


# --- helpers, not exposed over MCP ----------------------------------------


def process_source(source_id: str, cfg: Settings | None = None) -> SourceStatus:
    """Run the expensive half of ingest. Called by the background worker."""
    return _pipeline(cfg).process(source_id)


def ingest_now(
    url: str | None = None,
    file: bytes | None = None,
    filename: str | None = None,
    mime: str = "",
    title: str = "",
    text: str | None = None,
    cfg: Settings | None = None,
) -> SourceStatus:
    """Capture *and* process one source synchronously.

    Used by the CLI and the smoke script, where waiting is the point. The API
    uses ``ingest_source`` plus a background task instead.
    """
    return _pipeline(cfg).ingest_now(
        url=url, file=file, filename=filename, mime=mime, title=title, text=text
    )


def get_source_status(source_id: str, cfg: Settings | None = None) -> SourceStatus:
    """Pipeline state for one source."""
    return _pipeline(cfg).get_status(source_id)


def answer(query: str, k: int = 5, cfg: Settings | None = None) -> Answer:
    """Answer a question with citations that are verified to resolve."""
    return _agent(cfg).answer(query, k=k)


def source_exists(source_id: str, cfg: Settings | None = None) -> bool:
    """True if a citation resolves to a real object under ``raw/``."""
    return _agent(cfg).source_exists(source_id)


def cost_summary(since: datetime | None = None, cfg: Settings | None = None) -> CostSummary:
    """Aggregate the measured cost ledger."""
    store, _, _, _, cfg = _components(cfg)
    records = read_cost_ledger(store)
    if since is not None:
        records = [record for record in records if record.at >= since]
    summary = CostSummary(call_count=len(records))
    for record in records:
        summary.total_usd += record.cost_usd
        summary.input_tokens += record.input_tokens
        summary.output_tokens += record.output_tokens
        summary.cache_read_tokens += record.cache_read_tokens
        summary.by_model[record.model] = summary.by_model.get(record.model, 0.0) + record.cost_usd
    return summary


def delete_source(source_id: str, cfg: Settings | None = None) -> int:
    """Remove a source and its vectors. Used by the smoke script's cleanup.

    Leaves compiled wiki pages alone: they are synthesized text, and deleting a
    page because one of its sources went away would lose the other sources' work.
    """
    store, vectors, _, _, cfg = _components(cfg)
    removed = 0
    for key in store.list(f"raw/{source_id}/"):
        store.delete(key)
        removed += 1
    try:
        store.delete(f"status/{source_id}.json")
    except ObjectNotFound:  # pragma: no cover - delete is already idempotent
        pass
    vectors.delete_by_source(cfg.vectorize_chunks_index, source_id)
    return removed


def health(cfg: Settings | None = None) -> dict:
    """Service health plus the configured backends. Never touches the network."""
    from llmwiki import __version__

    cfg = cfg or default_settings
    return {
        "status": "ok",
        "version": __version__,
        "backends": {
            "storage": cfg.storage_backend,
            "vector": cfg.vector_backend,
            "embedding": cfg.embedding_backend,
            "llm": cfg.llm_provider,
        },
        "models": {
            "default": cfg.llm_model,
            "embedding": cfg.embedding_model,
        },
        # Echoed back deliberately, and not a secret. A container's environment
        # is baked in at create time, so an edited .env that was never applied
        # (`compose restart` instead of `compose up -d`) is otherwise invisible
        # from outside the box - this makes one unauthenticated curl enough to
        # tell which configuration the running process actually has.
        "log_level": cfg.log_level,
        "config": _config_state(cfg),
    }


def _config_state(cfg: Settings) -> dict:
    """Which optional config files the process actually found, by resolved path.

    These three paths live *outside* the package (design v1.4 4.8/4.8.2), so
    unlike ``chains/prompts/*.md`` they are not package data and do not travel
    with a ``pip install``.  Absence of each is a legitimate, documented
    configuration - the single-provider fallback and the fixed answer_query
    prompt - which is exactly what makes a *mistaken* absence so quiet: the
    service is healthy, answers questions, and silently ignores the routing
    table and skills the operator believes are in force.

    That happened on the first Hostinger deploy (2026-09-10): the container had
    no ``config/`` or ``skills/`` at all, so every request took the fallback
    path.  Paths are reported ``resolve()``d because they are relative by
    default and therefore mean different things depending on the working
    directory - ``./skills`` is ``/app/skills`` under the container's WORKDIR,
    and seeing that spelled out is most of the diagnosis.
    """
    providers_config = cfg.llm_providers_config.resolve()
    ops_config = cfg.llm_ops_config.resolve()
    skills_dir = cfg.agent_skills_dir.resolve()
    routed = providers_config.exists() and ops_config.exists()
    # Counts files rather than parsing them: /healthz is polled by the Docker
    # HEALTHCHECK every 30s, and discover_skills() would re-read and re-validate
    # every file on each poll, logging a warning per malformed file each time.
    skill_files = len(list(skills_dir.glob("*.md"))) if skills_dir.is_dir() else 0
    return {
        "llm_routing": "per-op table" if routed else "single-provider fallback",
        "providers_config": {"path": str(providers_config), "present": providers_config.exists()},
        "ops_config": {"path": str(ops_config), "present": ops_config.exists()},
        "skills_dir": {
            "path": str(skills_dir),
            "present": skills_dir.is_dir(),
            "skill_files": skill_files,
        },
    }
