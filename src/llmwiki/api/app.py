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
from llmwiki.api.proxyguard import disable_uvicorn_proxy_headers
from llmwiki.api.routes import CSRFFailed, Forbidden, router
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
        # with the process; the ingest worker (Phase 2) recovers owed work at
        # start and drains at stop. One lifespan wraps both.
        lifespan=_lifespan(None if mcp_app is None else mcp_app.lifespan),
    )
    app.include_router(router)
    # Replaces FastAPI's default 422 handler; see _validation_error.
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Forbidden, _forbidden)
    app.add_exception_handler(CSRFFailed, _csrf_failed)
    _mount_auth(app)

    if mcp_app is not None:
        from llmwiki.mcp.server import mount

        mount(app, _protect_mcp(mcp_app))

    _mount_channels(app)
    return app


def _lifespan(inner):  # type: ignore[no-untyped-def]
    """Start/stop the ingest worker around the (optional) MCP lifespan."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        from llmwiki import tools

        try:
            recovered = tools.recover_pending()
            if recovered:
                logger.info("ingest worker: recovered %d pending source(s)", len(recovered))
        except Exception as exc:  # pragma: no cover - storage trouble must not stop the app
            logger.warning("ingest worker: recovery skipped (%s: %s)", type(exc).__name__, exc)
        try:
            if inner is None:
                yield
            else:
                async with inner(app):
                    yield
        finally:
            tools.shutdown_worker()

    return lifespan


def _mount_auth(app: FastAPI) -> None:
    """AUTH_MODE=sso|both (docs/sso-integration-design.md B1/B4, v1.1): fail fast, then /login,
    the server-side session store (pruned now and hourly) and the session middleware.

    ``static`` (the default) mounts nothing - no /login, same responses as before (v1.1 item 5) -
    and only logs warnings: /mcp is open, and a weak INGEST_API_TOKEN.
    """
    from llmwiki import tools
    from llmwiki.config import settings as current_settings

    current_settings.require_auth_config()
    if current_settings.auth_mode == "static":
        if not current_settings.sso_protect_reads:
            logger.warning("MCP is unauthenticated in AUTH_MODE=static")
        weak = current_settings.static_token_weakness()
        if weak:
            logger.warning("INGEST_API_TOKEN %s; AUTH_MODE=sso|both would refuse to start", weak)
        return
    from llmwiki.api.login import router as login_router
    from llmwiki.api.session import SessionAuthMiddleware

    pruned = tools.auth_service(current_settings).init_sessions()
    app.include_router(login_router)
    app.add_middleware(SessionAuthMiddleware, cfg=current_settings)
    logger.info("auth: session store ready, %d expired session(s) pruned", pruned)
    logger.info("auth: AUTH_MODE=%s, issuer %s, permission %s, protect reads %s",
                current_settings.auth_mode, current_settings.sso_issuer,
                current_settings.sso_required_permission, current_settings.sso_protect_reads)


def _protect_mcp(mcp_app):  # type: ignore[no-untyped-def]
    """Wrap /mcp in the Bearer check in AUTH_MODE=sso|both (v1.1 item 1), and in static mode when
    SSO_PROTECT_READS=true (v1.2 C-12). Default static (reads open): unchanged, unwrapped."""
    from llmwiki.config import settings as current_settings

    if current_settings.auth_mode == "static" and not current_settings.sso_protect_reads:
        return mcp_app
    from llmwiki.api.session import MCPAuthMiddleware

    return MCPAuthMiddleware(mcp_app, current_settings)


async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
    """403 for a caller without SSO_REQUIRED_PERMISSION (design B2: never 401)."""
    return JSONResponse(status_code=403, content=Forbidden.BODY)


async def _csrf_failed(request: Request, exc: Exception) -> JSONResponse:
    """403 for a cookie-authenticated write without X-CSRF-Token (design v1.2 Q-30)."""
    return JSONResponse(status_code=403, content=CSRFFailed.BODY)


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


# D-19: before uvicorn wraps the app (Config.load imports it first); see api/proxyguard.py
disable_uvicorn_proxy_headers()
app = create_app()
