"""Telegram capture channel: webhook mode, in-process with the FastAPI service.

KB design doc §4.1/§5 (Phase 1). Telegram calls ``POST /channels/telegram/webhook``
on this service - there is no long-polling process here. An ``Update`` maps
onto ``tools.ingest_source(...)`` exactly the way every other transport calls
it: plain text becomes ``text=``, a message that is a single bare URL becomes
``url=`` (modality is auto-detected downstream, including YouTube links), and
a forwarded document/photo is downloaded via ``getFile`` and passed as
``file=``. A message matching none of those (a sticker, a poll, ...) is acked
and dropped rather than sent to ``ingest_source``.

Uses raw ``httpx`` (already a core dependency) rather than a Telegram SDK:
webhook mode only ever needs to verify one header, parse one JSON body, and
make two or three Bot API calls - a full polling-oriented client would bring
machinery this design never runs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from llmwiki import tools
from llmwiki.channels.domain_prefix import split_hashtag_domain
from llmwiki.config import Settings
from llmwiki.models.source import SourceRef

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
_BARE_URL = re.compile(r"^\s*(https?://\S+)\s*$")


class _NothingToCapture(Exception):
    """Raised when a Telegram message has no url/file/text to ingest."""


def build_router(cfg: Settings) -> APIRouter | None:
    """Build the Telegram webhook route, or ``None`` if no bot token is set.

    A configured token with an empty ``telegram_webhook_secret`` still builds
    the route (Telegram requires a public HTTPS URL regardless), but every
    request to it will 401 until the secret is also set - failing closed
    rather than accepting unverified updates.
    """
    token = cfg.telegram_bot_token.get_secret_value()
    if not token:
        return None
    secret = cfg.telegram_webhook_secret.get_secret_value()

    router = APIRouter()

    @router.post("/channels/telegram/webhook")
    async def webhook(
        request: Request,
        background: BackgroundTasks,
        x_telegram_bot_api_secret_token: str = Header(default=""),
    ) -> dict:
        if not secret or not secrets.compare_digest(x_telegram_bot_api_secret_token, secret):
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        update = await request.json()
        await _handle_update(update, token, background)
        return {"ok": True}

    return router


async def _handle_update(update: dict, token: str, background: BackgroundTasks) -> None:
    """Queue the capture and return, so the route answers 200 at once.

    Capture fetches the source synchronously (a page, a transcript - or, with
    YOUTUBE_WHISPER_MODEL, minutes of audio transcription). Telegram re-delivers
    any update it has not seen a 2xx for within seconds, so doing that work
    before responding would start the same fetch again in parallel; the
    background task sends the outcome to the chat instead.
    """
    message = update.get("message") or update.get("channel_post")
    if not message:
        return
    background.add_task(_capture_and_process, message, token)


async def _capture_and_process(message: dict, token: str) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    # Phase 2: a leading "#domain" on the caption (files/photos) or the text
    # itself is an explicit domain; it is stripped from what gets stored.
    domain, title = split_hashtag_domain(message.get("caption") or "")
    if domain is None and message.get("text"):
        domain, stripped = split_hashtag_domain(message["text"])
        if domain is not None:
            message = {**message, "text": stripped}

    async with httpx.AsyncClient(base_url=f"{API_BASE}/bot{token}", timeout=30.0) as client:
        try:
            ref = await _capture(message, title, client, token, domain)
        except _NothingToCapture:
            await _ack(client, chat_id, "Nothing to capture in that message.")
            return
        except KeyError as exc:
            await _ack(client, chat_id, f"Unknown domain {exc.args[0]!r}; nothing captured.")
            return
        except (tools.ExtractionError, ValueError) as exc:
            # A fetch the source refused (YouTube blocking a cloud IP, a dead
            # link, an empty message body): tell the sender why.
            logger.warning("telegram capture failed for chat %s: %s", chat_id, exc)
            await _ack(client, chat_id, f"Capture failed: {exc}")
            return
        except Exception:
            # Already past the 200 - the sender would otherwise hear nothing.
            logger.exception("telegram capture crashed for chat %s", chat_id)
            await _ack(client, chat_id, "Capture failed unexpectedly; see the service log.")
            return
        await _ack(client, chat_id, f"Captured. source_id={ref.source_id}")
        if not ref.duplicate:
            # Already inside a background task; WORKER_MODE=inline processes
            # before returning, so keep it off the event loop as well.
            await asyncio.to_thread(tools.enqueue_source, ref.source_id)


async def _capture(
    message: dict, title: str, client: httpx.AsyncClient, token: str,
    domain: str | None = None,
) -> SourceRef:
    document = message.get("document")
    photo = message.get("photo")  # list of sizes, largest last
    text = message.get("text")

    # ingest_source is synchronous and may spend minutes on a fetch; run it
    # off the event loop so /healthz (the Docker healthcheck) keeps answering.
    if document is not None:
        file_id = document["file_id"]
        filename = document.get("file_name") or f"{file_id}.jpg"
        mime = document.get("mime_type") or "image/jpeg"
        data = await _download(client, token, file_id)
        return await asyncio.to_thread(
            tools.ingest_source,
            file=data, filename=filename, mime=mime, title=title, domain=domain,
        )

    if photo:
        file_id = photo[-1]["file_id"]
        data = await _download(client, token, file_id)
        return await asyncio.to_thread(
            tools.ingest_source,
            file=data, filename=f"{file_id}.jpg", mime="image/jpeg", title=title, domain=domain,
        )

    if text:
        bare_url = _BARE_URL.match(text)
        if bare_url:
            return await asyncio.to_thread(
                tools.ingest_source, url=bare_url.group(1), title=title, domain=domain
            )
        return await asyncio.to_thread(tools.ingest_source, text=text, title=title, domain=domain)

    raise _NothingToCapture()


async def _download(client: httpx.AsyncClient, token: str, file_id: str) -> bytes:
    response = await client.get("/getFile", params={"file_id": file_id})
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    download = await client.get(f"{API_BASE}/file/bot{token}/{file_path}")
    download.raise_for_status()
    return download.content


async def _ack(client: httpx.AsyncClient, chat_id: int | None, text: str) -> None:
    if chat_id is None:
        return
    try:
        await client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:  # pragma: no cover - best-effort ack, never fails ingest
        logger.warning("failed to send Telegram ack to chat %s", chat_id)
