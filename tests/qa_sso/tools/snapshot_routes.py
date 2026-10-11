"""Static snapshot of llm-wiki-svc's HTTP routes and their auth, read from the source with ``ast``.

Nothing is imported or executed from the app, so this works without the app's dependencies and
without a running server. Used to freeze "AUTH_MODE=static behaves exactly as today" (design B3/B8):

    python tests/qa_sso/tools/snapshot_routes.py --ref <sha> > tests/qa_sso/snapshots/static_routes_<sha>.json
    python tests/qa_sso/tools/snapshot_routes.py            # the working tree (what REG-11 compares)

Output: one entry per route: method, path, handler, auth ("require_token" | "dashboard_token" | "none"),
plus the mounted sub-apps (/mcp) and the channel routers.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _source(path: Path, ref: str | None) -> str:
    if ref is None:
        return path.read_text()
    rel = path.relative_to(REPO).as_posix()
    return subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{rel}"], capture_output=True, text=True,
                          check=True).stdout


def _routes(path: Path, router_name: str = "router", ref: str | None = None) -> list[dict]:
    tree = ast.parse(_source(path, ref))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == router_name):
                continue
            method = dec.func.attr.upper()
            route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            deps = []
            for kw in dec.keywords:
                if kw.arg == "dependencies":
                    deps = ([ast.unparse(d) for d in kw.value.elts] if hasattr(kw.value, "elts")
                            else [ast.unparse(kw.value)])
            src = ast.unparse(node)
            if any("require_token" in d or "require_auth" in d for d in deps):
                auth = "require_token"          # require_auth is the design-B3 alias of require_token
            elif any("read" in d.lower() for d in deps):
                auth = "require_reads"          # SSO_PROTECT_READS dependency (design B3)
            elif "compare_digest" in src and "token" in src:
                auth = "dashboard_token"  # Bearer header or ?token=, inline check
            else:
                auth = "none"
            out.append({"method": method, "path": route, "handler": node.name, "auth": auth,
                        "file": str(path.relative_to(REPO)), "line": node.lineno})
    return sorted(out, key=lambda r: (r["path"], r["method"]))


def snapshot(ref: str | None = None) -> dict:
    """ref=None reads the working tree; ref='bca2bef' reads that commit via git show."""
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", ref or "HEAD"],
                         capture_output=True, text=True).stdout.strip() + ("" if ref else " (+working tree)")
    api = _routes(REPO / "src/llmwiki/api/routes.py", ref=ref)
    channels = []
    for name in ("telegram", "email"):
        channels += _routes(REPO / f"src/llmwiki/channels/{name}.py", ref=ref)
    return {
        "commit": sha,
        "source": "ast of src/llmwiki/api/routes.py + channels/*.py (tests/qa_sso/tools/snapshot_routes.py)",
        "routes": api,
        "channel_routes_mounted_only_when_configured": [
            {k: r[k] for k in ("method", "path", "handler", "file", "line")} for r in channels],
        "mounts": [{"path": "/mcp", "app": "fastmcp streamable HTTP (src/llmwiki/mcp/server.py)",
                    "auth": "none (no app-level auth; deployment relies on Cloudflare Access)"}],
        "static_mode_behaviour": {
            "require_token": {"missing_or_wrong": [401, {"detail": "invalid or missing bearer token"}],
                              "www_authenticate_header": False,
                              "order": "auth dependency runs before body/query validation: "
                                       "unauthenticated+invalid body -> 401, authenticated+invalid -> 422"},
            "dashboard_token": {"missing_or_wrong": [401, {"detail": "invalid or missing token"}],
                                "accepts": ["Authorization: Bearer <INGEST_API_TOKEN>", "?token=<INGEST_API_TOKEN>"],
                                "ok": "200 text/html"},
            "none": "open to everyone, credentials ignored",
            "mcp": "open: initialize/tools/list without any token succeed",
        },
    }


if __name__ == "__main__":
    ref = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else None
    json.dump(snapshot(ref), sys.stdout, indent=1)
    sys.stdout.write("\n")
