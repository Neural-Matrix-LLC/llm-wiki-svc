"""HTTP surface: auth, validation, and parity with the tool functions."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from llmwiki.api.app import create_app
from llmwiki.config import Settings
from llmwiki.storage.layout import is_source_id


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app wired to the offline backends, with a known bearer token."""
    from llmwiki import config, factory

    cfg = Settings(
        _env_file=None,
        storage_backend="local",
        vector_backend="memory",
        embedding_backend="fake",
        llm_backend="fake",
        local_storage_path=tmp_path / "data",
        embedding_dim=64,
        ingest_api_token="test-token",
    )
    monkeypatch.setattr(config, "settings", cfg)
    monkeypatch.setattr("llmwiki.api.routes.settings", cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
    factory.reset()
    yield TestClient(create_app())
    factory.reset()


AUTH = {"Authorization": "Bearer test-token"}


def test_healthz_needs_no_token_and_reports_backends(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backends"]["storage"] == "local"
    assert body["version"]


def test_healthz_echoes_the_effective_log_level(client) -> None:
    """A deployed container's environment is only visible from outside via this.

    Regression guard for the Hostinger deploy (2026-09-10): an edited .env that
    was never applied - `compose restart` reuses the environment baked in at
    container-create time - looked identical from outside to one that had been.
    """
    body = client.get("/healthz").json()

    assert body["log_level"] == "INFO"


def test_healthz_reports_whether_the_outside_package_config_was_found(client) -> None:
    """The three paths that live outside the package, by resolved location.

    Regression guard for the 2026-09-10 Hostinger deploy: the image carried
    neither ``config/`` nor ``skills/``, so the service ran healthy while
    silently taking the single-provider fallback and the fixed answer_query
    prompt. Absence is a legitimate configuration, so the fix is not to fail -
    it is to make which mode is in force visible from outside the box.
    """
    body = client.get("/healthz").json()
    config = body["config"]

    assert config["llm_routing"] in {"per-op table", "single-provider fallback"}
    for key in ("providers_config", "ops_config", "skills_dir"):
        # Reported resolved, because every one of them is relative by default
        # and so means different things in different working directories.
        assert config[key]["path"].startswith("/")
        assert isinstance(config[key]["present"], bool)
    assert isinstance(config["skills_dir"]["skill_files"], int)


def test_a_masked_token_shows_its_ends_but_never_the_middle() -> None:
    """The DEBUG line exists to compare two tokens, not to hand one over."""
    from llmwiki.api.routes import mask

    token = "llmw_0123456789abcdef_XYZQR"

    rendered = mask(token)

    assert rendered.startswith("llmw_")
    assert rendered.endswith("(27 chars)")
    assert "XYZQR" in rendered
    assert token not in rendered
    assert "0123456789abcdef" not in rendered


def test_a_token_too_short_to_mask_shows_only_its_length() -> None:
    """First-5 + last-5 of a 10-character token is the whole token."""
    from llmwiki.api.routes import mask

    assert mask("test-token") == "<10 chars, too short to show safely>"
    assert mask("") == "<empty>"


def test_debug_logging_never_writes_the_bearer_token(client, caplog) -> None:
    """Regression guard (2026-09-10): this line used to log both tokens in full.

    DEBUG is what a deployment turns on when auth is misbehaving, which is
    exactly when the log gets pasted into a ticket - so the full token must not
    be in it even though the level is enabled.
    """
    with caplog.at_level(logging.DEBUG, logger="llmwiki.api.routes"):
        client.post("/lint", headers=AUTH)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "require_token:" in logged, "the debug line should still fire"
    assert "test-token" not in logged


def test_ingest_without_a_token_is_401(client) -> None:
    response = client.post("/ingest", json={"url": "https://example.org/a"})
    assert response.status_code == 401


def test_ingest_with_a_wrong_token_is_401(client) -> None:
    response = client.post(
        "/ingest", json={"url": "https://example.org/a"}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_ingest_rejects_a_malformed_body(client) -> None:
    response = client.post("/ingest", json={"not_a_url": 1}, headers=AUTH)
    assert response.status_code == 422


def test_a_binary_body_posted_to_a_json_route_is_422_not_500(client) -> None:
    """A client mistake must not become a server error.

    Regression guard (2026-09-10): a PDF was posted as multipart/form-data to
    ``/ingest``, which takes JSON. FastAPI never parses a non-JSON content type,
    so the raw bytes land in the validation error, and its stock handler encodes
    them with a bare ``bytes.decode()`` - UnicodeDecodeError *inside the error
    handler*, i.e. a 500 and a traceback instead of the 422 the caller needs.
    """
    pdf = b"%PDF-1.4\n" + bytes(range(256)) + b"\n%%EOF\n"  # 0xbf is not valid UTF-8

    response = client.post(
        "/ingest", files={"file": ("guide.pdf", pdf, "application/pdf")}, headers=AUTH
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert "non-UTF-8" in body["detail"][0]["input"]
    # The caller used the wrong endpoint, so the 422 says which one is right.
    assert "/upload" in body["hint"]


def test_a_huge_rejected_body_is_not_echoed_back_in_full(client) -> None:
    """Quoting the rejected input is a debugging aid, not an amplifier.

    A content type FastAPI does not parse as JSON hands the *whole* raw body to
    the error, and the stock handler echoes every byte of it back.
    """
    response = client.post(
        "/ingest",
        content=b"x" * 200_000,
        headers={**AUTH, "content-type": "text/plain"},
    )

    assert response.status_code == 422
    assert len(response.content) < 2_000, len(response.content)


def test_ingest_rejects_a_body_naming_both_a_url_and_text(client) -> None:
    response = client.post(
        "/ingest", json={"url": "https://example.org/a", "text": "pasted"}, headers=AUTH
    )
    assert response.status_code == 422


def test_ingest_accepts_pure_text_and_reaches_done(client) -> None:
    """Text with no file and no URL is a first-class source over REST."""
    response = client.post(
        "/ingest",
        json={"text": "# Retrieval\n\nGrounding answers in retrieved documents.\n"},
        headers=AUTH,
    )

    assert response.status_code == 200, response.text
    source_id = response.json()["source_id"]
    assert is_source_id(source_id)
    # TestClient runs background tasks before returning, so the poll is settled.
    status = client.get(f"/sources/{source_id}")
    assert status.json()["state"] == "done", status.json()


def test_ingest_text_requires_a_token(client) -> None:
    assert client.post("/ingest", json={"text": "pasted"}).status_code == 401


def test_upload_captures_and_returns_a_source_ref(client) -> None:
    response = client.post(
        "/upload",
        files={"file": ("notes.md", b"# Retrieval\n\nGrounding answers in documents.\n",
                        "text/markdown")},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert is_source_id(body["source_id"])
    assert body["status"] in ("queued", "done")


def test_upload_then_poll_reaches_done(client) -> None:
    response = client.post(
        "/upload",
        files={"file": ("notes.md", b"# Retrieval\n\nGrounding answers in documents.\n",
                        "text/markdown")},
        headers=AUTH,
    )
    source_id = response.json()["source_id"]

    # TestClient runs background tasks synchronously once the response is consumed.
    status = client.get(f"/sources/{source_id}").json()
    assert status["state"] in ("done", "queued", "compiling"), status


def test_search_and_concepts_are_readable_without_a_token(client) -> None:
    assert client.get("/search", params={"q": "retrieval"}).status_code == 200
    assert client.get("/concepts").status_code == 200


def test_missing_page_is_404(client) -> None:
    assert client.get("/page/no-such-page").status_code == 404


def test_lint_requires_a_token(client) -> None:
    assert client.post("/lint").status_code == 401
    assert client.post("/lint", headers=AUTH).status_code == 200


# --- Phase 1-D: the richer Answer, and the feedback endpoint ---------------------


def test_answer_carries_the_query_graph_fields(client) -> None:
    client.post("/ingest", json={"text": "# Retrieval\n\nGrounding answers in retrieved "
                                          "documents.\n"}, headers=AUTH)
    body = client.get("/answer", params={"q": "retrieval grounding"}).json()

    assert set(body) >= {"text", "citations", "used_rag_fallback", "steps", "context",
                         "external_refs", "run_id"}
    assert body["steps"] == [] and body["external_refs"] == []
    assert body["run_id"] is None, "tracing is off, so there is no run to point at"
    assert body["context"], "the eval judge needs what the answer was written from"


def test_healthz_reports_the_query_graph_bounds(client) -> None:
    graph = client.get("/healthz").json()["config"]["query_graph"]
    assert set(graph) == {"max_tool_calls", "web_search_policy", "web_search_backend",
                          "langsmith_tracing"}
    assert graph["web_search_policy"] == "off"


def test_feedback_requires_a_token(client) -> None:
    body = {"run_id": "00000000-0000-0000-0000-000000000000", "score": 0}
    assert client.post("/feedback", json=body).status_code == 401


def test_feedback_is_409_when_tracing_is_off(client) -> None:
    body = {"run_id": "00000000-0000-0000-0000-000000000000", "score": 0, "correction": "x"}
    response = client.post("/feedback", json=body, headers=AUTH)
    assert response.status_code == 409
    assert "LANGSMITH_TRACING" in response.json()["detail"]


def test_feedback_rejects_a_score_outside_zero_to_one(client) -> None:
    body = {"run_id": "r", "score": 7}
    assert client.post("/feedback", json=body, headers=AUTH).status_code == 422


def test_feedback_forwards_to_tools_record_feedback(client, monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_record(run_id, score, correction="", cfg=None):
        calls.append((run_id, score, correction))
        return "fb-1"

    monkeypatch.setattr("llmwiki.tools.record_feedback", fake_record)
    body = {"run_id": "run-1", "score": 0.0, "correction": "cite 7b2f6aed523349f5-sample"}
    response = client.post("/feedback", json=body, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "feedback_id": "fb-1", "run_id": "run-1"}
    assert calls == [("run-1", 0.0, "cite 7b2f6aed523349f5-sample")]


# --- Phase 2: domains over REST (plan §21.2 A1/A3, §21.7) ---------------------------------


def test_domains_registry_is_general_only_until_one_is_registered(client) -> None:
    response = client.get("/domains")
    assert response.status_code == 200
    assert [d["name"] for d in response.json()] == ["general"]


def test_put_domain_needs_a_token_validates_the_name_and_registers(client) -> None:
    assert client.put("/domains/ml", json={"description": "x"}).status_code == 401
    assert client.put("/domains/Not%20Valid", json={}, headers=AUTH).status_code == 422
    assert client.put("/domains/general", json={}, headers=AUTH).status_code == 422

    created = client.put("/domains/ml-systems", json={"description": "GPU infra"}, headers=AUTH)
    assert created.status_code == 200, created.text
    assert created.json()["name"] == "ml-systems"
    names = [d["name"] for d in client.get("/domains").json()]
    assert names == ["general", "ml-systems"]


def test_ingest_into_an_explicit_domain_compiles_there(client) -> None:
    """End to end over REST: register, ingest with domain=, then read the domain's
    manifest and page - and confirm general stayed empty."""
    client.put("/domains/ml", json={"description": "Machine learning"}, headers=AUTH)

    response = client.post(
        "/ingest",
        json={"text": "# Retrieval\n\nGrounding answers in retrieved documents.\n",
              "domain": "ml"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    source_id = response.json()["source_id"]
    status = client.get(f"/sources/{source_id}").json()
    assert status["state"] == "done" and status["domain"] == "ml", status

    ml_gists = client.get("/concepts", params={"domain": "ml"}).json()
    assert ml_gists, "the domain's manifest holds the compiled pages"
    assert client.get("/concepts").json() == [], "general's manifest was never written"

    slug = next(g["slug"] for g in ml_gists if g["type"] == "concept")
    page = client.get(f"/page/{slug}", params={"domain": "ml"})
    assert page.status_code == 200 and "domain: ml" in page.text
    assert client.get(f"/page/{slug}").status_code == 404, "not a general page"

    # compile_update stays in the routed domain and is idempotent.
    again = client.post(f"/compile/{source_id}", headers=AUTH).json()
    assert again["domain"] == "ml" and again["reason"].startswith("source already compiled")


def test_ingest_into_an_unknown_domain_is_a_404_and_captures_nothing(client) -> None:
    response = client.post("/ingest", json={"text": "hello world", "domain": "nope"},
                           headers=AUTH)
    assert response.status_code == 404
    assert client.get("/concepts").json() == []


def test_upload_accepts_a_domain_form_field(client) -> None:
    client.put("/domains/ml", json={}, headers=AUTH)
    response = client.post(
        "/upload",
        files={"file": ("note.md", b"# Chunking\n\nSplit text into windows.\n", "text/markdown")},
        data={"domain": "ml"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    status = client.get(f"/sources/{response.json()['source_id']}").json()
    assert status["domain"] == "ml"


def test_concepts_and_lint_reject_an_unknown_domain(client) -> None:
    assert client.get("/concepts", params={"domain": "nope"}).status_code == 404
    assert client.post("/lint", params={"domain": "nope"}, headers=AUTH).status_code == 404
    report = client.post("/lint", headers=AUTH).json()
    assert report["domains"] == ["general"]


def test_delete_domain_409s_while_it_has_pages_then_removes_with_force(client) -> None:
    client.put("/domains/ml", json={}, headers=AUTH)
    client.post("/ingest", json={"text": "# Retrieval\n\nGrounding.\n", "domain": "ml"},
                headers=AUTH)
    assert client.delete("/domains/ml").status_code == 401
    assert client.delete("/domains/ml", headers=AUTH).status_code == 409
    assert client.delete("/domains/ml", params={"force": "true"}, headers=AUTH).status_code == 200
    assert client.delete("/domains/ml", headers=AUTH).status_code == 404
    assert [d["name"] for d in client.get("/domains").json()] == ["general"]


def test_search_and_answer_take_a_domain_and_404_an_unknown_one(client) -> None:
    client.put("/domains/ml", json={"description": "ML"}, headers=AUTH)
    client.post("/ingest", json={"text": "# Paged attention\n\nKV cache blocks on the GPU.\n",
                                 "domain": "ml"}, headers=AUTH)

    bad = {"q": "paged attention", "domain": "nope"}
    assert client.get("/search", params=bad).status_code == 404
    assert client.get("/answer", params=bad).status_code == 404

    hits = client.get("/search", params={"q": "paged attention", "domain": "ml"}).json()
    assert hits and all(h["domain"] == "ml" for h in hits)
    assert client.get("/search", params={"q": "paged attention", "domain": "general"}).json() == []

    body = client.get("/answer", params={"q": "paged attention"}).json()
    assert body["domains"] == ["general", "ml"], "policy all fans out"
    assert "cost_usd" in body
    scoped = client.get("/answer", params={"q": "paged attention", "domain": "ml"}).json()
    assert scoped["domains"] == ["ml"]


def test_synthesize_route_needs_a_token_and_404s_unknown_domains(client) -> None:
    client.post("/ingest", json={"text": "# Chunking\n\nWindows over text.\n"}, headers=AUTH)
    assert client.post("/synthesize/general").status_code == 401
    assert client.post("/synthesize/nope", headers=AUTH).status_code == 404
    body = client.post("/synthesize/general", headers=AUTH).json()
    assert body["written"] is True and body["domain"] == "general"
    assert client.get("/page/overview").status_code == 200
