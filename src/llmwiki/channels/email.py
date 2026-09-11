"""Email capture channel: provider inbound-parse webhook (Mailgun convention).

KB design doc §4.1/§5 (Phase 1). No IMAP polling: a transactional-email
provider parses inbound mail and POSTs the result to
``POST /channels/email/inbound`` as ``multipart/form-data``. Designed against
Mailgun's inbound-parse payload shape (``sender``, ``subject``,
``stripped-text``/``body-plain``, ``attachment-count``, ``attachment-1..N``,
``timestamp``, ``token``, ``signature``) since it needs no extra dependency -
FastAPI's existing form/file handling plus stdlib HMAC verification - and a
different provider's payload would only change the parsing in this one
module, not the ``ingest_source`` mapping below.

Each attachment becomes its own source (an email can legitimately capture
more than one document); the body becomes a source too, as a URL or as text,
whenever it isn't just a caption for the attachment(s).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from starlette.datastructures import FormData

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.models.source import SourceRef

logger = logging.getLogger(__name__)
_BARE_URL = re.compile(r"^\s*(https?://\S+)\s*$")


def build_router(cfg: Settings) -> APIRouter | None:
    """Build the inbound-mail route, or ``None`` if no signing key is set."""
    signing_key = cfg.mailgun_signing_key.get_secret_value()
    if not signing_key:
        return None

    router = APIRouter()

    @router.post("/channels/email/inbound")
    async def inbound(request: Request, background: BackgroundTasks) -> dict:
        form = await request.form()
        _verify(signing_key, form)
        refs = await _capture(form)
        for ref in refs:
            if not ref.duplicate:
                background.add_task(tools.process_source, ref.source_id)
        return {"ok": True, "source_ids": [ref.source_id for ref in refs]}

    return router


def _verify(signing_key: str, form: FormData) -> None:
    """Mailgun's documented HMAC check: sha256(timestamp + token) == signature."""
    timestamp = str(form.get("timestamp", ""))
    token = str(form.get("token", ""))
    signature = str(form.get("signature", ""))
    expected = hmac.new(
        signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
    ).hexdigest()
    if not signature or not secrets.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid Mailgun signature")


async def _capture(form: FormData) -> list[SourceRef]:
    subject = str(form.get("subject") or "")
    body = str(form.get("stripped-text") or form.get("body-plain") or "")
    refs: list[SourceRef] = []

    count = int(str(form.get("attachment-count") or 0) or 0)
    for i in range(1, count + 1):
        upload = form.get(f"attachment-{i}")
        if upload is None or isinstance(upload, str):
            continue
        data = await upload.read()
        refs.append(
            tools.ingest_source(
                file=data,
                filename=upload.filename,
                mime=upload.content_type or "application/octet-stream",
                title=subject,
            )
        )

    if refs:
        # Attachments plus a real cover note: capture the note too, rather
        # than silently dropping it.
        if body.strip():
            refs.append(tools.ingest_source(text=body, title=subject))
        return refs

    bare_url = _BARE_URL.match(body) if body else None
    if bare_url:
        refs.append(tools.ingest_source(url=bare_url.group(1), title=subject))
    elif body.strip():
        refs.append(tools.ingest_source(text=body, title=subject))
    return refs
