"""Web page extraction: trafilatura over the captured HTML.

The HTML is fetched at capture time and stored under ``raw/`` first, so
extraction is a pure function of stored bytes and can be re-run offline when the
extractor improves.
"""

from __future__ import annotations

from llmwiki.extractors.base import ExtractionError
from llmwiki.extractors.text import normalize
from llmwiki.models.source import ExtractedDoc, SourceMeta

FETCH_TIMEOUT_S = 30.0
USER_AGENT = "llmwiki/0.9 (+research knowledge base)"


def fetch(url: str) -> tuple[bytes, str]:
    """Fetch a URL at capture time. Returns ``(body, content_type)``."""
    import httpx

    response = httpx.get(
        url,
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "text/html").split(";")[0].strip()
    return response.content, content_type


class WebExtractor:
    """HTML to readable markdown, discarding navigation and boilerplate."""

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        import trafilatura

        html = data.decode("utf-8", errors="replace")
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            output_format="markdown",
        )
        if not text or not text.strip():
            raise ExtractionError(
                f"no readable content extracted from {meta.url or meta.source_id}"
            )

        title = meta.title
        if not title:
            metadata = trafilatura.extract_metadata(html)
            title = (getattr(metadata, "title", None) or "").strip() if metadata else ""
        return ExtractedDoc(
            source_id=meta.source_id,
            title=title or meta.url or "Untitled Page",
            text=normalize(text),
            modality="web",
            url=meta.url,
        )
