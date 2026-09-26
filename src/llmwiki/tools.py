"""The canonical tool surface - the shared brain (plan 10).

Seven tools are exposed to agents over MCP: ``search_wiki``, ``get_page``,
``ingest_source``, ``compile_update``, ``list_concepts``, ``lint_wiki`` and -
since Phase 2 (design v1.4 §4.10, plan §21.2 X3) - the read-only
``list_domains``, without which an agent could not discover what to pass as
``domain``.

Four more live here for the CLI, ``/healthz`` and the smoke script but are
deliberately *not* MCP tools, keeping the agent-facing surface to the six the
design doc names: ``get_source_status``, ``answer``, ``source_exists``,
``cost_summary``.

Every transport calls these functions and nothing else.  That is what makes the
FastAPI and MCP layers thin enough to be free.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from llmwiki import factory
from llmwiki.agent.judge import Judge
from llmwiki.agent.query import QueryAgent
from llmwiki.config import Settings
from llmwiki.config import settings as default_settings
from llmwiki.extractors.base import ExtractionError  # noqa: F401  (re-exported, see below)
from llmwiki.models.chunk import SearchHit
from llmwiki.models.page import Domain, LintReport, PageGist, WikiPage
from llmwiki.models.plan import (
    Answer,
    CompileResult,
    CostSummary,
    SynthesisResult,
    Verdict,
    WorkerStatus,
)
from llmwiki.models.source import SourceRef, SourceStatus
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.base import ObjectNotFound
from llmwiki.storage.layout import GENERAL, domain_index_name, is_source_id
from llmwiki.wiki import domains as domains_mod
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki import lint as lint_mod
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.pages import read_page

# ``ExtractionError`` is re-exported here for transports (api/, channels/ -
# which the layering rule keeps away from extractors/) so a failed URL fetch
# inside ingest_source can become a clean client-facing failure instead of a
# 500. Telegram in particular re-delivers any update not acked with a 2xx, so
# an unhandled fetch error there is a retry storm against the very site that
# just refused us.

logger = logging.getLogger(__name__)


class PageNotFound(KeyError):
    """Raised by :func:`get_page` when a slug has no stored page."""


# The LangSmith feedback key a human correction is filed under - the same
# string scripts/eval_answer.py --promote-feedback looks for.
FEEDBACK_KEY = "correctness"


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
    return IngestPipeline(store, vectors, embedder, llm, cfg, lexical=factory.lexical_index(cfg))


def _agent(cfg: Settings | None = None) -> QueryAgent:
    store, vectors, embedder, llm, cfg = _components(cfg)
    return QueryAgent(store, vectors, embedder, llm, cfg, web_searcher=factory.web_searcher(cfg),
                      lexical=factory.lexical_index(cfg), reranker=factory.reranker(cfg))


# --- the six canonical tools ----------------------------------------------


def search_wiki(
    query: str, k: int = 5, domain: str | None = None, cfg: Settings | None = None,
) -> list[SearchHit]:
    """Search the knowledge base: compiled wiki pages first, raw chunks as fallback.

    ``domain`` (Phase 2) scopes the search to one domain; otherwise
    ``QUERY_DOMAIN_POLICY`` decides. Under the ``routed`` policy this makes
    one model call, which is why the search is metered like ``answer``.
    """
    from llmwiki.llm.metering import collect_usage

    with collect_usage() as records:
        hits = _agent(cfg).search(query, k=k, domain=domain)
    _record_costs(records, kind="query", cfg=cfg)
    return hits


def get_page(slug: str, domain: str | None = None, cfg: Settings | None = None) -> WikiPage:
    """Return one wiki page in full, front matter and body.

    ``domain`` (Phase 2) names which domain's folder to look in; ``None`` is
    ``general``, so every pre-Phase-2 caller reads exactly what it always did.
    """
    store, _, _, _, cfg = _components(cfg)
    domain = _resolve_domain(store, domain)
    manifest = gists_mod.load_gists(store, domain)
    page_type = manifest[slug].type if slug in manifest else "concept"
    page = read_page(store, slug, page_type, domain)
    if page is None and page_type == "concept":
        # A slug absent from the manifest could still be an entity page or a
        # source note. Source notes are keyed by source_id, so only try that
        # folder when the slug actually looks like one.
        page = read_page(store, slug, "entity", domain)
        if page is None and _looks_like_source_id(slug):
            page = read_page(store, slug, "source", domain)
    if page is None:
        raise PageNotFound(slug)
    return page


def _resolve_domain(store, domain: str | None) -> str:  # type: ignore[no-untyped-def]
    """``None`` → general; anything else must be general or registered (``UnknownDomain``)."""
    if domain is None or domain == GENERAL:
        return GENERAL
    return domains_mod.require_domain(domains_mod.load_registry(store), domain)


def _looks_like_source_id(slug: str) -> bool:
    return is_source_id(slug)


def ingest_source(
    url: str | None = None,
    file: bytes | None = None,
    filename: str | None = None,
    mime: str = "",
    title: str = "",
    text: str | None = None,
    domain: str | None = None,
    cfg: Settings | None = None,
) -> SourceRef:
    """Capture a source. Returns immediately; compilation happens in the background.

    Exactly one of ``url`` (blog post, YouTube video, or a direct link to a
    PDF), ``file`` (uploaded PDF or text file) or ``text`` (a string stored
    verbatim). The modality is detected, never declared by the caller.
    ``domain`` (Phase 2) files the source under a registered domain explicitly;
    left ``None``, processing decides.
    """
    return _pipeline(cfg).capture(
        url=url, file=file, filename=filename, mime=mime, title=title, text=text,
        domain=domain,
    )


def compile_update(
    source_id: str, force: bool = False, cfg: Settings | None = None
) -> CompileResult:
    """Re-run the incremental compiler for one already-captured source.

    The source stays in the domain it was routed to (``raw/{id}/routing.json``);
    a source captured before Phase 2 is ``general``.
    """
    store, vectors, embedder, llm, cfg = _components(cfg)
    pipeline = _pipeline(cfg)
    meta = pipeline.load_meta(source_id)
    doc = pipeline.extract(meta)
    domain = pipeline.load_routing(source_id).domain
    return Compiler(store, vectors, embedder, llm, cfg, lexical=pipeline.lexical).compile_source(
        doc, force=force, domain=domain,
    )


def list_concepts(
    prefix: str | None = None, domain: str | None = None, cfg: Settings | None = None,
) -> list[PageGist]:
    """List one domain's page gists. Reads that ``gists.json`` only - no LLM call, no bodies."""
    store, _, _, _, cfg = _components(cfg)
    manifest = gists_mod.load_gists(store, _resolve_domain(store, domain))
    rows = sorted(manifest.values(), key=lambda gist: gist.slug)
    if prefix:
        rows = [gist for gist in rows if gist.slug.startswith(prefix)]
    return rows


