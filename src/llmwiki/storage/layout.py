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
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from llmwiki.models.source import GENERAL_DOMAIN

RAW_PREFIX = "raw/"
WIKI_PREFIX = "wiki/"
GISTS_KEY = "wiki/_meta/gists.json"
#: Phase 2 (plan §21.2 A1-A3). ``general`` *is* the Phase 0/1 layout: its keys
#: are the constants above, unchanged. Every other domain nests under
#: ``wiki/domains/{d}/`` with the same shape inside, and its vector/lexical
#: indexes are ``{base}-{d}``. The registry of non-general domains is one JSON
#: object at DOMAINS_KEY; ``general`` is implied and never listed there.
DOMAINS_PREFIX = "wiki/domains/"
DOMAINS_KEY = "wiki/_meta/domains.json"
GENERAL = GENERAL_DOMAIN
#: A domain name has to fit ``{index-base}-{d}`` inside Vectorize's 64-character
#: index-name cap and must never collide with a folder the layout already uses.
DOMAIN_MAX = 32
RESERVED_DOMAINS = frozenset({
    GENERAL, "domains", "_meta", "index", "concepts", "entities", "sources", "overview",
    "raw", "status", "wiki",
})
#: The pre-Phase-2 single-file ledger. Still read (read-through) until
#: ``llmwiki usage --migrate`` moves its lines into the partitioned keys below;
#: nothing writes it any more (plan §21.2 C1).
COST_KEY = "wiki/_meta/cost.jsonl"
#: Phase 2 ledger: ``wiki/_meta/cost/{YYYY-MM}/{DD}-{writer}.jsonl``. One key per
#: day per *writing process*, so the API, the CLI and the cron jobs never
#: read-modify-write the same object, and a month's spend is one prefix list.
COST_PREFIX = "wiki/_meta/cost/"
ALERTS_KEY = "wiki/_meta/cost/alerts.json"
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
_WRITER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


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


def check_domain(name: str, *, allow_general: bool = True) -> str:
    """Validate a domain name before it becomes part of a key or an index name.

    ``general`` passes when ``allow_general`` (it is a legitimate scope) and is
    rejected when a caller is registering a *new* domain (it is implied, never
    registered). Every other reserved word is a folder the layout already owns.
    """
    if name == GENERAL:
        if allow_general:
            return name
        raise ValueError(f"{GENERAL!r} is implied and cannot be registered")
    if not _DOMAIN_RE.match(name) or len(name) > DOMAIN_MAX:
        raise ValueError(
            f"not a valid domain name: {name!r} (lowercase letters, digits and hyphens, "
            f"at most {DOMAIN_MAX} characters, starting with a letter or digit)"
        )
    if name in RESERVED_DOMAINS:
        raise ValueError(f"{name!r} is a reserved name and cannot be a domain")
    return name


def domain_prefix(domain: str = GENERAL) -> str:
    """``""`` for general (its pages sit directly under ``wiki/``), else ``wiki/domains/{d}/``."""
    if check_domain(domain) == GENERAL:
        return ""
    return f"{DOMAINS_PREFIX}{domain}/"


def gists_key(domain: str = GENERAL) -> str:
    """The domain's gist manifest: ``wiki/_meta/gists.json`` for general."""
    if check_domain(domain) == GENERAL:
        return GISTS_KEY
    return f"{domain_prefix(domain)}_meta/gists.json"


def index_key(domain: str = GENERAL) -> str:
    """The domain's index page: ``wiki/index.md`` for general."""
    return INDEX_KEY if check_domain(domain) == GENERAL else f"{domain_prefix(domain)}index.md"


def overview_key(domain: str = GENERAL) -> str:
    """The domain's synthesis page (plan §21.2 A8): ``wiki/overview.md`` for general."""
    return f"{WIKI_PREFIX}overview.md" if check_domain(domain) == GENERAL else (
        f"{domain_prefix(domain)}overview.md"
    )


