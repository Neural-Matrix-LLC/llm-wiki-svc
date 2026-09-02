"""FastAPI application, with the MCP server mounted in the same process.

Design doc 4.6: one process serves REST and MCP over the identical ``tools.py``
functions, so the two surfaces cannot drift apart.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from llmwiki import __version__
from llmwiki.api.routes import router

logger = logging.getLogger(__name__)


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

    if mcp_app is not None:
        from llmwiki.mcp.server import mount

        mount(app, mcp_app)
    return app


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
