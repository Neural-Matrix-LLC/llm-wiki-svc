#!/usr/bin/env python3
"""Capture a YouTube URL and a PDF through the REST API, then prove they landed.

``smoke_flow.py`` exercises the Python core in-process. This script goes in
the front door instead - ``POST /ingest`` for the YouTube URL, ``POST /upload``
for the PDF - and then reads every place a source is supposed to end up:

  raw/     ``meta.json`` (modality, sha256 of the bytes we sent), the immutable
           ``original.*`` (byte-for-byte equal to the PDF we uploaded) and the
           ``extracted.md`` the extractor produced.
  wiki/    ``wiki/sources/{id}.md`` (the per-source note), at least one concept
           page whose front matter lists the source, the gist manifest and
           ``wiki/index.md``; the same pages fetched back through
           ``GET /concepts`` and ``GET /page/{slug}``.
  vectors  every chunk of the source in the chunks index (count equals the
           pipeline's ``chunk_count``, ids are ``{source_id}:{n}``, each stored
           text is exactly the slice of ``extracted.md`` its offsets claim),
           plus ``GET /search`` surfacing the source for its own opening text.

The read-back is done twice on purpose: once through the API the way a client
sees it, and once against the backends from ``.env`` directly (object store,
vector store, embedder - the same factories the server uses), so a bug that
hides behind the API (a wrong key, a stale cache) still shows.

    scripts/verify_capture.py --youtube https://www.youtube.com/watch?v=...
    scripts/verify_capture.py --pdf ~/papers/attention.pdf
    scripts/verify_capture.py --youtube ... --pdf ...      # both, in one run
    scripts/verify_capture.py --pdf ... --base-url http://localhost:8010
    scripts/verify_capture.py --pdf ... --rest-only         # server on other backends

Needs a running service (``uvicorn llmwiki.api.app:app``, or the compose
``api``/``dev`` profile on :8010/:8011) and the same ``.env`` it runs with, so
the direct read-back hits the same R2 bucket / Vectorize index / ``.data``
folder. With ``VECTOR_BACKEND=memory`` the server's vectors live in its own
process and only the ``GET /search`` half of the vector check can run.

Exit code is non-zero at the first failed check, and the check is named.
Sources are kept afterwards (that is the point); ``--cleanup`` removes them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from llmwiki.embedding.base import Embedder
    from llmwiki.models.chunk import SearchHit
    from llmwiki.storage.base import ObjectStore
    from llmwiki.vector.base import VectorStore

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "sample.pdf"
POLL_S = 2
# Vectorize is eventually consistent: a query right after the upsert can come
# back short. Re-ask a few times before calling the count wrong.
VECTOR_RETRIES = 6
VECTOR_RETRY_S = 5


class CheckFailed(AssertionError):
    """One named check did not hold."""


@dataclass
class Captured:
    label: str
    source_id: str
    modality: str
    duplicate: bool
    chunk_count: int = 0
    pages_touched: int = 0
    sent_bytes: bytes | None = None     # the PDF, for the byte-equality check
    url: str | None = None              # the YouTube URL, for the meta check
    extracted: str = ""
    pages: list[str] = field(default_factory=list)


def check(condition: object, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def step(label: str) -> None:
    print(f"\n== {label}", flush=True)


def ok(message: str) -> None:
    print(f"   ok  {message}", flush=True)


def note(message: str) -> None:
    print(f"   --  {message}", flush=True)


# --- capture through the REST API -------------------------------------------


class Api:
    def __init__(self, base_url: str, token: str, timeout_s: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout_s)
        self.auth = {"Authorization": f"Bearer {token}"}

    def health(self) -> dict:
        response = self.client.get("/healthz")
        response.raise_for_status()
        return response.json()

    def ingest_url(self, url: str) -> dict:
        response = self.client.post("/ingest", json={"url": url}, headers=self.auth)
        _raise_with_detail(response)
        return response.json()

    def upload(self, path: Path, data: bytes) -> dict:
        response = self.client.post(
            "/upload",
            files={"file": (path.name, data, "application/pdf")},
            headers=self.auth,
        )
        _raise_with_detail(response)
        return response.json()

    def status(self, source_id: str) -> dict:
        response = self.client.get(f"/sources/{source_id}")
        response.raise_for_status()
        return response.json()

    def concepts(self) -> list[dict]:
        response = self.client.get("/concepts")
        response.raise_for_status()
        return response.json()

    def page(self, slug: str) -> str:
        response = self.client.get(f"/page/{slug}")
        response.raise_for_status()
        return response.text

    def search(self, query: str, k: int = 10) -> list[dict]:
        response = self.client.get("/search", params={"q": query, "k": k})
        response.raise_for_status()
        return response.json()


def _raise_with_detail(response: httpx.Response) -> None:
    """Surface the API's ``detail`` (a 422 names the cause) instead of a bare status."""
    if response.is_success:
        return
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text
    raise CheckFailed(f"{response.request.method} {response.request.url.path} -> "
                      f"{response.status_code}: {detail}")