def lint_wiki(
    dry_run: bool = True, domain: str | None = None, cfg: Settings | None = None,
) -> LintReport:
    """Run the global lint - every domain, or one. Scheduled or manual only, never on ingest."""
    store, _, _, _, cfg = _components(cfg)
    return lint_mod.lint_wiki(store, dry_run=dry_run, domain=domain)


def list_domains(cfg: Settings | None = None) -> list[Domain]:
    """The domain registry, ``general`` first. One object read; no LLM call (Phase 2)."""
    store, _, _, _, cfg = _components(cfg)
    registry = domains_mod.load_registry(store)
    general = Domain(name=GENERAL, description="Everything not filed under a registered domain")
    return [general, *(registry.domains[name] for name in registry.names() if name != GENERAL)]


def upsert_domain(name: str, description: str = "", cfg: Settings | None = None) -> Domain:
    """Register a domain (or update its description) and make sure its indexes exist.

    Admin-only by construction: not an MCP tool; REST needs the bearer token.
    Creating the two vector indexes here, at registration, is what lets the
    first compile into the domain proceed without a setup step (plan §21.2 A2).
    """
    store, vectors, _, _, cfg = _components(cfg)
    domain = domains_mod.upsert_domain(store, name, description)
    for base in (cfg.vectorize_gists_index, cfg.vectorize_chunks_index):
        vectors.ensure_index(domain_index_name(base, name))
    return domain


def remove_domain(name: str, force: bool = False, cfg: Settings | None = None) -> bool:
    """Unregister a domain. Refuses while it still holds pages unless ``force``."""
    store, _, _, _, cfg = _components(cfg)
    return domains_mod.remove_domain(store, name, force=force)


# --- helpers, not exposed over MCP ----------------------------------------


