"""Ingest orchestration: capture, extract, embed, compile.

``capture`` is synchronous and cheap - it writes the immutable ``raw/`` objects
and returns a :class:`SourceRef` so the caller is not blocked.  ``process`` is
the expensive half and runs in the background (plan 9).

Failures are recorded on the source's status rather than raised: one corrupt PDF
must not take down the worker or lose the ten sources behind it in the queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.extractors.base import ExtractionError, detect_modality, get_extractor
from llmwiki.lexical.base import LexicalIndex
from llmwiki.llm.base import LLMClient
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import (
    DomainAssignment,
    ExtractedDoc,
    Modality,
    SourceMeta,
    SourceRef,
    SourceState,
    SourceStatus,
)
from llmwiki.pipeline.chunker import chunk_document
from llmwiki.pipeline.worker import clear_pending, domain_lock, mark_pending
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    GENERAL,
    content_hash_for_bytes,
    content_hash_for_url,
    domain_index_name,
    ext_for,
    raw_extracted,
    raw_meta,
    raw_original,
    raw_prefix_for_hash,
    raw_routing,
    source_id_for,
    source_id_from_key,
    status_key,
)
from llmwiki.vector.base import VectorStore
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import load_registry, require_domain
from llmwiki.wiki.ledger import CostLedger

logger = logging.getLogger(__name__)


class IngestPipeline:
    """Wires the layers into the one-directional flow from the design doc."""

    def __init__(
        self,
        store: ObjectStore,
        vectors: VectorStore,
        embedder: Embedder,
        llm: LLMClient,
        settings: Settings,
        lexical: LexicalIndex | None = None,
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.settings = settings
        # Phase 2 (plan §21.2 B1): the keyword half of hybrid retrieval; None
        # means LEXICAL_BACKEND=none and nothing lexical is written.
        self.lexical = lexical
        # Usage of the last extract()'s image descriptions (plan §21.2 D4) -
        # handed to the compiler so INGEST_TOKEN_BUDGET covers vision too.
        self._vision_costs: list[CostRecord] = []

    # -- capture ------------------------------------------------------------

    def capture(
        self,
        *,
        url: str | None = None,
        file: bytes | None = None,
        filename: str | None = None,
        mime: str = "",
        title: str = "",
        text: str | None = None,
        domain: str | None = None,
    ) -> SourceRef:
        """Write the immutable raw objects. Returns immediately; nothing is compiled yet.

        Exactly one of three inputs names the source, which between them cover
        the five capture kinds:

        * ``url`` - a blog post, a YouTube video, or a direct link to a PDF.
          Which one it is comes from the URL shape and the served content type,
          not from the caller (see :func:`detect_modality`).
        * ``file`` - uploaded bytes: a PDF, or a ``.txt``/``.md`` text file.
        * ``text`` - a string pasted straight in, stored verbatim as its own
          immutable source.

        ``domain`` (Phase 2, plan §21.2 A4) is the caller's explicit choice of
        where the source is filed; it must be ``general`` or a registered
        domain, and it is recorded on the immutable ``meta.json`` so the router
        never second-guesses it. ``None`` leaves the decision to processing.
        """
        content_hash = self._content_hash(url=url, file=file, text=text)
        if domain is not None:
            # Validated before any fetch or write: an unknown domain is a
            # caller error, and must not leave a half-captured source behind.
            domain = require_domain(load_registry(self.store), domain)

        # Checked before any fetch: a URL already captured must not be pulled
        # over the network a second time just to be discarded as a duplicate.
        # A prefix list rather than a HEAD because the slug half of the id is
        # not known yet (for a URL it comes from the page title) and must not
        # matter: the same bytes under a new filename are the same source.
        existing = self._existing_source(content_hash)
        if existing is not None:
            logger.info("capture: source_id=%s duplicate, skipping", existing)
            return SourceRef(source_id=existing, status="done", duplicate=True)

        if text is not None:
            data = text.encode("utf-8")
            resolved_mime = mime or "text/plain"
            modality: Modality = "text"
        elif file is not None:
            data = file
            resolved_mime = mime or "application/octet-stream"
            modality = detect_modality(resolved_mime, filename, None)
        else:
            assert url is not None
            data, resolved_mime = self._fetch(url, detect_modality(mime, filename, url))
            # Re-detected against what the server actually served: a link to a
            # PDF and a link to a blog post are indistinguishable until then.
            modality = detect_modality(resolved_mime or mime, filename, url)

        if not title:
            title = _title_from_source(modality, url, data)
        source_id = source_id_for(content_hash, _slug_basis(title, filename, url))

        meta = SourceMeta(
            source_id=source_id,
            modality=modality,
            title=title,
            url=url,
            filename=filename,
            mime=resolved_mime,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            domain=domain,
        )
        extension = ext_for(resolved_mime, filename, url)
        self.store.put(raw_original(source_id, extension), data, resolved_mime)
        self.store.put(
            raw_meta(source_id), meta.model_dump_json(indent=2).encode(), "application/json"
        )
        self.set_status(SourceStatus(source_id=source_id, state="queued"))
        # Owed work survives a restart (Phase 2, plan §21.2 C3): the worker's
        # recover() resubmits every marker still present at startup.
        mark_pending(self.store, source_id)
        logger.info("capture: source_id=%s modality=%s queued", source_id, modality)
        return SourceRef(source_id=source_id, status="queued")

    def _existing_source(self, content_hash: str) -> str | None:
        """The id already holding this content, in either id format, or None."""
        for key in self.store.list(raw_prefix_for_hash(content_hash)):
            source_id = source_id_from_key(key)
            if source_id is not None and source_id.startswith(content_hash):
                return source_id
        return None

    @staticmethod
    def _content_hash(*, url: str | None, file: bytes | None, text: str | None) -> str:
        """Validate the inputs and content-address the source before anything is fetched."""
        supplied = [
            name for name, value in (("url", url), ("file", file), ("text", text))
            if value is not None
        ]
        if len(supplied) != 1:
            raise ValueError(
                f"provide exactly one of url, file or text (got {', '.join(supplied) or 'none'})"
            )
        if text is not None:
            if not text.strip():
                raise ValueError("text is empty")
            return content_hash_for_bytes(text.encode("utf-8"))
        if file is not None:
            if not file:
                raise ValueError("file is empty")
            return content_hash_for_bytes(file)
        assert url is not None
        if not url.strip():
            raise ValueError("url is empty")
        return content_hash_for_url(url)

    def _fetch(self, url: str, modality: str) -> tuple[bytes, str]:
        """Fetch remote content at capture time so extraction stays reproducible."""
        if modality == "youtube":
            from llmwiki.extractors.youtube import fetch_transcript

            return (
                fetch_transcript(
                    url,
                    proxy_url=self.settings.youtube_proxy_url or None,
                    cookies_path=self.settings.youtube_cookies_path or None,
                    whisper_model=self.settings.youtube_whisper_model or None,
                ),
                "application/json",
            )
        from llmwiki.extractors.web import fetch

        return fetch(url)

    # -- processing ---------------------------------------------------------

    def process(self, source_id: str) -> SourceStatus:
        """Extract, embed and compile one captured source."""
        started = time.monotonic()
        status = SourceStatus(source_id=source_id, state="extracting")
        self.set_status(status)
        logger.info("process: source_id=%s state=extracting", source_id)
        try:
            meta = self.load_meta(source_id)
            doc = self.extract(meta)

            # Phase 2 (plan §21.2 A4): decide the domain once, before anything
            # that depends on it (the chunk index, the manifest) is written.
            assignment = self.route(meta, doc)
            status.domain = assignment.domain
            status.suggested_domain = assignment.suggested_domain or None

            # One writer per domain manifest at a time (Phase 2, plan §21.2 C3);
            # extraction and routing above ran unlocked, so other sources'
            # cheap stages overlap with this domain's compile.
            with domain_lock(assignment.domain):
                status.state = "embedding"
                self.set_status(status)
                logger.info("process: source_id=%s state=embedding domain=%s",
                            source_id, assignment.domain)
                status.chunk_count = self._embed(doc, assignment.domain)

                status.state = "compiling"
                self.set_status(status)
                logger.info("process: source_id=%s state=compiling", source_id)
                result = Compiler(
                    self.store, self.vectors, self.embedder, self.llm, self.settings,
                    lexical=self.lexical,
                ).compile_source(doc, domain=assignment.domain,
                                 prior_costs=self._vision_costs)
            status.pages_touched = result.pages_touched
            status.vision_calls = int(doc.extra.get("vision_calls", 0) or 0)
            status.state = "done"
            if result.aborted:
                status.state = "failed"
                status.error = result.reason
        except (ExtractionError, ObjectNotFound) as exc:
            status.state = "failed"
            status.error = str(exc)
            logger.warning("process: source_id=%s failed: %s", source_id, exc)
        except Exception as exc:  # pragma: no cover - unexpected, still must not crash the worker
            status.state = "failed"
            status.error = f"{type(exc).__name__}: {exc}"
            logger.error("process: source_id=%s unexpected failure: %s", source_id, exc,
                         exc_info=True)
        status.elapsed_s = time.monotonic() - started
        logger.info("process: source_id=%s state=%s elapsed=%.2fs",
                    source_id, status.state, status.elapsed_s)
        self.set_status(status)
        clear_pending(self.store, source_id)
        return status

    def ingest_now(self, **kwargs: object) -> SourceStatus:
        """Capture and process synchronously. Used by the CLI and the smoke script."""
        ref = self.capture(**kwargs)  # type: ignore[arg-type]
        if ref.duplicate:
            return self.get_status(ref.source_id)
        return self.process(ref.source_id)

    def extract(self, meta: SourceMeta) -> ExtractedDoc:
        """Extract, then describe any pending vision pages, then store ``extracted.md``.

        Only text leaves this method (Phase 2, plan §21.2 D2): chunking, the
        lexical index and the compiler never see an image. The description
        step's usage is ledgered as ``kind="ingest"`` and kept on
        ``self._vision_costs`` so the compile's budget check counts it.
        """
        extension = ext_for(meta.mime, meta.filename, meta.url)
        data = self.store.get(raw_original(meta.source_id, extension))
        cfg = self.settings
        extractor = get_extractor(
            meta.modality, vision_mode=cfg.vision_mode,
            vision_max_pages=cfg.vision_max_pages_per_source,
            vision_min_chars=cfg.vision_min_chars_per_page,
            vision_image_area=cfg.vision_image_area_ratio,
        )
        doc = extractor.extract(meta, data)
        self._vision_costs = []
        if doc.extra.get("vision_pages"):
            from llmwiki.pipeline.describe import describe_pending_pages

            doc, usage = describe_pending_pages(doc, self.llm, cfg, self.store)
            self._vision_costs = list(usage)
            if usage:
                CostLedger(self.store, writer=cfg.cost_writer).append(
                    usage, kind="ingest", source_id=meta.source_id,
                )
        self.store.put(raw_extracted(meta.source_id), doc.text.encode("utf-8"), "text/markdown")
        return doc

    # -- routing (Phase 2, plan §21.2 A4) --------------------------------------------

    def route(self, meta: SourceMeta, doc: ExtractedDoc) -> DomainAssignment:
        """Decide, and persist, which domain a source is filed under.

        Cheapest first: an explicit ``domain=`` at capture wins and costs
        nothing; so does a registry with nothing but ``general`` in it, and
        ``DOMAIN_ROUTING=off``. Otherwise one ``route_domain`` call
        (``wiki/router.py``), whose usage is ledgered as ``kind="ingest"``.
        The decision is written to ``raw/{id}/routing.json`` so recompiles
        never route again.
        """
        if meta.domain is not None:
            assignment = DomainAssignment(domain=meta.domain, confidence=1.0, explicit=True,
                                          reason="named at capture")
        else:
            registry = load_registry(self.store)
            if registry.is_general_only:
                assignment = DomainAssignment(domain=GENERAL, reason="no domains registered")
            elif self.settings.domain_routing == "off":
                assignment = DomainAssignment(domain=GENERAL, reason="DOMAIN_ROUTING=off")
            else:
                from llmwiki.wiki.router import DomainRouter

                router = DomainRouter(
                    self.llm, registry, min_confidence=self.settings.domain_route_min_confidence,
                )
                assignment, usage = router.route_source(doc)
                if usage:
                    CostLedger(self.store, writer=self.settings.cost_writer).append(
                        usage, kind="ingest", domain=assignment.domain,
                        source_id=meta.source_id,
                    )
        self.store.put(
            raw_routing(meta.source_id),
            assignment.model_dump_json(indent=2).encode("utf-8"),
            "application/json",
        )
        return assignment

    def load_routing(self, source_id: str) -> DomainAssignment:
        """The persisted routing decision; a source routed before Phase 2 is general."""
        try:
            raw = self.store.get(raw_routing(source_id))
        except ObjectNotFound:
            return DomainAssignment(domain=GENERAL, reason="pre-Phase-2 source")
        return DomainAssignment.model_validate_json(raw)

    def _embed(self, doc: ExtractedDoc, domain: str = GENERAL) -> int:
        chunks = chunk_document(
            doc,
            size=self.settings.chunk_size_chars,
            overlap=self.settings.chunk_overlap_chars,
        )
        if not chunks:
            return 0
        vectors = self.embedder.embed([chunk.text for chunk in chunks])
        metadata = []
        for chunk in chunks:
            row = json.loads(chunk.metadata.model_dump_json())
            # The chunk text rides along so a citation needs no second fetch.
            row["text"] = chunk.text[:1000]
            metadata.append(row)
        index = domain_index_name(self.settings.vectorize_chunks_index, domain)
        self.vectors.upsert(index, [chunk.id for chunk in chunks], vectors, metadata)
        if self.lexical is not None:
            # The full chunk text is searchable; the vector row keeps its 1000
            # chars for citations.
            self.lexical.upsert(index, [chunk.id for chunk in chunks],
                                [chunk.text for chunk in chunks], metadata)
        return len(chunks)

    # -- status -------------------------------------------------------------

    def set_status(self, status: SourceStatus) -> None:
        self.store.put(
            status_key(status.source_id),
            status.model_dump_json(indent=2).encode("utf-8"),
            "application/json",
        )

    def get_status(self, source_id: str) -> SourceStatus:
        try:
            raw = self.store.get(status_key(source_id))
        except ObjectNotFound:
            state: SourceState = "done" if self.store.exists(raw_meta(source_id)) else "failed"
            return SourceStatus(
                source_id=source_id,
                state=state,
                error=None if state == "done" else "unknown source",
            )
        return SourceStatus(**json.loads(raw.decode("utf-8")))

    def load_meta(self, source_id: str) -> SourceMeta:
        return SourceMeta(**json.loads(self.store.get(raw_meta(source_id)).decode("utf-8")))


def _slug_basis(title: str, filename: str | None, url: str | None) -> str:
    """What the readable half of the id is made from, best available first.

    A PDF has no title at capture (extraction is the expensive half and runs
    later), so its filename stem is next; a URL-only PDF falls through to the
    URL's last path segment, e.g. ``2301-12345`` for an arXiv link.
    """
    if title.strip():
        return title
    if filename:
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem.strip():
            return stem
    if url:
        tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        return tail or urlsplit(url).netloc
    return ""


def _title_from_source(modality: str, url: str | None, data: bytes) -> str:
    """Fill ``meta.title`` at capture so ``raw/`` never needs a later rewrite."""
    if modality == "text":
        from llmwiki.extractors.text import first_line

        return first_line(data.decode("utf-8", errors="replace"))
    if modality == "web":
        from llmwiki.extractors.web import title_from_html

        return title_from_html(data.decode("utf-8", errors="replace"))
    if modality == "youtube" and url:
        from llmwiki.extractors.youtube import fetch_video_title

        return fetch_video_title(url)
    return ""