def wait_until_done(api: Api, captured: Captured, timeout_s: float) -> None:
    started = time.monotonic()
    status = api.status(captured.source_id)
    last = None
    while status["state"] not in ("done", "failed"):
        if status["state"] != last:
            print(f"   ... {status['state']}", flush=True)
            last = status["state"]
        check(time.monotonic() - started < timeout_s,
              f"{captured.label}: pipeline still '{status['state']}' after {timeout_s:.0f}s")
        time.sleep(POLL_S)
        status = api.status(captured.source_id)
    check(status["state"] == "done",
          f"{captured.label}: pipeline failed: {status.get('error')}")
    captured.chunk_count = status["chunk_count"]
    captured.pages_touched = status["pages_touched"]
    ok(f"state=done chunks={status['chunk_count']} pages_touched={status['pages_touched']} "
       f"elapsed={status['elapsed_s']:.1f}s")


def capture_youtube(api: Api, url: str, timeout_s: float) -> Captured:
    step(f"POST /ingest  {url}")
    ref = api.ingest_url(url)
    captured = Captured("youtube", ref["source_id"], "youtube", ref["duplicate"], url=url)
    print(f"   source_id={captured.source_id} duplicate={captured.duplicate}")
    if captured.duplicate:
        note("already captured earlier - verifying the existing objects")
    wait_until_done(api, captured, timeout_s)
    return captured


def capture_pdf(api: Api, path: Path, timeout_s: float) -> Captured:
    step(f"POST /upload  {path}")
    data = path.read_bytes()
    check(data.startswith(b"%PDF"), f"{path} does not look like a PDF")
    ref = api.upload(path, data)
    captured = Captured("pdf", ref["source_id"], "pdf", ref["duplicate"], sent_bytes=data)
    print(f"   source_id={captured.source_id} duplicate={captured.duplicate} "
          f"bytes={len(data)}")
    if captured.duplicate:
        note("already captured earlier - verifying the existing objects")
    wait_until_done(api, captured, timeout_s)
    return captured


# --- read back: raw/ --------------------------------------------------------