def process_source(source_id: str, cfg: Settings | None = None) -> SourceStatus:
    """Run the expensive half of ingest, in the caller's thread.

    Per-domain serialization holds regardless of who calls (the pipeline takes
    ``domain_lock`` itself), so the MCP tool and the CLI can keep calling this
    directly. REST and the channels go through :func:`enqueue_source`.
    """
    status = _pipeline(cfg).process(source_id)
    # The compiler and the router ledgered their own spend; this is where the
    # alert guard sees it (one refresh-if-stale per ingest).
    _evaluate_alerts(0.0, cfg)
    return status


def enqueue_source(source_id: str, cfg: Settings | None = None) -> None:
    """Hand one captured source to the ingest worker (Phase 2, plan §21.2 C3).

    ``WORKER_MODE=threads`` returns at once; ``inline`` processes before
    returning, which is what keeps the REST tests and ``TestClient``'s
    run-background-tasks-before-returning semantics deterministic.
    """
    factory.compile_worker(cfg).submit(source_id)


def worker_status(cfg: Settings | None = None) -> WorkerStatus:
    """What the ingest worker is doing: queued, in flight, parked, paused-why."""
    return factory.compile_worker(cfg).status()


def recover_pending(cfg: Settings | None = None) -> list[str]:
    """Resubmit every source whose processing a restart interrupted. Called at startup."""
    return factory.compile_worker(cfg).recover()


def resume_processing(cfg: Settings | None = None) -> list[str]:
    """Retry sources parked under the cost cap now, rather than at the next timer tick."""
    return factory.compile_worker(cfg).resume()


def shutdown_worker(cfg: Settings | None = None) -> None:
    """Drain and stop the ingest worker. Called by the app's lifespan on shutdown."""
    factory.compile_worker(cfg).shutdown(wait=True)


def processing_capped(cfg: Settings | None = None) -> bool:
    """Whether post-capture processing is paused by the monthly hard cap (plan §21.2 C5)."""
    try:
        return bool(factory.cost_alerts(cfg).capped())
    except Exception as exc:  # pragma: no cover - an unreadable ledger must not stop ingest
        logger.warning("cost cap check skipped: %s", exc)
        return False


def budget_status(cfg: Settings | None = None) -> dict:
    """Month/day spend against the thresholds, the cap state and what already fired."""
    return factory.cost_alerts(cfg).status()


def usage_summary(
    since: datetime | None = None,
    until: datetime | None = None,
    domain: str | None = None,
    month: str | None = None,
    cfg: Settings | None = None,
) -> CostSummary:
    """Spend broken down by model, op, domain, kind, day and source (Phase 2, plan §21.6.6).

    ``month`` (``YYYY-MM``) is the usual window; with nothing given the
    current month to date. Reads only the month prefixes the window covers.
    """
    from llmwiki.wiki.ledger import summarize

    if month is not None:
        from llmwiki.storage.layout import cost_month_prefix

        cost_month_prefix(month)  # validates YYYY-MM by name
        year, mon = (int(part) for part in month.split("-"))
        since = datetime(year, mon, 1, tzinfo=UTC)
        until = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=UTC)
    elif since is None and until is None:
        now = datetime.now(UTC)
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        until = now
    records = factory.ledger(cfg).read(since=since, until=until, domain=domain)
    summary = summarize(records)
    summary.since, summary.until = since, until
    return summary


def migrate_cost_ledger(cfg: Settings | None = None) -> int:
    """Move the legacy single-file ledger into partitioned keys (one-shot; idempotent)."""
    return factory.ledger(cfg).migrate_legacy()


def ingest_now(
    url: str | None = None,
    file: bytes | None = None,
    filename: str | None = None,
    mime: str = "",
    title: str = "",
    text: str | None = None,
    domain: str | None = None,
    cfg: Settings | None = None,
) -> SourceStatus:
    """Capture *and* process one source synchronously.

    Used by the CLI and the smoke script, where waiting is the point. The API
    uses ``ingest_source`` plus a background task instead.
    """
    return _pipeline(cfg).ingest_now(
        url=url, file=file, filename=filename, mime=mime, title=title, text=text,
        domain=domain,
    )


def get_source_status(source_id: str, cfg: Settings | None = None) -> SourceStatus:
    """Pipeline state for one source."""
    return _pipeline(cfg).get_status(source_id)


