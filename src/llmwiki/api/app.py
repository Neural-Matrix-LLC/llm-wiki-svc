"""FastAPI application, with the MCP server mounted in the same process.

Design doc 4.6: one process serves REST and MCP over the identical ``tools.py``
functions, so the two surfaces cannot drift apart.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from llmwiki import __version__
from llmwiki.api.routes import router
from llmwiki.config import configure_logging
from llmwiki.config import settings as default_settings

configure_logging(default_settings.log_level)
logger = logging.getLogger(__name__)

#: How much of a rejected body to echo back. A validation error quotes what it
#: rejected, which is a debugging aid for a small JSON body and a denial of
#: service for a multi-megabyte upload.
MAX_ECHOED_BODY_CHARS = 500


def create_app() -> FastAPI:
    """Build the application. Called at import time by uvicorn and by the tests."""
    mcp_app = _build_mcp_app()

    app = FastAPI(
        title="llmwiki",
        version=__version__,
        summary="A compiled research knowledge base: immutable raw sources, an LLM-compiled wiki.",
        # The MCP app owns a session manager that has to be started and stopped
        # with the process; handing FastAPI its lifespan is what does that.
        lifespan=None if mcp_app is None else mcp_app.lifespan,
    )
    app.include_router(router)
    # Replaces FastAPI's default 422 handler; see _validation_error.
    app.add_exception_handler(RequestValidationError, _validation_error)

    if mcp_app is not None:
        from llmwiki.mcp.server import mount

        mount(app, mcp_app)

    _mount_channels(app)
    return app


def _mount_channels(app: FastAPI) -> None:
    """Mount optional capture channels (Telegram, email) - Phase 1, KB design §5.

    Each channel degrades to "not mounted" when its own secret is unset, the
    same posture as the optional MCP mount above. Settings is re-imported
    here (not taken from the module-level ``default_settings`` above) so a
    test that monkeypatches ``llmwiki.config.settings`` before calling
    ``create_app()`` again sees its own channel configuration, matching how
    ``api/routes.py`` reads ``settings`` as a live module attribute.
    """
    from llmwiki.channels import email as email_channel
    from llmwiki.channels import telegram as telegram_channel
    from llmwiki.config import settings as current_settings

    for build in (telegram_channel.build_router, email_channel.build_router):
        channel_router = build(current_settings)
        if channel_router is not None:
            app.include_router(channel_router)
async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Return 422 for an invalid body, including one that is not valid UTF-8.

    FastAPI's stock handler encodes the rejected input with a bare
    ``bytes.decode()``. A body whose content type is not JSON is never parsed,
    so the raw bytes reach that call, and binary bytes raise UnicodeDecodeError
    *inside the error handler* - turning a client mistake into a 500 with a
    traceback and no usable message (2026-09-10: a PDF posted as multipart to
    ``/ingest``, which takes JSON; files belong at ``/upload``).
    """
    assert isinstance(exc, RequestValidationError)  # registered for that type only
    body: dict = {"detail": jsonable_encoder(exc.errors(), custom_encoder={bytes: _as_text})}
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        body["hint"] = "this endpoint takes a JSON body; POST a file to /upload instead"
    return JSONResponse(status_code=422, content=body)


def _as_text(raw: bytes) -> str:
    """Render a rejected body as text that is always JSON-encodable, and bounded."""
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        return f"<{len(raw)} bytes of non-UTF-8 data>"
    if len(text) > MAX_ECHOED_BODY_CHARS:
        return f"{text[:MAX_ECHOED_BODY_CHARS]}... <{len(text)} characters total>"
    return text


def _build_mcp_app():  # type: ignore[no-untyped-def]
    """Build the MCP ASGI app, or ``None`` if it cannot be built.

    Optional at runtime: a missing or incompatible ``fastmcp`` must degrade to a
    working REST service rather than a dead process, since the REST surface is
    what the ingest path depends on.
    """
    try:
        from llmwiki.mcp.server import build_http_app

        return build_http_app()
    except Exception as exc:  # pragma: no cover - depends on the installed fastmcp
        logger.warning("MCP server not mounted (%s: %s); REST API is unaffected",
                       type(exc).__name__, exc)
        return None


app = create_app()
