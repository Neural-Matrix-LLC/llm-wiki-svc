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
import time

from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.extractors.base import ExtractionError, detect_modality, get_extractor
from llmwiki.llm.base import LLMClient
from llmwiki.models.source import ExtractedDoc, SourceMeta, SourceRef, SourceState, SourceStatus
from llmwiki.pipeline.chunker import chunk_document
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    ext_for,
    raw_extracted,
    raw_meta,
    raw_original,
    source_id_for_bytes,
    source_id_for_url,
    status_key,
)
from llmwiki.vector.base import VectorStore
from llmwiki.wiki.compiler import Compiler


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
    ) -> SourceRef:
        """Write the immutable raw objects. Returns immediately; nothing is compiled yet."""
        if not url and file is None:
            raise ValueError("provide either url or file")

        if file is not None:
            source_id = source_id_for_bytes(file)
            data = file
            modality = detect_modality(mime, filename, None)
            resolved_mime = mime or "application/octet-stream"
        else:
            assert url is not None
            source_id = source_id_for_url(url)
            modality = detect_modality(mime, filename, url)
            data, resolved_mime = self._fetch(url, modality)

        if self.store.exists(raw_meta(source_id)):
            return SourceRef(source_id=source_id, status="done", duplicate=True)

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
        return SourceRef(source_id=source_id, status="queued")

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
        try:
            meta = self.load_meta(source_id)
            doc = self.extract(meta)

            status.state = "embedding"
            self.set_status(status)
            status.chunk_count = self._embed(doc)

            status.state = "compiling"
            self.set_status(status)
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
        except Exception as exc:  # pragma: no cover - unexpected, still must not crash the worker
            status.state = "failed"
            status.error = f"{type(exc).__name__}: {exc}"
        status.elapsed_s = time.monotonic() - started
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
