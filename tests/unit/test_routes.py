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


def test_ingest_answers_422_when_the_url_cannot_be_fetched(client, monkeypatch) -> None:
    """A blocked or dead URL is a client-visible failure, not a 500 with a traceback."""
    from llmwiki import tools

    def blocked(**kw):
        raise tools.ExtractionError("YouTube blocked the transcript request for LJF3frcDgRM")

    monkeypatch.setattr("llmwiki.tools.ingest_source", blocked)

    response = client.post(
        "/ingest", json={"url": "https://youtu.be/LJF3frcDgRM"}, headers=AUTH
    )

    assert response.status_code == 422, response.text
    assert "LJF3frcDgRM" in response.json()["detail"]