def answer(
    query: str, k: int = 5, domain: str | None = None, cfg: Settings | None = None,
) -> Answer:
    """Answer a question with citations that are verified to resolve.

    Every LLM call the query graph makes is metered (Phase 2, plan §21.2 C2):
    the sum is ``Answer.cost_usd`` and the records go to the ledger as
    ``kind="query"``. Recording never fails an answer - a ledger write error
    is logged and the answer is returned regardless.
    """
    from llmwiki.llm.metering import collect_usage

    with collect_usage() as records:
        result = _agent(cfg).answer(query, k=k, domain=domain)
    result.cost_usd = sum(record.cost_usd for record in records)
    _record_costs(records, kind="query", cfg=cfg)
    return result


def _record_costs(records: list, *, kind: str, cfg: Settings | None) -> None:
    if not records:
        return
    try:
        factory.ledger(cfg).append(records, kind=kind)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - storage trouble must not fail a query
        logger.warning("cost ledger append (%s) failed: %s", kind, exc)
        return
    _evaluate_alerts(sum(record.cost_usd for record in records), cfg)


def _evaluate_alerts(added_usd: float, cfg: Settings | None) -> None:
    """Run the cost guard after spend was recorded (Phase 2, plan §21.2 C4). Never raises."""
    try:
        factory.cost_alerts(cfg).evaluate(added_usd)
    except Exception as exc:  # pragma: no cover - alerts must never fail the work that fired them
        logger.warning("cost alerts skipped: %s", exc)


def source_exists(source_id: str, cfg: Settings | None = None) -> bool:
    """True if a citation resolves to a real object under ``raw/``."""
    return _agent(cfg).source_exists(source_id)


def judge_answer(question: str, answer_text: str, context: str,
                 cfg: Settings | None = None) -> Verdict:
    """Grade an answer's groundedness in its own retrieved context (eval only).

    Phase 1-D (design §4.9). Not an MCP tool and never called on the query
    path - ``scripts/eval_answer.py --judge`` is the consumer.
    """
    from llmwiki.llm.metering import collect_usage

    _, _, _, llm, _ = _components(cfg)
    with collect_usage() as records:
        verdict = Judge(llm).grade(question, answer_text, context)
    _record_costs(records, kind="eval", cfg=cfg)
    return verdict


def record_feedback(run_id: str, score: float, correction: str = "",
                    cfg: Settings | None = None) -> str:
    """Attach a human correction to the LangSmith run that produced an answer.

    The human-in-the-loop half of the correction loop (design §4.9): "this
    answer is wrong, here is the right one". Needs ``LANGSMITH_TRACING=true``
    (there is no run to attach to otherwise) - raises ``RuntimeError`` naming
    the setting rather than silently dropping the correction. ``langsmith`` is
    imported here, function-locally, so nothing else pays for it. Returns the
    feedback id.
    """
    cfg = cfg or default_settings
    if not cfg.langsmith_tracing:
        raise RuntimeError(
            "record_feedback needs LANGSMITH_TRACING=true: without tracing no run "
            "exists to attach the correction to"
        )
    cfg.require("langsmith_api_key")
    from langsmith import Client

    client = Client(
        api_key=cfg.langsmith_api_key.get_secret_value(),
        api_url=cfg.langsmith_endpoint or None,
    )
    feedback = client.create_feedback(
        run_id, key=FEEDBACK_KEY, score=score, comment=correction or None,
    )
    return str(feedback.id)


def cost_summary(since: datetime | None = None, cfg: Settings | None = None) -> CostSummary:
    """Aggregate the measured cost ledger from ``since`` (or everything).

    Reads the partitioned ledger (Phase 2, plan §21.2 C1) plus the legacy
    single file until it is migrated; a ``since`` bounds the read to the months
    it covers.
    """
    from llmwiki.wiki.ledger import summarize

    records = factory.ledger(cfg).read(since=since)
    return summarize(records)


def delete_source(source_id: str, cfg: Settings | None = None) -> int:
    """Remove a source and its vectors. Used by the smoke script's cleanup.

    Leaves compiled wiki pages alone: they are synthesized text, and deleting a
    page because one of its sources went away would lose the other sources' work.
    """
    store, vectors, embedder, llm, cfg = _components(cfg)
    # The domain's chunk index is the one that holds the vectors; read it
    # before raw/ (and routing.json with it) is deleted.
    domain = IngestPipeline(store, vectors, embedder, llm, cfg).load_routing(source_id).domain
    removed = 0
    for key in store.list(f"raw/{source_id}/"):
        store.delete(key)
        removed += 1
    try:
        store.delete(f"status/{source_id}.json")
    except ObjectNotFound:  # pragma: no cover - delete is already idempotent
        pass
    chunk_index = domain_index_name(cfg.vectorize_chunks_index, domain)
    vectors.delete_by_source(chunk_index, source_id)
    lexical = factory.lexical_index(cfg)
    if lexical is not None:
        lexical.delete_by_source(chunk_index, source_id)
    return removed


