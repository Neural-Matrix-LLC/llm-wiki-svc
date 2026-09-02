"""The canonical tool surface, and that MCP exposes exactly the six named tools."""

from __future__ import annotations

import inspect

import pytest

from llmwiki import tools
from llmwiki.mcp import server as mcp_server

CANONICAL = {"search_wiki", "get_page", "ingest_source", "compile_update", "list_concepts",
             "lint_wiki"}
HELPERS = {"get_source_status", "answer", "source_exists", "cost_summary"}


def test_the_six_canonical_tools_exist_in_tools_py() -> None:
    for name in CANONICAL:
        assert callable(getattr(tools, name)), f"{name} is missing from the tool surface"


def test_the_helpers_exist_but_are_not_canonical() -> None:
    for name in HELPERS:
        assert callable(getattr(tools, name))
    assert not HELPERS & CANONICAL


@pytest.mark.asyncio
async def test_mcp_exposes_exactly_the_six_canonical_tools() -> None:
    server = mcp_server.build_server()
    registered = {tool.name for tool in await server.list_tools()}

    assert registered == CANONICAL, (
        f"MCP surface drifted: unexpected {sorted(registered - CANONICAL)}, "
        f"missing {sorted(CANONICAL - registered)}"
    )


@pytest.mark.asyncio
async def test_every_mcp_tool_has_a_description() -> None:
    """An undescribed tool is one a model will use wrongly."""
    server = mcp_server.build_server()
    for tool in await server.list_tools():
        assert tool.description and tool.description.strip(), f"{tool.name} has no description"


def test_mcp_tools_delegate_to_tools_py(monkeypatch) -> None:
    """Parity: the MCP layer must call tools.py, not reimplement anything."""
    source = inspect.getsource(mcp_server)
    for name in CANONICAL:
        assert f"tools.{name}(" in source, f"MCP tool {name} does not call tools.{name}"


def test_transport_layers_share_one_implementation() -> None:
    """REST and MCP must reach the same function object, or they will drift."""
    from llmwiki.api import routes

    assert routes.tools is tools
    assert mcp_server.tools is tools


def test_mcp_is_reachable_at_slash_mcp(tmp_path, monkeypatch) -> None:
    """The mounted MCP app must answer at /mcp/, not /mcp/mcp/.

    FastMCP's ``http_app()`` serves itself at ``/mcp`` by default, so mounting it
    at ``/mcp`` without rooting it at ``/`` buries the endpoint one level deeper
    and every MCP client 404s.
    """
    from fastapi.testclient import TestClient

    from llmwiki import config, factory
    from llmwiki.api.app import create_app
    from llmwiki.config import Settings

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path, embedding_dim=64)
    monkeypatch.setattr(config, "settings", cfg)
    factory.reset()

    with TestClient(create_app()) as client:
        response = client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            },
        )

    assert response.status_code == 200, response.text
    assert "serverInfo" in response.text
    factory.reset()
