"""scripts/verify_capture.py: the checks it applies to what /ingest and /upload leave behind.

The script is a standalone entry point that talks to a running service over
HTTP and reads the backends directly. Here the "service" is the FastAPI app
under ``TestClient`` (an ``httpx.Client``, so the script's ``Api`` wrapper
needs no shim) on the offline backends, in the same process - so the factory
cache hands the script the very object store and memory vector store the app
wrote to, and every read-back path runs, not just the REST half.

What is pinned is the judgement: a clean capture passes every check, and each
kind of corruption the script exists to catch (a swapped original, a chunk
whose stored text is not the slice its offsets name, a missing source note) is
named as a failure.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from llmwiki.config import Settings
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_extracted, raw_meta, raw_original, wiki_source_note

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "sample.pdf"
TRANSCRIPT = REPO / "tests" / "fixtures" / "transcript.json"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vc() -> ModuleType:
    return _load("verify_capture")


@pytest.fixture
def env(tmp_path, monkeypatch, vc):
    """The app on offline backends plus the script's view of the same backends."""
    from llmwiki import config, factory

    # The memory store is immediately consistent; the Vectorize back-off is dead time here.
    monkeypatch.setattr(vc, "VECTOR_RETRY_S", 0)
    from llmwiki.api.app import create_app

    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_provider="fake", web_search_backend="none",
        local_storage_path=tmp_path / "data", embedding_dim=64,
        ingest_api_token="verify-capture-test-token",
    )
    for target in ("llmwiki.config.settings", "llmwiki.api.routes.settings",
                   "llmwiki.tools.default_settings", "llmwiki.factory.default_settings"):
        monkeypatch.setattr(target, cfg)
    monkeypatch.setattr(config, "settings", cfg)
    factory.reset()
    client = TestClient(create_app())
    yield cfg, client, factory.object_store(cfg), factory.vector_store(cfg), factory.embedder(cfg)
    factory.reset()


def _api(vc: ModuleType, client: TestClient) -> object:
    api = vc.Api.__new__(vc.Api)
    api.client = client
    api.auth = {"Authorization": "Bearer verify-capture-test-token"}
    return api


def _run_all(vc: ModuleType, env, captured) -> None:
    cfg, client, store, vectors, embedder = env
    api = _api(vc, client)
    vc.verify_raw(store, captured)
    vc.verify_wiki(store, api, captured)
    vc.verify_vectors(vectors, embedder, cfg.vectorize_chunks_index, api, captured)


def test_clean_pdf_upload_passes_every_check(vc, env) -> None:
    cfg, client, store, vectors, embedder = env

    captured = vc.capture_pdf(_api(vc, client), FIXTURE, timeout_s=30)

    assert captured.source_id.startswith(hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16])
    assert not captured.duplicate
    assert captured.chunk_count > 0
    _run_all(vc, env, captured)
    assert captured.extracted.strip()
    assert captured.pages, "the wiki check must record which pages cite the source"


def test_duplicate_upload_is_verified_not_rejected(vc, env) -> None:
    cfg, client, *_ = env
    api = _api(vc, client)
    first = vc.capture_pdf(api, FIXTURE, timeout_s=30)

    again = vc.capture_pdf(api, FIXTURE, timeout_s=30)

    assert again.duplicate and again.source_id == first.source_id
    _run_all(vc, env, again)


def test_swapped_original_is_caught(vc, env) -> None:
    cfg, client, store, *_ = env
    captured = vc.capture_pdf(_api(vc, client), FIXTURE, timeout_s=30)
    store.put(raw_original(captured.source_id, "pdf"), b"%PDF-1.4 not the same bytes")

    with pytest.raises(vc.CheckFailed, match="sha256"):
        vc.verify_raw(store, captured)


def test_chunk_text_that_is_not_its_slice_of_extracted_md_is_caught(vc, env) -> None:
    cfg, client, store, vectors, embedder = env
    captured = vc.capture_pdf(_api(vc, client), FIXTURE, timeout_s=30)
    vc.verify_raw(store, captured)
    [hit, *_] = vectors.query(cfg.vectorize_chunks_index, embedder.embed(["probe"])[0],
                              k=1, where={"source_id": captured.source_id})
    tampered = dict(hit.metadata, text="text that never came out of the extractor")
    vectors.upsert(cfg.vectorize_chunks_index, [hit.id], [embedder.embed(["x"])[0]], [tampered])

    with pytest.raises(vc.CheckFailed, match="not extracted.md"):
        vc.verify_vectors_direct(vectors, embedder, cfg.vectorize_chunks_index, captured)


def test_vector_count_disagreeing_with_the_pipeline_is_caught(vc, env) -> None:
    cfg, client, store, vectors, embedder = env
    captured = vc.capture_pdf(_api(vc, client), FIXTURE, timeout_s=30)
    vc.verify_raw(store, captured)
    captured.chunk_count += 1  # the pipeline claims one more chunk than is stored

    with pytest.raises(vc.CheckFailed, match="pipeline reported"):
        vc.verify_vectors_direct(vectors, embedder, cfg.vectorize_chunks_index, captured)


def test_missing_source_note_is_caught(vc, env) -> None:
    cfg, client, store, *_ = env
    captured = vc.capture_pdf(_api(vc, client), FIXTURE, timeout_s=30)
    store.delete(wiki_source_note(captured.source_id))

    with pytest.raises(vc.CheckFailed, match="per-source note"):
        vc.verify_wiki(store, _api(vc, client), captured)


def test_youtube_raw_shape_is_checked_against_the_segment_contract(vc, env) -> None:
    """The YouTube half cannot be captured offline; its raw/ contract can still be pinned."""
    cfg, client, store, *_ = env
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    data = json.dumps(json.loads(TRANSCRIPT.read_text()), ensure_ascii=False).encode()
    source_id = hashlib.sha256(url.encode()).hexdigest()[:16] + "-a-talk"
    meta = SourceMeta(source_id=source_id, modality="youtube", title="A talk", url=url,
                      mime="application/json", sha256=hashlib.sha256(data).hexdigest(),
                      byte_size=len(data))
    store.put(raw_original(source_id, "json"), data, "application/json")
    store.put(raw_meta(source_id), meta.model_dump_json().encode(), "application/json")
    store.put(raw_extracted(source_id), b"the transcript text", "text/markdown")
    captured = vc.Captured("youtube", source_id, "youtube", False, url=url)

    vc.verify_raw(store, captured)

    assert captured.extracted == "the transcript text"

    # A segment list missing the contract's keys is not a captured transcript.
    store.put(raw_original(source_id, "json"), b'[{"caption": "x"}]', "application/json")
    store.put(raw_meta(source_id), meta.model_copy(update={
        "sha256": hashlib.sha256(b'[{"caption": "x"}]').hexdigest(), "byte_size": 18,
    }).model_dump_json().encode(), "application/json")
    with pytest.raises(vc.CheckFailed, match="segment shape"):
        vc.verify_raw(store, captured)


def test_wrong_bearer_token_is_reported_with_the_api_detail(vc, env) -> None:
    cfg, client, *_ = env
    api = _api(vc, client)
    api.auth = {"Authorization": "Bearer nope"}

    with pytest.raises(vc.CheckFailed, match="401"):
        vc.capture_pdf(api, FIXTURE, timeout_s=30)