def verify_raw(store: ObjectStore, captured: Captured) -> None:
    from llmwiki.models.source import SourceMeta
    from llmwiki.storage.layout import raw_extracted, raw_meta

    step(f"raw/{captured.source_id}/  ({captured.label})")
    keys = sorted(store.list(f"raw/{captured.source_id}/"))
    check(keys, "no objects under raw/ - the direct read-back is looking at a different "
                "store than the server (check STORAGE_BACKEND / LOCAL_STORAGE_PATH / R2_*)")
    for key in keys:
        print(f"   {key}")

    check(raw_meta(captured.source_id) in keys, "meta.json missing")
    meta = SourceMeta(**json.loads(store.get(raw_meta(captured.source_id))))
    check(meta.source_id == captured.source_id, "meta.json source_id disagrees with the key")
    check(meta.modality == captured.modality,
          f"meta.json modality={meta.modality!r}, expected {captured.modality!r}")
    if captured.url:
        check(meta.url == captured.url, f"meta.json url={meta.url!r}, sent {captured.url!r}")
    ok(f"meta.json: modality={meta.modality} title={meta.title!r} "
       f"mime={meta.mime} bytes={meta.byte_size}")

    originals = [k for k in keys if k.rsplit("/", 1)[1].startswith("original.")]
    check(len(originals) == 1, f"expected one original.*, found {originals}")
    original = store.get(originals[0])
    check(hashlib.sha256(original).hexdigest() == meta.sha256,
          "original.* bytes do not hash to meta.json's sha256")
    check(len(original) == meta.byte_size, "original.* size disagrees with meta.json")
    if captured.sent_bytes is not None:
        check(original == captured.sent_bytes,
              "original.pdf is not byte-for-byte what we uploaded")
        ok(f"{originals[0].rsplit('/', 1)[1]}: identical to the uploaded file "
           f"({len(original)} bytes, sha256 matches meta.json)")
    else:
        segments = json.loads(original)
        check(isinstance(segments, list) and segments,
              "original.* for a YouTube source should be a non-empty segment list")
        check({"text", "start", "duration"} <= set(segments[0]),
              f"segment shape is off: {segments[0]}")
        ok(f"{originals[0].rsplit('/', 1)[1]}: {len(segments)} transcript segments, "
           f"sha256 matches meta.json")

    check(raw_extracted(captured.source_id) in keys, "extracted.md missing")
    captured.extracted = store.get(raw_extracted(captured.source_id)).decode("utf-8")
    check(captured.extracted.strip(), "extracted.md is empty")
    ok(f"extracted.md: {len(captured.extracted)} chars; starts "
       f"{captured.extracted.strip()[:80]!r}")


# --- read back: wiki/ -------------------------------------------------------


def verify_wiki(store: ObjectStore, api: Api, captured: Captured) -> None:
    from llmwiki.storage.layout import GISTS_KEY, INDEX_KEY, wiki_page, wiki_source_note
    from llmwiki.wiki.gists import load_gists
    from llmwiki.wiki.pages import read_page

    step(f"wiki/  ({captured.label})")
    note_key = wiki_source_note(captured.source_id)
    check(store.exists(note_key), f"{note_key} missing - the compiler did not write the "
                                  "per-source note")
    source_note = store.get(note_key).decode("utf-8")
    check(captured.source_id in source_note, "source note does not mention its own source_id")
    ok(f"{note_key}: {len(source_note)} chars")

    check(store.exists(GISTS_KEY), f"{GISTS_KEY} missing")
    gists = load_gists(store)
    citing = sorted(slug for slug, gist in gists.items() if captured.source_id in gist.sources)
    check(captured.source_id in citing,
          f"{GISTS_KEY} has no entry for the source note {captured.source_id}")
    # The manifest lists the source note under its own id; the concept and
    # entity pages are the compiler's actual synthesis work.
    captured.pages = [slug for slug in citing if gists[slug].type in ("concept", "entity")]
    check(captured.pages, f"no concept/entity page in {GISTS_KEY} cites {captured.source_id} "
                          f"- the compiler wrote the source note and nothing else")
    ok(f"{GISTS_KEY}: {len(gists)} gists; source note listed; "
       f"{len(captured.pages)} page(s) cite the source: {captured.pages}")

    for slug in captured.pages:
        page = read_page(store, slug, gists[slug].type)
        check(page is not None, f"{wiki_page(slug, gists[slug].type)} is in the manifest "
                                f"but not in the store")
        assert page is not None
        check(page.front_matter.slug == slug, f"page {slug}: front-matter slug mismatch")
        check(captured.source_id in page.front_matter.sources,
              f"page {slug}: manifest says it cites the source, the page's front matter does not")
        check(page.front_matter.gist, f"page {slug}: empty gist")
        check(page.body.strip(), f"page {slug}: empty body")
        ok(f"{wiki_page(slug, gists[slug].type)} v{page.front_matter.version}: "
           f"{page.front_matter.gist[:70]!r}")

    check(store.exists(INDEX_KEY), f"{INDEX_KEY} missing")
    index = store.get(INDEX_KEY).decode("utf-8")
    missing = [slug for slug in captured.pages if slug not in index]
    check(not missing, f"{INDEX_KEY} does not link {missing}")
    ok(f"{INDEX_KEY} links every citing page")

    # The same pages, as a client sees them.
    listed = {gist["slug"] for gist in api.concepts()}
    missing = [slug for slug in captured.pages if slug not in listed]
    check(not missing, f"GET /concepts does not list {missing}")
    for slug in captured.pages:
        body = api.page(slug)
        check(captured.source_id in body,
              f"GET /page/{slug} does not carry {captured.source_id} in its front matter")
    ok(f"GET /concepts and GET /page/{{slug}} return the same {len(captured.pages)} page(s)")


