"""REG-*: AUTH_MODE=static behaves exactly as before (design B3/B8 + v1.1 item 5: byte-for-byte, same 401 bodies
AND headers, no /login or /logout (404), SSO_PROTECT_READS still applies with the static token; v1.1 item 1: MCP
still open but a startup WARNING). Expected values come from the snapshot taken from the code at bca2bef
(snapshots/static_routes_bca2bef.json, tools/snapshot_routes.py)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

import matrix as M
from conftest import HERE, instance_or_skip

SNAP = json.loads((HERE / "snapshots" / "static_routes_bca2bef.json").read_text())
NEW_ROUTES_ALLOWED: set = set()      # v1.1 item 5: no /login, /logout in static mode
# A FastAPI HTTPException(401) at bca2bef carries exactly these headers under uvicorn (no WWW-Authenticate,
# no Set-Cookie, no Cache-Control). Values of date/server/content-length are not compared.
BASELINE_401_HEADERS = {"date", "server", "content-length", "content-type"}


def assert_baseline_401(r, body):
    assert r.status_code == 401, r.status_code
    assert r.json() == body
    assert r.content == json.dumps(body, separators=(",", ":")).encode(), r.content
    assert set(r.headers.keys()) == BASELINE_401_HEADERS, f"401 headers changed: {sorted(r.headers.keys())}"
    assert r.headers["content-type"] == "application/json"


@pytest.fixture(scope="module")
def static(cfg):
    return instance_or_skip(cfg, "static")


def _c(inst):
    return httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False)


@pytest.mark.needs_wiki
def test_REG_01_route_inventory_unchanged(static):
    with _c(static) as c:
        spec = c.get("/openapi.json").json()
    live = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    snap = {(r["method"], r["path"]) for r in SNAP["routes"]}
    assert snap <= live, f"routes disappeared: {snap - live}"
    assert live - snap <= NEW_ROUTES_ALLOWED, f"unexpected new routes in static mode: {live - snap - NEW_ROUTES_ALLOWED}"


@pytest.mark.needs_wiki
@pytest.mark.parametrize("probe", M.PROTECTED, ids=lambda p: p.id)
@pytest.mark.parametrize("auth", [None, "Bearer wrong-token", "Basic Zm9vOmJhcg==", "Bearer a.b.c"])
def test_REG_02_protected_unauthenticated_401_exact_body(static, probe, auth):
    with _c(static) as c:
        kw = probe.kw()
        kw["headers"] = {**kw.get("headers", {}), **({"Authorization": auth} if auth else {})}
        r = c.request(probe.method, probe.path, **kw)
    assert_baseline_401(r, {"detail": "invalid or missing bearer token"})


@pytest.mark.needs_wiki
@pytest.mark.parametrize("probe", [p for p in M.PROTECTED if not p.deny_only], ids=lambda p: p.id)
def test_REG_03_protected_static_token_allowed_auth_before_validation(static, static_token, probe):
    with _c(static) as c:
        kw = probe.kw()
        kw["headers"] = {**kw.get("headers", {}), "Authorization": f"Bearer {static_token}"}
        r = c.request(probe.method, probe.path, **kw)
    assert r.status_code == probe.allowed_status, (r.status_code, r.text[:200])


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_REG_04_sso_token_is_just_a_wrong_static_token(static, tokens):
    with _c(static) as c:
        r = c.get("/usage", headers={"Authorization": f"Bearer {tokens('qa_wiki_super_app1')['access_token']}"})
    assert_baseline_401(r, {"detail": "invalid or missing bearer token"})


@pytest.mark.needs_wiki
def test_REG_05_dashboard(static, static_token):
    with _c(static) as c:
        none = c.get("/dashboard")
        assert_baseline_401(none, {"detail": "invalid or missing token"})
        assert c.get("/dashboard", params={"token": "wrong"}).status_code == 401
        q = c.get("/dashboard", params={"token": static_token})
        assert q.status_code == 200 and q.headers["content-type"].startswith("text/html")
        h = c.get("/dashboard", headers={"Authorization": f"Bearer {static_token}"})
        assert h.status_code == 200
        assert "<h1>llmwiki usage" in q.text
        assert c.get("/dashboard", params={"token": static_token, "month": "bad"}).status_code == 422


@pytest.mark.needs_wiki
@pytest.mark.parametrize("probe", M.READ + M.HEALTH, ids=lambda p: p.id)
@pytest.mark.parametrize("auth", [None, "Bearer garbage", "Bearer a.b.c"])
def test_REG_06_reads_open_and_ignore_credentials(static, probe, auth):
    with _c(static) as c:
        r = c.request(probe.method, probe.path, headers={"Authorization": auth} if auth else {})
    if probe.allowed_status:
        assert r.status_code == probe.allowed_status, (probe.id, r.status_code)
    else:
        assert r.status_code not in (401, 403, 303)


@pytest.mark.needs_wiki
def test_REG_07_healthz_shape(static):
    with _c(static) as c:
        body = c.get("/healthz").json()
    assert body["status"] == "ok"
    assert {"version", "backends", "lexical", "budget"} <= set(body)
    assert "auth_mode" not in body or body["auth_mode"] == "static"


@pytest.mark.needs_wiki
def test_REG_08_no_session_machinery_in_static_mode(static, static_token):
    with _c(static) as c:
        for path in ("/healthz", "/domains", "/usage"):
            r = c.get(path, headers={"Authorization": f"Bearer {static_token}"})
            assert not r.headers.get_list("set-cookie"), (path, r.headers.get_list("set-cookie"))


@pytest.mark.needs_wiki
def test_REG_09_mcp_open_as_today(static):
    with _c(static) as c:
        r = c.post("/mcp/", json=M.MCP_INIT, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200


@pytest.mark.needs_wiki
@pytest.mark.parametrize("method,path", [("GET", "/login"), ("POST", "/login"), ("POST", "/logout"),
                                         ("GET", "/logout"), ("GET", "/login?next=/dashboard")])
def test_REG_12_no_login_logout_routes(static, method, path):
    """v1.1 item 5 (Q-01): no /login, /logout in static mode -> FastAPI's plain 404."""
    with _c(static) as c:
        r = c.request(method, path, data={"username": "x", "password": "y"} if method == "POST" else None)
    assert r.status_code == 404, (method, path, r.status_code)
    assert r.json() == {"detail": "Not Found"}
    assert not r.headers.get_list("set-cookie")


