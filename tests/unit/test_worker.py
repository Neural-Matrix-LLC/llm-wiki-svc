"""The ingest worker (Phase 2, plan §21.2 C3/C5): per-domain serialization, recovery, parking.

Threaded tests use sleeps of a few hundred milliseconds: the property under
test is a *lock*, so same-domain compiles are disjoint by construction, and
different-domain overlap only needs two threads to both be running.
"""

from __future__ import annotations

import threading
import time

import pytest
from tests.factories import make_source_meta

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.pipeline.worker import (
    CompileWorker,
    clear_pending,
    domain_lock,
    list_pending,
    mark_pending,
)
from llmwiki.storage.layout import pending_key, raw_extracted, raw_meta, raw_original
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import upsert_domain


@pytest.fixture
def offline_cfg(tmp_path):
    from llmwiki import factory

    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake", local_storage_path=tmp_path / "data",
        embedding_dim=64, worker_mode="inline",
    )
    factory.reset()
    yield cfg
    try:
        factory.compile_worker(cfg).shutdown(wait=True)
    finally:
        factory.reset()


def _capture(store, source_id: str, text: str, domain: str | None = None) -> None:
    meta = make_source_meta(source_id).model_copy(
        update={"modality": "text", "mime": "text/plain", "url": None, "domain": domain})
    store.put(raw_original(source_id, "txt"), text.encode(), "text/plain")
    store.put(raw_meta(source_id), meta.model_dump_json().encode(), "application/json")
    store.put(raw_extracted(source_id), text.encode(), "text/markdown")
    mark_pending(store, source_id)


# --- markers and locks ---------------------------------------------------------------


def test_pending_markers_round_trip(store) -> None:
    mark_pending(store, "a" * 16)
    mark_pending(store, "b" * 16)
    store.put("status/_pending/not-a-source-id", b"")
    assert list_pending(store) == ["a" * 16, "b" * 16]
    clear_pending(store, "a" * 16)
    clear_pending(store, "a" * 16)  # idempotent
    assert list_pending(store) == ["b" * 16]
    assert not store.exists(pending_key("a" * 16))


def test_domain_lock_is_one_object_per_domain() -> None:
    assert domain_lock("ml") is domain_lock("ml")
    assert domain_lock("ml") is not domain_lock("bio")


# --- inline mode ------------------------------------------------------------------------


def test_inline_submit_processes_now_and_clears_the_marker(offline_cfg) -> None:
    from llmwiki import factory

    store = factory.object_store(offline_cfg)
    _capture(store, "a" * 16, "# Retrieval\n\nGrounding answers in documents.\n")
    worker = factory.compile_worker(offline_cfg)

    assert worker.mode == "inline"
    assert worker.submit("a" * 16) is None
    assert tools.get_source_status("a" * 16, cfg=offline_cfg).state == "done"
    assert list_pending(store) == []
    assert worker.status().in_flight == [] and not worker.status().paused


def test_capture_writes_the_marker_and_enqueue_clears_it(offline_cfg) -> None:
    from llmwiki import factory

    store = factory.object_store(offline_cfg)
    ref = tools.ingest_source(text="# Chunking\n\nWindows over text.\n", cfg=offline_cfg)
    assert list_pending(store) == [ref.source_id]
    tools.enqueue_source(ref.source_id, cfg=offline_cfg)
    assert list_pending(store) == []
    assert tools.get_source_status(ref.source_id, cfg=offline_cfg).state == "done"


# --- recovery ------------------------------------------------------------------------------


def test_recover_resubmits_every_pending_source(offline_cfg) -> None:
    from llmwiki import factory

    store = factory.object_store(offline_cfg)
    _capture(store, "a" * 16, "# One\n\nfirst\n")
    _capture(store, "b" * 16, "# Two\n\nsecond\n")
    mark_pending(store, "c" * 16)  # marker with no raw objects: a failed capture

    recovered = tools.recover_pending(cfg=offline_cfg)

    assert recovered == ["a" * 16, "b" * 16, "c" * 16]
    assert tools.get_source_status("a" * 16, cfg=offline_cfg).state == "done"
    assert tools.get_source_status("c" * 16, cfg=offline_cfg).state == "failed"
    assert list_pending(store) == [], "a failed source's marker is cleared too"


