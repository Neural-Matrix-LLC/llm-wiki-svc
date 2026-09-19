"""MCP server over the same ``tools.py`` functions the REST API calls.

Only the canonical tools from design doc 4.6 are exposed - the original six
plus, since Phase 2 (design §4.10, plan §21.2 X3), the read-only
``list_domains``.  The helpers in ``tools.py`` (status, answer, cost, the
registry mutations) stay off the agent-facing surface on purpose: a small,
well-named tool set is easier for a model to use correctly than a large one.
"""

from __future__ import annotations

from typing import Any

from llmwiki import tools


def build_server() -> Any:
    """Construct the FastMCP server and register the seven tools."""
    from fastmcp import FastMCP

    mcp: Any = FastMCP("llmwiki")

    @mcp.tool()
    def search_wiki(query: str, k: int = 5, domain: str | None = None) -> list[dict]:
        """Search the knowledge base. Compiled wiki pages first, raw source chunks as fallback.

        `domain` restricts the search to one domain (see list_domains); leave
        it out to search according to the service's policy.
        """
        return [hit.model_dump(mode="json")
                for hit in tools.search_wiki(query, k=k, domain=domain)]

    @mcp.tool()
    def get_page(slug: str, domain: str | None = None) -> dict:
        """Fetch one wiki page in full by its slug.

        `domain` names which domain's wiki to read from (see list_domains);
        leave it out for the general wiki.
        """
        return tools.get_page(slug, domain=domain).model_dump(mode="json")

    @mcp.tool()
    def ingest_source(
        url: str | None = None, text: str | None = None, title: str = "",
        domain: str | None = None,
    ) -> dict:
        """Capture a source into the knowledge base, then compile it.

        Pass exactly one of: `url` - a blog post, a YouTube video, or a direct
        link to a PDF, fetched and stored as captured; or `text` - a block of
        text stored verbatim as its own source. Files are uploaded over the
        REST API's /upload instead. `domain` files the source under a
        registered domain (see list_domains); leave it out to let the service
        decide. Returns the source_id to poll or cite.
        """
        ref = tools.ingest_source(url=url, text=text, title=title, domain=domain)
        if not ref.duplicate:
            tools.process_source(ref.source_id)
        return ref.model_dump(mode="json")

    @mcp.tool()
    def compile_update(source_id: str, force: bool = False) -> dict:
        """Re-run the incremental compiler for one already-captured source."""
        return tools.compile_update(source_id, force=force).model_dump(mode="json")

    @mcp.tool()
    def list_concepts(prefix: str | None = None, domain: str | None = None) -> list[dict]:
        """List one domain's pages as one-line gists. Cheap: reads only that manifest.

        `domain` defaults to the general wiki; see list_domains for the others.
        """
        return [gist.model_dump(mode="json")
                for gist in tools.list_concepts(prefix, domain=domain)]

    @mcp.tool()
    def list_domains() -> list[dict]:
        """List the knowledge base's domains - name and one-line description, general first.

        Call this before passing `domain` to search_wiki, get_page,
        list_concepts or ingest_source. Read-only; domains are created by an
        administrator, never by this tool.
        """
        return [domain.model_dump(mode="json") for domain in tools.list_domains()]

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