@pytest.fixture(scope="module")
def static_reads(cfg):
    return instance_or_skip(cfg, "static_reads")


@pytest.mark.needs_wiki
@pytest.mark.parametrize("probe", M.READ, ids=lambda p: p.id)
def test_REG_13_static_protect_reads_requires_static_token(static_reads, static_token, probe):
    """v1.1 item 5 (Q-03): SSO_PROTECT_READS still applies in static mode and requires the static token."""
    with _c(static_reads) as c:
        none = c.request(probe.method, probe.path)
        assert_baseline_401(none, {"detail": "invalid or missing bearer token"})
        wrong = c.request(probe.method, probe.path, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401
        ok = c.request(probe.method, probe.path, headers={"Authorization": f"Bearer {static_token}"})
    if probe.allowed_status:
        assert ok.status_code == probe.allowed_status, (probe.id, ok.status_code)
    else:
        assert ok.status_code not in (401, 403, 303)


@pytest.mark.needs_wiki
def test_REG_14_static_protect_reads_healthz_open_mcp_needs_static_token(static_reads, static_token):
    """v1.2 C-12: static + SSO_PROTECT_READS=true also requires the static token on /mcp (Phase 0 401 body)."""
    hdr = {"Accept": "application/json, text/event-stream"}
    with _c(static_reads) as c:
        assert c.get("/healthz").status_code == 200
        none = c.post("/mcp/", json=M.MCP_INIT, headers=hdr)
        wrong = c.post("/mcp/", json=M.MCP_INIT, headers=hdr | {"Authorization": "Bearer wrong"})
        ok = c.post("/mcp/", json=M.MCP_INIT, headers=hdr | {"Authorization": f"Bearer {static_token}"})
    assert none.status_code == 401 and wrong.status_code == 401, (none.status_code, wrong.status_code)
    assert none.json() == {"detail": "invalid or missing bearer token"}, none.text
    assert "www-authenticate" not in none.headers, "static mode: no Bearer challenge (Phase 0 401)"
    assert ok.status_code == 200, (ok.status_code, ok.text[:200])


@pytest.mark.needs_wiki
def test_REG_14b_static_protect_reads_mcp_401_bytes_match_route_401(static_reads):
    """C-12 says 'with the Phase 0 401 body'. Compare the /mcp 401 with a static route 401 byte for byte."""
    with _c(static_reads) as c:
        mcp = c.post("/mcp/", json=M.MCP_INIT, headers={"Accept": "application/json, text/event-stream"})
        route = c.post("/ingest", json={"text": "x"})
    assert mcp.status_code == route.status_code == 401
    assert mcp.content == route.content, (mcp.content, route.content)
    assert mcp.headers.get("content-type") == route.headers.get("content-type")


@pytest.mark.needs_wiki
def test_REG_15_static_mcp_startup_warning(cfg, static):
    """v1.1 item 1: WARNING 'MCP is unauthenticated in AUTH_MODE=static' in the static instance's log."""
    logs = [f for f in (cfg.log_files or []) if "wiki-static" in f]
    if not logs:
        pytest.skip("LLM_WIKI_LOG_FILES has no wiki-static log (START-07 covers this with a throw-away process)")
    text = "".join(Path(f).read_text(errors="replace") for f in logs)
    assert "MCP is unauthenticated in AUTH_MODE=static" in text


REG10_JUSTIFIED: dict = {}     # path -> reason given by the developer in the report (none so far)


@pytest.mark.static_check
def test_REG_10_existing_tests_unchanged(cfg):
    """B8 + v1.1 item 5: existing llm-wiki tests pass *unchanged* ('revert edits to existing tests, e.g.
    tests/unit/test_layering.py, unless a test is genuinely wrong - then explain it'): no existing test file
    modified or deleted vs main. A justified exception goes into REG10_JUSTIFIED with the developer's reason."""
    repo = Path(cfg.wiki_repo)
    base = subprocess.run(["git", "-C", str(repo), "merge-base", "main", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    # against the merge base, INCLUDING uncommitted work-tree changes (the developer builds in place)
    p = subprocess.run(["git", "-C", str(repo), "diff", "--name-status", base, "--",
                        "tests/unit", "tests/integration", "tests/conftest.py", "tests/doubles.py", "tests/factories.py"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    changed = [line for line in p.stdout.splitlines() if line and line[0] in "MDR"]
    changed = [line for line in changed if line.split("\t")[-1] not in REG10_JUSTIFIED]
    assert not changed, changed


@pytest.mark.static_check
def test_REG_11_existing_route_auth_unchanged_in_code(cfg):
    """Every route protected at bca2bef is still protected (require_token or its require_auth alias) and every
    open route is still open by default; new routes may be added."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("snapshot_routes", HERE / "tools" / "snapshot_routes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    now = {(r["method"], r["path"]): r["auth"] for r in mod.snapshot()["routes"]}
    for r in SNAP["routes"]:
        key = (r["method"], r["path"])
        assert key in now, f"route removed: {key}"
        if r["auth"] == "require_token":
            assert now[key] == "require_token", (key, now[key])
        if r["auth"] == "none":
            assert now[key] in ("none", "require_reads"), (key, now[key])
