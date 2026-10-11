"""Guards the layer boundaries in the plan (4.2).

The design's claim that FastAPI and MCP are cheap glue holds only while business
logic stays out of them. This test walks the AST of every module and fails on a
forbidden import edge, so the boundary is mechanical rather than aspirational.

Permanent test: do not weaken it to make a new import pass. Move the code instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "llmwiki"

# layer -> the llmwiki subpackages it must never import
FORBIDDEN: dict[str, set[str]] = {
    "models": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
               "pipeline", "tools", "api", "mcp", "cli", "channels", "factory", "config",
               "chains", "websearch", "eval", "lexical", "rerank", "notify"},
    "storage": {"extractors", "embedding", "vector", "llm", "wiki", "agent", "pipeline",
                "tools", "api", "mcp", "cli", "channels", "factory", "websearch", "eval",
                "lexical", "rerank", "notify"},
    "extractors": {"storage", "embedding", "vector", "llm", "wiki", "agent", "pipeline",
                   "tools", "api", "mcp", "cli", "channels", "factory", "websearch", "eval",
                   "lexical", "rerank", "notify"},
    "embedding": {"storage", "extractors", "vector", "llm", "wiki", "agent", "pipeline",
                  "tools", "api", "mcp", "cli", "channels", "factory", "websearch", "eval",
                  "lexical", "rerank", "notify"},
    "vector": {"storage", "extractors", "embedding", "llm", "wiki", "agent", "pipeline",
               "tools", "api", "mcp", "cli", "channels", "factory", "websearch", "eval",
               "lexical", "rerank", "notify"},
    "llm": {"storage", "extractors", "embedding", "vector", "wiki", "agent", "pipeline",
            "tools", "api", "mcp", "cli", "channels", "factory", "websearch", "eval",
            "lexical", "rerank", "notify"},
    # L1 peer of vector/embedding (Phase 1-D, design §4.9): the query graph's
    # optional web-search backend. Same banned set as the other primitives.
    "websearch": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
                  "pipeline", "tools", "api", "mcp", "cli", "channels", "factory", "eval",
                  "lexical", "rerank", "notify"},
    # Phase 2 (design §4.10, plan §21.5): three more L1 primitives - the lexical
    # index, the reranker and the alert notifier. Same posture as websearch:
    # protocols plus adapters, importing nothing above L0. Rows are inert until
    # the packages exist (``_real_layers`` only counts directories on disk).
    "lexical": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
                "pipeline", "tools", "api", "mcp", "cli", "channels", "factory", "eval",
                "websearch", "rerank", "notify"},
    "rerank": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
               "pipeline", "tools", "api", "mcp", "cli", "channels", "factory", "eval",
               "websearch", "lexical", "notify"},
    "notify": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
               "pipeline", "tools", "api", "mcp", "cli", "channels", "factory", "eval",
               "websearch", "lexical", "rerank"},
    "wiki": {"api", "mcp", "cli", "channels", "pipeline", "agent", "tools", "factory", "eval"},
    "agent": {"api", "mcp", "cli", "channels", "pipeline", "tools", "factory", "eval"},
    "pipeline": {"api", "mcp", "cli", "channels", "tools", "factory", "eval"},
    "tools": {"api", "mcp", "cli", "channels", "eval"},
    "api": {"storage", "extractors", "embedding", "vector", "llm", "pipeline", "factory",
            "websearch", "eval", "lexical", "rerank", "notify"},
    "mcp": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
            "pipeline", "factory", "websearch", "eval", "lexical", "rerank", "notify"},
    "cli": {"storage", "extractors", "embedding", "vector", "llm", "pipeline", "websearch",
            "eval"},
    # New L4 transport layer (webhook capture channels: Telegram, email - Phase
    # 1, KB design §5). Same posture as mcp: reaches tools/models/config, never
    # the L1 primitives or pipeline.
    "channels": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
                 "pipeline", "factory", "websearch", "eval", "lexical", "rerank", "notify"},
    # L4 peer of cli (Phase 1-D, design §4.9): the LangSmith eval/feedback
    # helpers. Reaches the domain through tools.py only, exactly like a
    # transport; langsmith itself is imported function-locally.
    "eval": {"storage", "extractors", "embedding", "vector", "llm", "wiki", "agent",
             "pipeline", "chains", "api", "mcp", "cli", "channels", "factory", "websearch",
             "lexical", "rerank", "notify"},
}

# api/ and cli.py legitimately render a page; that is serialization, not logic.
ALLOWED_EXCEPTIONS = {
    ("api", "wiki"),
    ("cli", "wiki"),
    ("cli", "factory"),
}


def _modules() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if path.name != "__init__.py")


def _layer_of(path: Path) -> str:
    relative = path.relative_to(SRC)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def _real_layers() -> set[str]:
    """Layer names that actually exist, so ``from llmwiki import __version__`` is not a layer."""
    names = {path.name for path in SRC.iterdir() if path.is_dir() and not path.name.startswith("_")}
    names |= {path.stem for path in SRC.glob("*.py") if path.name != "__init__.py"}
    return names


def _imported_layers(path: Path) -> set[str]:
    """Every ``llmwiki.<layer>`` this module imports, including function-local imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    known = _real_layers()
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "llmwiki":
                if len(parts) > 1:
                    layers.add(parts[1])
                else:
                    layers.update(alias.name for alias in node.names if alias.name in known)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "llmwiki" and len(parts) > 1:
                    layers.add(parts[1])
    return layers & known


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_layer_does_not_import_upward(path: Path) -> None:
    layer = _layer_of(path)
    banned = FORBIDDEN.get(layer, set())
    if not banned:
        return
    violations = {
        imported
        for imported in _imported_layers(path) & banned
        if (layer, imported) not in ALLOWED_EXCEPTIONS
    }
    assert not violations, (
        f"{path.relative_to(SRC)} is in layer '{layer}' and imports {sorted(violations)}; "
        "dependencies must point inward and downward only (plan 4.2)"
    )


def test_transport_layer_only_calls_tools() -> None:
    """api/, mcp/, cli.py and channels/ reach the domain through tools.py, not around it.

    Transport modules may import one another - mounting MCP (and the Telegram/
    email channels) inside the FastAPI app in one process is decision D6, not
    a layering violation.
    """
    allowed = {"tools", "models", "config", "wiki", "factory", "api", "mcp", "cli", "channels"}
    for path in _modules():
        if _layer_of(path) not in {"api", "mcp", "cli", "channels"}:
            continue
        imported = _imported_layers(path)
        assert imported <= allowed, (
            f"{path.relative_to(SRC)} imports {sorted(imported - allowed)}; transport layers "
            "must validate input, call tools.py, and serialize the result"
        )


def test_no_module_level_io() -> None:
    """Importing any llmwiki module must be free of side effects (plan 4.4)."""
    banned_calls = {"open", "read_text", "read_bytes", "get", "post", "client"}
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                    if name in banned_calls:
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno} calls {name}()")
    assert not offenders, "module-level I/O found: " + "; ".join(offenders)
