"""Telegram and email capture channels: auth, mapping onto ingest_source, optional mount."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from llmwiki import config, factory
from llmwiki.config import Settings


@pytest.fixture
def cfg(tmp_path):
    return Settings(
        _env_file=None,
        storage_backend="local",
        vector_backend="memory",
        embedding_backend="fake",
        llm_backend="fake",
        local_storage_path=tmp_path / "data",
        embedding_dim=64,
    )


# --- Telegram --------------------------------------------------------------


def test_build_router_is_none_without_a_bot_token(cfg) -> None:
    from llmwiki.channels.telegram import build_router

    assert build_router(cfg) is None


def _set_telegram(cfg: Settings, token: str = "123:abc", secret: str = "shh") -> None:
    """Set Telegram fields to real ``SecretStr`` values.

    Assigning a plain ``str`` (``cfg.telegram_bot_token = "..."``) skips
    pydantic coercion, since ``Settings`` doesn't set ``validate_assignment``
    - assign a ``SecretStr`` instance directly instead.
    """
    cfg.telegram_bot_token = SecretStr(token)
    cfg.telegram_webhook_secret = SecretStr(secret)


def test_webhook_without_the_secret_header_is_401(cfg, monkeypatch) -> None:
    from llmwiki.channels import telegram

    _set_telegram(cfg)
    monkeypatch.setattr(telegram, "_handle_update", AsyncMock())

    router = telegram.build_router(cfg)
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/channels/telegram/webhook", json={})
    assert response.status_code == 401

    response = client.post(
        "/channels/telegram/webhook", json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 401


def test_webhook_with_the_right_secret_dispatches_the_update(cfg, monkeypatch) -> None:
    from llmwiki.channels import telegram

    _set_telegram(cfg)
    handled = AsyncMock()
    monkeypatch.setattr(telegram, "_handle_update", handled)

    router = telegram.build_router(cfg)
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    body = {"message": {"chat": {"id": 1}, "text": "hello"}}
    response = client.post(
        "/channels/telegram/webhook", json=body,
        headers={"X-Telegram-Bot-Api-Secret-Token": "shh"},
    )
    assert response.status_code == 200
    handled.assert_awaited_once()
    assert handled.await_args.args[0] == body


@pytest.mark.asyncio
async def test_plain_text_message_becomes_a_text_source(monkeypatch) -> None:
    from llmwiki.channels import telegram

    calls = []
    monkeypatch.setattr(
        "llmwiki.tools.ingest_source",
        lambda **kw: calls.append(kw) or _fake_ref(),
    )

    ref = await telegram._capture({"text": "just some notes"}, "", client=None, token="t")

    assert calls[0]["text"] == "just some notes"
    assert calls[0].get("url") is None
    assert ref.source_id


@pytest.mark.asyncio
async def test_bare_url_message_becomes_a_url_source(monkeypatch) -> None:
    from llmwiki.channels import telegram

    calls = []
    monkeypatch.setattr(
        "llmwiki.tools.ingest_source",
        lambda **kw: calls.append(kw) or _fake_ref(),
    )

    await telegram._capture({"text": "https://example.org/post"}, "", client=None, token="t")

    assert calls[0]["url"] == "https://example.org/post"
    assert calls[0].get("text") is None


@pytest.mark.asyncio
async def test_document_message_downloads_and_ingests_as_a_file(monkeypatch) -> None:
    from llmwiki.channels import telegram

    calls = []
    monkeypatch.setattr(
        "llmwiki.tools.ingest_source",
        lambda **kw: calls.append(kw) or _fake_ref(),
    )
    monkeypatch.setattr(telegram, "_download", AsyncMock(return_value=b"%PDF-1.4 ..."))

    message = {
        "document": {"file_id": "f1", "file_name": "paper.pdf", "mime_type": "application/pdf"},
        "caption": "worth reading",
    }
    await telegram._capture(message, "worth reading", client=None, token="t")

    assert calls[0]["file"] == b"%PDF-1.4 ..."
    assert calls[0]["filename"] == "paper.pdf"
    assert calls[0]["mime"] == "application/pdf"
    assert calls[0]["title"] == "worth reading"


@pytest.mark.asyncio
async def test_a_message_with_nothing_to_capture_raises() -> None:
    from llmwiki.channels import telegram

    with pytest.raises(telegram._NothingToCapture):
        await telegram._capture({"sticker": {}}, "", client=None, token="t")


def _fake_ref():
    from llmwiki.models.source import SourceRef

    return SourceRef(source_id="a" * 16, duplicate=False)


# --- Email -------------------------------------------------------------


def test_email_build_router_is_none_without_a_signing_key(cfg) -> None:
    from llmwiki.channels.email import build_router

    assert build_router(cfg) is None


def _mailgun_form(signing_key: str, **fields) -> dict:
    timestamp = "1"
    token = "tok"
    signature = hmac.new(
        signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
    ).hexdigest()
    return {"timestamp": timestamp, "token": token, "signature": signature, **fields}


def test_email_webhook_rejects_a_bad_signature(cfg) -> None:
    from fastapi import FastAPI

    from llmwiki.channels.email import build_router

    cfg.mailgun_signing_key = SecretStr("key")
    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)

    response = client.post(
        "/channels/email/inbound",
        data={"timestamp": "1", "token": "tok", "signature": "bogus", "subject": "hi"},
    )
    assert response.status_code == 401


def test_email_webhook_ingests_bare_url_body(cfg, monkeypatch) -> None:
    from fastapi import FastAPI

    from llmwiki.channels.email import build_router

    cfg.mailgun_signing_key = SecretStr("key")
    calls = []
    monkeypatch.setattr(
        "llmwiki.tools.ingest_source", lambda **kw: calls.append(kw) or _fake_ref()
    )
    monkeypatch.setattr("llmwiki.tools.process_source", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)

    form = _mailgun_form(
        "key", subject="a link", **{"stripped-text": "https://example.org/x",
                                     "attachment-count": "0"},
    )
    response = client.post("/channels/email/inbound", data=form)

    assert response.status_code == 200, response.text
    assert calls[0]["url"] == "https://example.org/x"
    assert calls[0]["title"] == "a link"


def test_email_webhook_ingests_an_attachment_and_the_cover_note(cfg, monkeypatch) -> None:
    from fastapi import FastAPI

    from llmwiki.channels.email import build_router

    cfg.mailgun_signing_key = SecretStr("key")
    calls = []
    monkeypatch.setattr(
        "llmwiki.tools.ingest_source", lambda **kw: calls.append(kw) or _fake_ref()
    )
    monkeypatch.setattr("llmwiki.tools.process_source", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(build_router(cfg))
    client = TestClient(app)

    form = _mailgun_form("key", subject="paper + note", **{"stripped-text": "please read this",
                                                            "attachment-count": "1"})
    files = {"attachment-1": ("paper.pdf", b"%PDF-1.4", "application/pdf")}
    response = client.post("/channels/email/inbound", data=form, files=files)

    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert calls[0]["file"] == b"%PDF-1.4"
    assert calls[0]["filename"] == "paper.pdf"
    assert calls[1]["text"] == "please read this"


# --- Optional mount into the FastAPI app -----------------------------------


def test_app_mounts_no_channel_routes_when_unconfigured(tmp_path, monkeypatch) -> None:
    from llmwiki.api.app import create_app

    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake",
        local_storage_path=tmp_path / "data", embedding_dim=64,
    )
    monkeypatch.setattr(config, "settings", cfg)
    factory.reset()

    client = TestClient(create_app())
    # An unmounted route 404s; a mounted one would 401 (missing secret header)
    # or 422, never 404 - this distinguishes "not registered" from "registered
    # but rejected", which route-introspection can't do across Starlette
    # versions (``include_router`` wraps routes differently release to release).
    assert client.post("/channels/telegram/webhook", json={}).status_code == 404
    assert client.post("/channels/email/inbound", data={}).status_code == 404
    factory.reset()


def test_app_mounts_telegram_route_when_configured(tmp_path, monkeypatch) -> None:
    from llmwiki.api.app import create_app

    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake",
        local_storage_path=tmp_path / "data", embedding_dim=64,
        telegram_bot_token="123:abc", telegram_webhook_secret="shh",
    )
    monkeypatch.setattr(config, "settings", cfg)
    factory.reset()

    client = TestClient(create_app())
    # Mounted and rejected for a missing secret header (not 404 - not mounted).
    assert client.post("/channels/telegram/webhook", json={}).status_code == 401
    factory.reset()
