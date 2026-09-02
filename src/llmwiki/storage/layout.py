"""The only module that builds object keys (plan 5.1).

Two invariants live here:

* ``raw/`` is append-only - ``original.*`` and ``meta.json`` are written once at
  capture and never mutated.
* No caller-supplied string reaches a key unsanitized.  Source ids are hex
  digests and slugs are normalized, so a hostile filename or URL cannot escape
  its prefix.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

RAW_PREFIX = "raw/"
WIKI_PREFIX = "wiki/"
GISTS_KEY = "wiki/_meta/gists.json"
COST_KEY = "wiki/_meta/cost.jsonl"
STATUS_PREFIX = "status/"
INDEX_KEY = "wiki/index.md"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated, filesystem- and Obsidian-safe."""
    slug = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return slug[:80] or "untitled"


def canonical_url(url: str) -> str:
    """Normalize a URL so the same page captured twice yields the same source_id."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def source_id_for_bytes(data: bytes) -> str:
    """Content address a file: identical bytes are the same source."""
    return hashlib.sha256(data).hexdigest()[:16]


def source_id_for_url(url: str) -> str:
    """Content address a URL by its canonical form."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def _check_id(source_id: str) -> str:
    if not _ID_RE.match(source_id):
        raise ValueError(f"not a valid source_id: {source_id!r}")
    return source_id


def _check_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if not _EXT_RE.match(ext):
        raise ValueError(f"not a valid extension: {ext!r}")
    return ext


def raw_original(source_id: str, ext: str) -> str:
    """``raw/{id}/original.{ext}`` - immutable bytes exactly as captured."""
    return f"{RAW_PREFIX}{_check_id(source_id)}/original.{_check_ext(ext)}"


def raw_meta(source_id: str) -> str:
    """``raw/{id}/meta.json`` - written once, never mutated."""
    return f"{RAW_PREFIX}{_check_id(source_id)}/meta.json"


def raw_extracted(source_id: str) -> str:
    """``raw/{id}/extracted.md`` - the one rewritable object under raw/."""
    return f"{RAW_PREFIX}{_check_id(source_id)}/extracted.md"


def status_key(source_id: str) -> str:
    """Pipeline progress for one source, polled by ``GET /sources/{id}``."""
    return f"{STATUS_PREFIX}{_check_id(source_id)}.json"


def wiki_page(slug: str, page_type: str = "concept") -> str:
    """``wiki/concepts/{slug}.md``, ``wiki/entities/{slug}.md`` or ``wiki/sources/{id}.md``."""
    if page_type == "index":
        return INDEX_KEY
    folder = {"concept": "concepts", "entity": "entities", "source": "sources"}[page_type]
    safe = slugify(slug) if page_type != "source" else _check_id(slug)
    return f"{WIKI_PREFIX}{folder}/{safe}.md"


def wiki_source_note(source_id: str) -> str:
    """``wiki/sources/{id}.md`` - the per-source note linking back to raw/."""
    return wiki_page(source_id, "source")


def ext_for(mime: str, filename: str | None, url: str | None) -> str:
    """Best-effort extension for the immutable original."""
    if filename and "." in filename:
        candidate = filename.rsplit(".", 1)[1].lower()
        if _EXT_RE.match(candidate):
            return candidate
    by_mime = {
        "application/pdf": "pdf",
        "text/html": "html",
        "text/plain": "txt",
        "text/markdown": "md",
        "image/png": "png",
        "image/jpeg": "jpg",
    }
    if mime in by_mime:
        return by_mime[mime]
    if url:
        tail = urlsplit(url).path.rsplit(".", 1)
        if len(tail) == 2 and _EXT_RE.match(tail[1].lower()):
            return tail[1].lower()
    return "bin"