def test_app_lifespan_recovers_pending_work_on_startup(offline_cfg, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from llmwiki import config, factory
    from llmwiki.api.app import create_app

    monkeypatch.setattr(config, "settings", offline_cfg)
    monkeypatch.setattr("llmwiki.api.routes.settings", offline_cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", offline_cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", offline_cfg)
    store = factory.object_store(offline_cfg)
    _capture(store, "a" * 16, "# Left over\n\nfrom before the restart\n")

    with TestClient(create_app()) as client:
        assert client.get(f"/sources/{'a' * 16}").json()["state"] == "done"
        assert client.get("/worker").json()["mode"] == "inline"
    assert list_pending(store) == []


# --- parking under the cap (C5) ------------------------------------------------------------


def test_capped_worker_parks_marks_paused_and_keeps_the_marker(offline_cfg) -> None:
    from llmwiki import factory

    store = factory.object_store(offline_cfg)
    _capture(store, "a" * 16, "# Text\n\nbody\n")
    capped = {"on": True}
    pipeline = tools._pipeline(offline_cfg)
    worker = CompileWorker(offline_cfg, store, process=pipeline.process,
                           set_status=pipeline.set_status, capped=lambda: capped["on"])

    status = worker.run_now("a" * 16)

    assert status.state == "paused" and "COST_HARD_CAP_MONTHLY_USD" in (status.error or "")
    assert tools.get_source_status("a" * 16, cfg=offline_cfg).state == "paused"
    assert list_pending(store) == ["a" * 16], "owed work is not forgotten"
    snapshot = worker.status()
    assert snapshot.paused and snapshot.parked == ["a" * 16] and snapshot.reason

    capped["on"] = False
    assert worker.resume() == ["a" * 16]
    assert tools.get_source_status("a" * 16, cfg=offline_cfg).state == "done"
    assert list_pending(store) == [] and not worker.status().paused


def test_threaded_worker_retries_parked_sources_on_its_timer(offline_cfg) -> None:
    from llmwiki import factory

    cfg = offline_cfg.model_copy(update={"worker_mode": "threads", "worker_threads": 2,
                                         "worker_resume_interval_s": 0.05})
    store = factory.object_store(cfg)
    _capture(store, "a" * 16, "# Text\n\nbody\n")
    capped = {"on": True}
    pipeline = tools._pipeline(cfg)
    worker = CompileWorker(cfg, store, process=pipeline.process, set_status=pipeline.set_status,
                           capped=lambda: capped["on"])
    try:
        worker.submit("a" * 16)
        deadline = time.monotonic() + 3
        while worker.status().parked != ["a" * 16] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.status().parked == ["a" * 16]
        capped["on"] = False
        while tools.get_source_status("a" * 16, cfg=cfg).state != "done":
            assert time.monotonic() < deadline, "the timer never retried the parked source"
            time.sleep(0.02)
    finally:
        worker.shutdown()


# --- serialization -----------------------------------------------------------------------


def _timed_compiles(monkeypatch, delay: float) -> list[tuple[str, float, float]]:
    """Patch Compiler.compile_source to record (domain, start, end) and take ``delay``."""
    intervals: list[tuple[str, float, float]] = []
    real = Compiler.compile_source

    def timed(self, doc, force=False, domain="general", **kwargs):
        start = time.monotonic()
        time.sleep(delay)
        result = real(self, doc, force=force, domain=domain, **kwargs)
        intervals.append((domain, start, time.monotonic()))
        return result

    monkeypatch.setattr(Compiler, "compile_source", timed)
    return intervals


def _overlap(a: tuple[str, float, float], b: tuple[str, float, float]) -> bool:
    return a[1] < b[2] and b[1] < a[2]


def test_same_domain_compiles_never_interleave_but_different_domains_do(
    offline_cfg, monkeypatch,
) -> None:
    """Load-bearing (plan §21.2 C3): the manifest has one writer at a time per domain."""
    from llmwiki import factory

    cfg = offline_cfg.model_copy(update={"worker_mode": "threads", "worker_threads": 4})
    store = factory.object_store(cfg)
    upsert_domain(store, "ml")
    upsert_domain(store, "bio")
    for i, (sid, domain) in enumerate([("a" * 16, "ml"), ("b" * 16, "ml"), ("c" * 16, "bio"),
                                        ("d" * 16, "bio")]):
        _capture(store, sid, f"# Doc {i}\n\nbody {i}\n", domain=domain)
    intervals = _timed_compiles(monkeypatch, delay=0.25)
    pipeline = tools._pipeline(cfg)
    worker = CompileWorker(cfg, store, process=pipeline.process, set_status=pipeline.set_status)
    try:
        futures = [worker.submit(sid) for sid in ("a" * 16, "b" * 16, "c" * 16, "d" * 16)]
        for future in futures:
            assert future is not None
            assert future.result(timeout=20).state == "done"
    finally:
        worker.shutdown()

    by_domain = {d: [iv for iv in intervals if iv[0] == d] for d in ("ml", "bio")}
    for domain, ivs in by_domain.items():
        assert len(ivs) == 2 and not _overlap(*ivs), f"{domain} compiles overlapped: {ivs}"
    assert any(_overlap(m, b) for m in by_domain["ml"] for b in by_domain["bio"]), (
        "different domains should have compiled concurrently"
    )
    assert list_pending(store) == []


def test_run_now_waits_for_the_domain_lock(offline_cfg) -> None:
    from llmwiki import factory

    store = factory.object_store(offline_cfg)
    upsert_domain(store, "ml")
    _capture(store, "a" * 16, "# Text\n\nbody\n", domain="ml")
    pipeline = tools._pipeline(offline_cfg)
    worker = CompileWorker(offline_cfg, store, process=pipeline.process,
                           set_status=pipeline.set_status)
    finished = threading.Event()

    def run():
        worker.run_now("a" * 16)
        finished.set()

    with domain_lock("ml"):
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.3)
        assert not finished.is_set(), "run_now must block while the domain is locked"
    thread.join(timeout=10)
    assert finished.is_set()
    assert tools.get_source_status("a" * 16, cfg=offline_cfg).state == "done"


def test_worker_routes_report_status_and_resume_needs_a_token(offline_cfg, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from llmwiki import config
    from llmwiki.api.app import create_app

    cfg = offline_cfg.model_copy(update={"ingest_api_token": SecretStr("test-token")})
    monkeypatch.setattr(config, "settings", cfg)
    monkeypatch.setattr("llmwiki.api.routes.settings", cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
    with TestClient(create_app()) as client:
        body = client.get("/worker").json()
        assert set(body) >= {"mode", "paused", "reason", "queued", "in_flight", "parked"}
        assert client.post("/worker/resume").status_code == 401
        resumed = client.post("/worker/resume", headers={"Authorization": "Bearer test-token"})
        assert resumed.json() == {"ok": True, "resumed": []}