# --- read back: vectors -----------------------------------------------------


def verify_vectors(vectors: VectorStore | None, embedder: Embedder | None,
                   chunks_index: str, api: Api, captured: Captured) -> None:
    step(f"vectors  ({captured.label})")
    if captured.chunk_count == 0:
        note("pipeline reported chunk_count=0 (duplicate re-run?) - counting what is there")

    if vectors is not None and embedder is not None:
        verify_vectors_direct(vectors, embedder, chunks_index, captured)
    else:
        note("direct vector read-back skipped (see --rest-only / VECTOR_BACKEND=memory)")

    # Through the API: the source's own opening text should find it. Wiki
    # pages rank first by design, so accept a page that cites the source too.
    query = " ".join(captured.extracted.split()[:60])
    rows = api.search(query, k=10)
    check(rows, "GET /search returned nothing")
    by_chunk = [r for r in rows if r.get("source_id") == captured.source_id]
    by_page = [r for r in rows if r.get("slug") in captured.pages]
    top_rows = [(r.get("origin"), r.get("slug") or r.get("source_id")) for r in rows[:5]]
    check(by_chunk or by_page,
          f"GET /search for the source's own text surfaced neither its chunks nor a page "
          f"citing it; top hits: {top_rows}")
    top = (by_chunk or by_page)[0]
    ok(f"GET /search: {len(by_chunk)} chunk hit(s), {len(by_page)} page hit(s); best "
       f"{top['origin']} {top.get('slug') or top.get('source_id')} score={top['score']:.3f}")


def verify_vectors_direct(vectors: VectorStore, embedder: Embedder, chunks_index: str,
                          captured: Captured) -> None:
    """Every chunk of the source, straight from the vector store, checked against extracted.md."""
    probe = embedder.embed([captured.extracted[:2000]])[0]
    hits: list[SearchHit] = []
    for attempt in range(1, VECTOR_RETRIES + 1):
        hits = vectors.query(chunks_index, probe, k=100, where={"source_id": captured.source_id})
        if captured.chunk_count == 0 or len(hits) >= captured.chunk_count:
            break
        note(f"{len(hits)}/{captured.chunk_count} vectors visible, retry {attempt}...")
        time.sleep(VECTOR_RETRY_S)
    check(hits, f"no vectors in {chunks_index!r} with source_id={captured.source_id}")
    if captured.chunk_count:
        check(len(hits) == captured.chunk_count,
              f"{len(hits)} vectors stored, pipeline reported {captured.chunk_count}")
    ids = sorted(hit.id for hit in hits)
    expected = sorted(f"{captured.source_id}:{n}" for n in range(len(hits)))
    check(ids == expected, f"chunk ids are not {captured.source_id}:0..{len(hits) - 1}: {ids}")
    for hit in hits:
        check(hit.source_id == captured.source_id, f"{hit.id}: wrong source_id in metadata")
        check(hit.text.strip(), f"{hit.id}: no chunk text rides along with the vector")
        start, stop = hit.metadata.get("char_start"), hit.metadata.get("char_end")
        check(isinstance(start, int) and isinstance(stop, int),
              f"{hit.id}: char offsets missing from metadata")
        check(captured.extracted[start:stop].startswith(hit.text),
              f"{hit.id}: stored text is not extracted.md[{start}:{stop}]")
    ok(f"{chunks_index}: {len(hits)} chunks {captured.source_id}:0..{len(hits) - 1}, "
       f"each one's text is the slice of extracted.md its offsets name")


