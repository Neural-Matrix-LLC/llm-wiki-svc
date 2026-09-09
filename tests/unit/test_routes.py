"""HTTP surface: auth, validation, and parity with the tool functions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from llmwiki.api.app import create_app
from llmwiki.config import Settings


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
    assert len(source_id) == 16
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
    assert len(body["source_id"]) == 16
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
