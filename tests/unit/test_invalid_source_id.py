"""QA PRE-01 follow-up: a malformed source id is rejected cleanly by every transport.

HTTP: 404 ``not a valid source_id`` (test_source_status_route.py). MCP (``compile_update``, the
only MCP tool that takes a source id): a tool error. CLI (``source``, ``compile``): the message on
stderr, exit 1, no traceback.
"""

from __future__ import annotations

import pytest

from llmwiki import cli, tools
from llmwiki.mcp import server as mcp_server

BAD = "qa-no-such-source"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    # cli.main() writes os.environ; pre-set every key it touches so monkeypatch restores them
    for key, value in {**cli.OFFLINE_ENV, "COST_WRITER": "cli", "WORKER_MODE": "inline"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("LEXICAL_BACKEND", "memory")
    monkeypatch.setenv("RERANKER_BACKEND", "none")
    monkeypatch.chdir(tmp_path)  # no .env


def test_tools_raise_invalid_source_id_before_building_backends(monkeypatch) -> None:
    def no_backends(*_a, **_k):
        raise AssertionError("a backend was built for a malformed id")

    monkeypatch.setattr(tools, "_pipeline", no_backends)
    monkeypatch.setattr(tools, "_components", no_backends)
    for call in (tools.get_source_status, tools.compile_update):
        with pytest.raises(tools.InvalidSourceId) as info:
            call(BAD)
        assert str(info.value) == "not a valid source_id" and info.value.source_id == BAD
        assert isinstance(info.value, ValueError)  # what callers caught before still works


@pytest.mark.asyncio
async def test_mcp_compile_update_returns_a_tool_error() -> None:
    from fastmcp import Client

    async with Client(mcp_server.build_server()) as client:
        result = await client.call_tool("compile_update", {"source_id": BAD},
                                        raise_on_error=False)
    assert result.is_error
    text = " ".join(getattr(part, "text", "") for part in result.content)
    assert "not a valid source_id" in text and BAD in text
    assert "Traceback" not in text


@pytest.mark.parametrize("argv", [["source", BAD], ["compile", BAD], ["source", "x" * 300],
                                  ["compile", "--force", "0123456789ABCDEF"]])
def test_cli_prints_a_clear_message_and_exits_1(cli_env, capsys, argv) -> None:
    assert cli.main(["--offline", *argv]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip().splitlines()[-1] == f"not a valid source_id: {argv[-1]!r}"
    assert "Traceback" not in err


def test_cli_source_with_a_well_formed_id_still_works(cli_env, capsys) -> None:
    assert cli.main(["--offline", "source", "0123456789abcdef"]) == 0
    assert '"source_id": "0123456789abcdef"' in capsys.readouterr().out


# ---------------------------------------------------------------- QA PRE-03: well-formed, unknown
UNKNOWN = "00000000000000aa"


def test_compile_unknown_source_raises_source_not_found(cli_env) -> None:
    from llmwiki import factory
    from llmwiki.config import load_settings

    factory.reset()
    cfg = load_settings()
    with pytest.raises(tools.SourceNotFound) as info:
        tools.compile_update(UNKNOWN, cfg=cfg)
    assert str(info.value) == f"no such source: {UNKNOWN!r}" and info.value.source_id == UNKNOWN
    factory.reset()


def test_cli_compile_unknown_source_is_one_stderr_line(cli_env, capsys) -> None:
    assert cli.main(["--offline", "compile", UNKNOWN]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip().splitlines()[-1] == f"no such source: {UNKNOWN!r}"
    assert "Traceback" not in err


@pytest.mark.asyncio
async def test_mcp_compile_update_unknown_source_is_a_tool_error(monkeypatch) -> None:
    from fastmcp import Client

    def missing(source_id, force=False, cfg=None):
        raise tools.SourceNotFound(source_id)

    monkeypatch.setattr(tools, "compile_update", missing)
    async with Client(mcp_server.build_server()) as client:
        result = await client.call_tool("compile_update", {"source_id": UNKNOWN},
                                        raise_on_error=False)
    assert result.is_error
    text = " ".join(getattr(part, "text", "") for part in result.content)
    assert f"no such source: {UNKNOWN!r}" in text and "Traceback" not in text