def synthesize(domain: str | None = None, cfg: Settings | None = None) -> list[SynthesisResult]:
    """Write or refresh the overview page of one domain, or of every domain (Phase 2).

    Scheduled or manual only - never on the ingest path. Holds the domain's
    lock while it writes the manifest, like a compile does.
    """
    from llmwiki.pipeline.worker import domain_lock
    from llmwiki.wiki.synthesis import synthesize_domain

    store, vectors, embedder, llm, cfg = _components(cfg)
    registry = domains_mod.load_registry(store)
    names = registry.names() if domain is None else [domains_mod.require_domain(registry, domain)]
    results = []
    for name in names:
        with domain_lock(name):
            results.append(synthesize_domain(store, vectors, embedder, llm, cfg, name,
                                             lexical=factory.lexical_index(cfg)))
    _evaluate_alerts(sum(r.cost_usd for r in results), cfg)
    return results


def rebuild_lexical(domain: str | None = None, cfg: Settings | None = None) -> dict[str, int]:
    """Rebuild the keyword index from ``raw/`` and the manifests (Phase 2, plan §21.2 B2).

    Raises by name when ``LEXICAL_BACKEND=none`` - there is nothing to build.
    """
    from llmwiki.pipeline.lexical_rebuild import rebuild

    store, _, _, _, cfg = _components(cfg)
    lexical = factory.lexical_index(cfg)
    if lexical is None:
        raise RuntimeError("LEXICAL_BACKEND=none: there is no lexical index to rebuild")
    if domain is not None:
        domain = _resolve_domain(store, domain)
    return rebuild(store, lexical, cfg, domain=domain)


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
            # Phase 2: the keyword half of hybrid retrieval, and the reranker.
            "lexical": cfg.lexical_backend,
            "reranker": cfg.reranker_backend,
        },
        "lexical": {
            "backend": cfg.lexical_backend,
            "path": str(cfg.lexical_root.resolve()) if cfg.lexical_backend == "sqlite" else None,
        },
        # Phase 2: the thresholds in force (never the spend - that needs storage,
        # and /healthz must not touch it).
        "budget": {
            "daily_alert_usd": cfg.cost_alert_daily_usd,
            "monthly_alert_usd": cfg.cost_alert_monthly_usd,
            "hard_cap_usd": cfg.cost_hard_cap_monthly_usd,
            "notify": cfg.notify_backend,
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
        # op -> "provider/model" when the table is in force; {} otherwise. Read
        # by scripts/eval_answer.py so an experiment records which model
        # answered - and is the quickest way to see on a box which model each
        # stage actually uses.
        "routes": _routes_in_force(cfg) if routed else {},
        "providers_config": {"path": str(providers_config), "present": providers_config.exists()},
        "ops_config": {"path": str(ops_config), "present": ops_config.exists()},
        "skills_dir": {
            "path": str(skills_dir),
            "present": skills_dir.is_dir(),
            "skill_files": skill_files,
        },
        # Phase 1-D: the query graph's bounds, echoed for the same reason as
        # log_level above - what the running process actually has.
        "query_graph": {
            "max_tool_calls": cfg.agent_max_tool_calls,
            "web_search_policy": cfg.agent_web_search_policy,
            "web_search_backend": cfg.web_search_backend,
            "langsmith_tracing": cfg.langsmith_tracing,
        },
    }


def _routes_in_force(cfg: Settings) -> dict[str, str]:
    """``op -> "provider/model"`` from the routing table; ``{}`` if it fails to load.

    Never raises: /healthz must answer even when the table is broken - the
    process would not have started in that case anyway (the factory validates
    it first), so an empty dict here is only ever a transient-state report.
    """
    from llmwiki.llm.routing_config import load_routing_config

    try:
        routing = load_routing_config(cfg.llm_providers_config, cfg.llm_ops_config)
    except RuntimeError:
        return {}
    if routing is None:
        return {}
    return {op: f"{route.provider}/{route.model}" for op, route in sorted(routing.ops.items())}
