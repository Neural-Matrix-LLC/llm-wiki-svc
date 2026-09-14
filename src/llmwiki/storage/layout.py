"""The only module that builds object keys (plan 5.1).

Two invariants live here:

* ``raw/`` is append-only - ``original.*`` and ``meta.json`` are written once at
  capture and never mutated.
* No caller-supplied string reaches a key unsanitized.  Source ids are a hex
  digest plus a normalized slug, so a hostile filename or URL cannot escape
  its prefix.

A source id is ``{content_hash}-{slug}``: 16 hex chars of SHA-256 over the
bytes (files, text) or the canonical URL, then a slug of the title. The hash
is what dedups - capture lists ``raw/{hash}`` before it fetches or spends a
token - and the slug is what makes ``raw/``, ``status/`` and ``wiki/sources/``
readable in an object browser or Obsidian. Hash first, so the folder is
prefix-listable by content alone; slug baked into the id, so every id-only
caller already holds the full key and nothing needs a lookup. Ids minted
before 2026-09-13 are the bare 16-hex hash and stay valid.
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

# The slug share of a source id. Vectorize caps a vector id at 64 bytes and
# chunk ids are ``{source_id}:{n}`` (models/chunk.py), so 16 + 1 + 40 leaves
# room for the separator and a five-digit chunk index.
SOURCE_SLUG_MAX = 40
HASH_LEN = 16

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_ID_RE = re.compile(r"^[0-9a-f]{16}(-[a-z0-9][a-z0-9-]{0,39})?$")
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


def content_hash_for_bytes(data: bytes) -> str:
    """Content address a file: identical bytes are the same source."""
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def content_hash_for_url(url: str) -> str:
    """Content address a URL by its canonical form."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:HASH_LEN]


def source_slug(title: str) -> str:
    """The readable half of a source id: a slug short enough for a chunk id."""
    return slugify(title)[:SOURCE_SLUG_MAX].rstrip("-") or "untitled"


def source_id_for(content_hash: str, title: str) -> str:
    """``{hash}-{slug}`` - the one place a source id is minted."""
    if not _HASH_RE.match(content_hash):
        raise ValueError(f"not a valid content hash: {content_hash!r}")
    return f"{content_hash}-{source_slug(title)}"


def content_hash_of(source_id: str) -> str:
    """The dedup key inside a source id, for either id format."""
    return _check_id(source_id)[:HASH_LEN]


def raw_prefix_for_hash(content_hash: str) -> str:
    """``raw/{hash}`` - lists the source folder for a hash whatever its slug (or none)."""
    if not _HASH_RE.match(content_hash):
        raise ValueError(f"not a valid content hash: {content_hash!r}")
    return f"{RAW_PREFIX}{content_hash}"


def source_id_from_key(key: str) -> str | None:
    """The source id a ``raw/{id}/...`` key belongs to, or None for anything else."""
    if not key.startswith(RAW_PREFIX):
        return None
    folder, _, _ = key[len(RAW_PREFIX):].partition("/")
    return folder if _ID_RE.match(folder) else None


def is_source_id(value: str) -> bool:
    """True for either id format; the cheap test before treating a slug as one."""
    return bool(_ID_RE.match(value))


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