# --- main -------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--youtube", metavar="URL", help="YouTube URL to POST to /ingest")
    ap.add_argument("--pdf", metavar="PATH", type=Path,
                    help=f"PDF to POST to /upload (default: {FIXTURE.relative_to(REPO)} "
                         "when --youtube is not given)")
    ap.add_argument("--base-url", default=None,
                    help="service root (default: http://localhost:$API_PORT from .env; the "
                         "compose api/dev profiles listen on :8010/:8011)")
    ap.add_argument("--token", default=None, help="bearer token (default: INGEST_API_TOKEN)")
    ap.add_argument("--timeout", type=float, default=600,
                    help="seconds to wait for each source's pipeline (default 600)")
    ap.add_argument("--rest-only", action="store_true",
                    help="skip the direct object-store / vector-store read-back")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the captured sources (raw/, status/, vectors) afterwards")
    args = ap.parse_args()

    if not args.youtube and not args.pdf:
        args.pdf = FIXTURE
    if args.pdf is not None and not args.pdf.is_file():
        print(f"no such file: {args.pdf}", file=sys.stderr)
        return 2

    from llmwiki import factory
    from llmwiki.config import load_settings

    settings = load_settings()
    base_url = args.base_url or f"http://localhost:{settings.api_port}"
    token = args.token or settings.ingest_api_token.get_secret_value()
    api = Api(base_url, token, timeout_s=max(60.0, args.timeout))

    step(f"GET {base_url}/healthz")
    try:
        health = api.health()
    except httpx.HTTPError as exc:
        print(f"   service not reachable at {base_url}: {exc}", file=sys.stderr)
        return 2
    backends = health["backends"]
    print(f"   llmwiki {health['version']} | storage={backends['storage']} "
          f"vector={backends['vector']} embed={backends['embedding']} llm={backends['llm']}")
    for name in ("storage", "vector", "embedding"):
        local = getattr(settings, f"{name}_backend")
        if backends[name] != local and not args.rest_only:
            note(f"server {name}={backends[name]} but this .env says {local}: "
                 f"direct read-back would hit a different backend - use --rest-only "
                 f"or point .env at the server's backends")
            return 2

    direct_vectors = not args.rest_only and backends["vector"] != "memory"
    if not args.rest_only and backends["vector"] == "memory":
        note("server VECTOR_BACKEND=memory: its vectors are process-local, "
             "the vector check will go through GET /search only")

    vectors: VectorStore | None = None
    embedder: Embedder | None = None
    if direct_vectors:
        vectors = factory.vector_store(settings)
        embedder = factory.embedder(settings)

    captured: list[Captured] = []
    if args.youtube:
        captured.append(capture_youtube(api, args.youtube, args.timeout))
    if args.pdf:
        captured.append(capture_pdf(api, args.pdf, args.timeout))

    for item in captured:
        if args.rest_only:
            # Without the store there is no extracted.md to anchor the search on;
            # fall back to the pages the API says exist.
            step(f"REST-only read-back  ({item.label})")
            item.pages = [g["slug"] for g in api.concepts()
                          if item.source_id in api.page(g["slug"])]
            check(item.pages, f"no page from GET /concepts cites {item.source_id}")
            ok(f"{len(item.pages)} page(s) cite the source: {item.pages}")
            hits = api.search(item.pages[0].replace("-", " "), k=10)
            check(any(h.get("source_id") == item.source_id or h.get("slug") in item.pages
                      for h in hits), "GET /search does not surface the source or its pages")
            ok("GET /search surfaces it")
            continue
        store = factory.object_store(settings)
        verify_raw(store, item)
        verify_wiki(store, api, item)
        verify_vectors(vectors, embedder, settings.vectorize_chunks_index, api, item)

    if args.cleanup:
        from llmwiki import tools

        step("cleanup")
        for item in captured:
            removed = tools.delete_source(item.source_id, cfg=settings)
            print(f"   removed {item.source_id} ({removed} raw objects + vectors); "
                  f"wiki pages are left in place by design")

    print(f"\nVERIFY PASS  {', '.join(f'{c.label}={c.source_id}' for c in captured)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckFailed as exc:
        print(f"\nVERIFY FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
