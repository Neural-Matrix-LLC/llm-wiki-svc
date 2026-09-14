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
from llmwiki.llm.base import LLMClient
from llmwiki.models.source import (
    ExtractedDoc,
    Modality,
    SourceMeta,
    SourceRef,
    SourceState,
    SourceStatus,
)
from llmwiki.pipeline.chunker import chunk_document
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    content_hash_for_bytes,
    content_hash_for_url,
    ext_for,
    raw_extracted,
    raw_meta,
    raw_original,
    raw_prefix_for_hash,
    source_id_for,
    source_id_from_key,
    status_key,
)
from llmwiki.vector.base import VectorStore
from llmwiki.wiki.compiler import Compiler

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
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.settings = settings

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
        """
        content_hash = self._content_hash(url=url, file=file, text=text)

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
        )
        extension = ext_for(resolved_mime, filename, url)
        self.store.put(raw_original(source_id, extension), data, resolved_mime)
        self.store.put(
            raw_meta(source_id), meta.model_dump_json(indent=2).encode(), "application/json"
        )
        self.set_status(SourceStatus(source_id=source_id, state="queued"))
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

            return fetch_transcript(url), "application/json"
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

            status.state = "embedding"
            self.set_status(status)
            logger.info("process: source_id=%s state=embedding", source_id)
            status.chunk_count = self._embed(doc)

            status.state = "compiling"
            self.set_status(status)
            logger.info("process: source_id=%s state=compiling", source_id)
            result = Compiler(
                self.store, self.vectors, self.embedder, self.llm, self.settings
            ).compile_source(doc)
            status.pages_touched = result.pages_touched
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
        return status

    def ingest_now(self, **kwargs: object) -> SourceStatus:
        """Capture and process synchronously. Used by the CLI and the smoke script."""
        ref = self.capture(**kwargs)  # type: ignore[arg-type]
        if ref.duplicate:
            return self.get_status(ref.source_id)
        return self.process(ref.source_id)

    def extract(self, meta: SourceMeta) -> ExtractedDoc:
        extension = ext_for(meta.mime, meta.filename, meta.url)
        data = self.store.get(raw_original(meta.source_id, extension))
        doc = get_extractor(meta.modality).extract(meta, data)
        self.store.put(raw_extracted(meta.source_id), doc.text.encode("utf-8"), "text/markdown")
        return doc

    def _embed(self, doc: ExtractedDoc) -> int:
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
        self.vectors.upsert(
            self.settings.vectorize_chunks_index,
            [chunk.id for chunk in chunks],
            vectors,
            metadata,
        )
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
