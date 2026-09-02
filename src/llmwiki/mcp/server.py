"""MCP server over the same ``tools.py`` functions the REST API calls.

Only the six canonical tools from design doc 4.6 are exposed.  The helpers in
``tools.py`` (status, answer, cost) stay off the agent-facing surface on purpose:
a small, well-named tool set is easier for a model to use correctly than a large
one.
"""

from __future__ import annotations

from typing import Any

from llmwiki import tools


def build_server() -> Any:
    """Construct the FastMCP server and register the six tools."""
    from fastmcp import FastMCP

    mcp: Any = FastMCP("llmwiki")

    @mcp.tool()
    def search_wiki(query: str, k: int = 5) -> list[dict]:
        """Search the knowledge base. Compiled wiki pages first, raw source chunks as fallback."""
        return [hit.model_dump(mode="json") for hit in tools.search_wiki(query, k=k)]

    @mcp.tool()
    def get_page(slug: str) -> dict:
        """Fetch one wiki page in full by its slug."""
        return tools.get_page(slug).model_dump(mode="json")

    @mcp.tool()
    def ingest_source(url: str, title: str = "") -> dict:
        """Capture a URL into the knowledge base. Compilation continues in the background."""
        ref = tools.ingest_source(url=url, title=title)
        if not ref.duplicate:
            tools.process_source(ref.source_id)
        return ref.model_dump(mode="json")

    @mcp.tool()
    def compile_update(source_id: str, force: bool = False) -> dict:
        """Re-run the incremental compiler for one already-captured source."""
        return tools.compile_update(source_id, force=force).model_dump(mode="json")

    @mcp.tool()
    def list_concepts(prefix: str | None = None) -> list[dict]:
        """List the wiki's pages as one-line gists. Cheap: reads only the manifest."""
        return [gist.model_dump(mode="json") for gist in tools.list_concepts(prefix)]

    @mcp.tool()
    def lint_wiki(dry_run: bool = True) -> dict:
        """Check the wiki for orphans, dangling links and missing gists."""
        return tools.lint_wiki(dry_run=dry_run).model_dump(mode="json")

    return mcp


def build_http_app() -> Any:
    """Build the MCP ASGI app, rooted at ``/`` so mounting decides the final path.

    Without ``path="/"`` the app serves itself at ``/mcp``, and mounting that at
    ``/mcp`` puts the endpoint at ``/mcp/mcp``.
    """
    return build_server().http_app(path="/")


def mount(app: Any, mcp_app: Any, path: str = "/mcp") -> None:
    """Mount an MCP ASGI app inside an existing FastAPI app."""
    app.mount(path, mcp_app)


def main() -> None:
    """Run MCP standalone over stdio, for clients that prefer a subprocess."""
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
