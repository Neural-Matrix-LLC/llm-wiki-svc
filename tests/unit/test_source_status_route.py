"""QA PRE-01: GET /sources/<malformed id> is a 404, not a 500."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from llmwiki.api.app import create_app
from llmwiki.config import Settings


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    from llmwiki import config, factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake", reranker_backend="none",
                   lexical_backend="memory", local_storage_path=tmp_path, embedding_dim=64)
    monkeypatch.setattr(config, "settings", cfg)
    monkeypatch.setattr("llmwiki.api.routes.settings", cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
    factory.reset()
    yield TestClient(create_app(), raise_server_exceptions=False)
    factory.reset()


@pytest.mark.parametrize("bad", ["qa-no-such-source", "nothing", "0123456789ABCDEF", "x" * 200,
                                 "..%2Eetc", "0123456789abcde"])
def test_malformed_source_id_is_404(client, bad) -> None:
    r = client.get(f"/sources/{bad}")
    assert r.status_code == 404, r.text
    assert r.json() == {"detail": "not a valid source_id"}


def test_well_formed_unknown_id_keeps_its_previous_answer(client) -> None:
    r = client.get("/sources/0123456789abcdef")
    assert r.status_code == 200 and r.json()["state"] == "failed"