def domain_index_name(base: str, domain: str = GENERAL) -> str:
    """The vector/lexical index a domain uses: ``base`` for general, ``{base}-{d}`` otherwise."""
    return base if check_domain(domain) == GENERAL else f"{base}-{domain}"


def domain_of_key(key: str) -> str | None:
    """Which domain a ``wiki/`` key belongs to, or None for anything outside ``wiki/``.

    ``wiki/_meta/...`` and ``wiki/index.md`` belong to general, like every key
    not under ``wiki/domains/``.
    """
    if not key.startswith(WIKI_PREFIX):
        return None
    if key.startswith(DOMAINS_PREFIX):
        name, _, _ = key[len(DOMAINS_PREFIX):].partition("/")
        return name or None
    return GENERAL


def wiki_page(slug: str, page_type: str = "concept", domain: str = GENERAL) -> str:
    """The page's key inside its domain.

    General: ``wiki/concepts/{slug}.md``, ``wiki/entities/{slug}.md``,
    ``wiki/sources/{id}.md`` - byte-for-byte the Phase 0/1 keys. Any other
    domain: the same shape under ``wiki/domains/{d}/``.
    """
    if page_type == "index":
        return index_key(domain)
    if page_type == "overview":
        return overview_key(domain)
    folder = {"concept": "concepts", "entity": "entities", "source": "sources"}[page_type]
    safe = slugify(slug) if page_type != "source" else _check_id(slug)
    if check_domain(domain) == GENERAL:
        return f"{WIKI_PREFIX}{folder}/{safe}.md"
    return f"{domain_prefix(domain)}{folder}/{safe}.md"


def wiki_source_note(source_id: str, domain: str = GENERAL) -> str:
    """``wiki/sources/{id}.md`` (or the domain's ``sources/``) - the note linking back to raw/."""
    return wiki_page(source_id, "source", domain)


def raw_routing(source_id: str) -> str:
    """``raw/{id}/routing.json`` - the domain decision; derived and rewritable like extracted.md."""
    return f"{RAW_PREFIX}{_check_id(source_id)}/routing.json"


def raw_vision(source_id: str) -> str:
    """``raw/{id}/vision.json`` - cached image descriptions; derived and rewritable."""
    return f"{RAW_PREFIX}{_check_id(source_id)}/vision.json"


def pending_key(source_id: str) -> str:
    """``status/_pending/{id}`` - marks work the ingest worker still owes (plan §21.2 C3)."""
    return f"{STATUS_PREFIX}_pending/{_check_id(source_id)}"


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


def check_cost_writer(writer: str) -> str:
    """Validate a ledger writer name (``api``, ``cli``, ``backfill``...) before it is a key."""
    if not _WRITER_RE.match(writer):
        raise ValueError(f"not a valid cost writer name: {writer!r}")
    return writer


def cost_month_prefix(month: str) -> str:
    """``wiki/_meta/cost/{YYYY-MM}/`` - everything one month's ledger holds."""
    if not _MONTH_RE.match(month):
        raise ValueError(f"not a YYYY-MM month: {month!r}")
    return f"{COST_PREFIX}{month}/"


def cost_key(day: date, writer: str) -> str:
    """``wiki/_meta/cost/{YYYY-MM}/{DD}-{writer}.jsonl`` - one writer's lines for one day."""
    return f"{cost_month_prefix(day.strftime('%Y-%m'))}{day:%d}-{check_cost_writer(writer)}.jsonl"


def cost_key_day(key: str) -> date | None:
    """The day a ledger key belongs to, or None for anything else under the prefix."""
    if not key.startswith(COST_PREFIX) or not key.endswith(".jsonl"):
        return None
    rest = key[len(COST_PREFIX):]
    month, _, name = rest.partition("/")
    if not _MONTH_RE.match(month) or len(name) < 3 or not name[:2].isdigit():
        return None
    try:
        return date.fromisoformat(f"{month}-{name[:2]}")
    except ValueError:
        return None
