"""MCP-*: MCP clients and scripts with the static token / SSO token (design B5 + v1.1 item 1: /mcp is wrapped in
an auth middleware with require_auth's rules in sso/both; unchanged/open in static, D-01 resolved). Matrix covers
the grid; these are end-to-end (initialize -> tools/list -> tools/call list_domains)."""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import instance_or_skip

pytestmark = [pytest.mark.needs_wiki]
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc_payload(r: httpx.Response) -> dict:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(r.text[:500])
    return r.json()


def mcp_session(url: str, auth: dict) -> tuple[int, list | None]:
    """-> (status of initialize, tool names or None)."""
    with httpx.Client(base_url=url, timeout=30, follow_redirects=False) as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "qa-sso", "version": "0"}}}
        r = c.post("/mcp/", json=init, headers=HDR | auth)
        if r.status_code != 200:
            return r.status_code, None
        sid = r.headers.get("mcp-session-id")
        h = HDR | auth | ({"mcp-session-id": sid} if sid else {})
        c.post("/mcp/", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=h)
        assert r.status_code == 200, (r.status_code, r.text[:300])
        tools = [t["name"] for t in _rpc_payload(r)["result"]["tools"]]
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "list_domains", "arguments": {}}}, headers=h)
        assert r.status_code == 200 and "error" not in _rpc_payload(r), r.text[:300]
        return 200, tools


def test_MCP_01_static_mode_unchanged_open(cfg):
    """Snapshot: /mcp has no app-level auth today; AUTH_MODE=static keeps it so (v1.1 item 1, startup WARNING:
    START-07 / REG-15)."""
    inst = instance_or_skip(cfg, "static")
    status, tools = mcp_session(inst.url, {})
    assert status == 200 and "search_wiki" in tools and "ingest_source" in tools


def test_MCP_02_both_mode_static_token_works(cfg, static_token):
    inst = instance_or_skip(cfg, "both")
    status, tools = mcp_session(inst.url, {"Authorization": f"Bearer {static_token}"})
    assert status == 200 and tools


@pytest.mark.needs_sso
@pytest.mark.parametrize("inst_name", ["both", "sso"])
def test_MCP_03_sso_token_with_perm_works(cfg, tokens, inst_name):
    inst = instance_or_skip(cfg, inst_name)
    status, _ = mcp_session(inst.url, {"Authorization": f"Bearer {tokens('qa_wiki_direct')['access_token']}"})
    assert status == 200


@pytest.mark.parametrize("auth", ["none", "static", "static_wrong", "garbage_jwt"])
def test_MCP_04_sso_mode_requires_sso_token(cfg, auth, request):
    """v1.1 item 1: in sso mode SSO token + permission; the static token is not accepted."""
    inst = instance_or_skip(cfg, "sso")
    tok = {"none": None, "static": request.getfixturevalue("static_token") if auth.startswith("static") else None,
           "garbage_jwt": "eyJhbGciOiJSUzI1NiJ9.eyJ4IjoxfQ.c2ln"}.get(auth)
    if auth == "static_wrong":
        tok = request.getfixturevalue("static_token") + "-wrong"
    hdr = {} if tok is None else {"Authorization": f"Bearer {tok}"}
    status, _ = mcp_session(inst.url, hdr)
    assert status == 401, f"/mcp answered {status} without an SSO token in AUTH_MODE=sso (write tools exposed)"


@pytest.mark.parametrize("auth", ["none", "static_wrong", "garbage_jwt"])
def test_MCP_04b_both_mode_rejects_missing_or_bad(cfg, auth, request):
    inst = instance_or_skip(cfg, "both")
    tok = {"none": None, "garbage_jwt": "eyJhbGciOiJSUzI1NiJ9.eyJ4IjoxfQ.c2ln",
           "static_wrong": request.getfixturevalue("static_token") + "-wrong" if auth == "static_wrong" else None}[auth]
    status, _ = mcp_session(inst.url, {} if tok is None else {"Authorization": f"Bearer {tok}"})
    assert status == 401


def test_MCP_04c_401_has_bearer_challenge(cfg):
    inst = instance_or_skip(cfg, "sso")
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=HDR)
    assert r.status_code == 401 and "bearer" in r.headers.get("www-authenticate", "").lower()


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/messages", "/mcp/sse", "/MCP/", "/mcp//"])
def test_MCP_04d_no_unprotected_mcp_subpath(cfg, path):
    """The middleware wraps the whole mount: no sub-path or redirect variant reaches MCP without auth."""
    inst = instance_or_skip(cfg, "sso")
    with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
        for method in ("GET", "POST"):
            r = c.request(method, path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"} if method == "POST" else None,
                          headers=HDR)
            assert r.status_code in (401, 404, 405, 307), (method, path, r.status_code)
            if r.status_code == 307:
                assert r.headers.get("location", "").rstrip("/").endswith("/mcp"), r.headers.get("location")


@pytest.mark.needs_sso
@pytest.mark.parametrize("inst_name", ["sso", "both"])
def test_MCP_05_token_without_perm_403(cfg, tokens, inst_name):
    inst = instance_or_skip(cfg, inst_name)
    status, _ = mcp_session(inst.url, {"Authorization": f"Bearer {tokens('qa_wiki_none')['access_token']}"})
    assert status == 403


@pytest.mark.parametrize("inst_name", ["static", "both"])
def test_MCP_06_scripts_static_token_on_rest(cfg, static_token, inst_name):
    """scripts/*.py and iOS shortcuts send Authorization: Bearer INGEST_API_TOKEN to REST (e.g. /ingest)."""
    inst = instance_or_skip(cfg, inst_name)
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        r = c.post("/ingest", json={}, headers={"Authorization": f"Bearer {static_token}"})
    assert r.status_code == 422, "authorised (body validation runs after auth)"


def test_MCP_07_sso_mode_static_token_rejected_on_rest(cfg, static_token):
    inst = instance_or_skip(cfg, "sso")
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        r = c.post("/ingest", json={}, headers={"Authorization": f"Bearer {static_token}"})
    assert r.status_code == 401


def test_MCP_08_channels_unaffected(cfg):
    """B5: Telegram/email webhooks keep their own secrets. When mounted, a request without the channel secret
    gets the channel's own rejection, never a redirect to /login or an SSO 401 challenge."""
    for inst in cfg.instances.values():
        with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
            for path in ("/channels/telegram/webhook", "/channels/email/inbound"):
                r = c.post(path, json={})
                assert r.status_code != 303 and "invalid_token" not in r.headers.get("www-authenticate", ""), (inst.name, path)


@pytest.mark.needs_sso
def test_MCP_09_mid_session_token_checked_per_request(cfg, tokens, forge):
    """Auth is per HTTP request, not only at initialize: a tools/call with the mcp-session-id but without (or
    with an expired) bearer is rejected."""
    inst = instance_or_skip(cfg, "sso")
    good = {"Authorization": f"Bearer {tokens('qa_wiki_direct')['access_token']}"}
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "qa", "version": "0"}}}
        r = c.post("/mcp/", json=init, headers=HDR | good)
        assert r.status_code == 200
        sid = r.headers.get("mcp-session-id")
        h = HDR | ({"mcp-session-id": sid} if sid else {})
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_domains", "arguments": {}}}
        assert c.post("/mcp/", json=call, headers=h).status_code == 401
        import time as _t
        expired = forge(tokens("qa_wiki_direct")["access_token"], exp=int(_t.time()) - 120)
        assert c.post("/mcp/", json=call, headers=h | {"Authorization": f"Bearer {expired}"}).status_code == 401


@pytest.mark.needs_sso
@pytest.mark.parametrize("inst_name", ["sso", "both"])
def test_MCP_10_session_cookie_not_accepted_on_mcp(cfg, browser_factory, seeded, tokens, inst_name):
    """Developer's choice beyond the design (v1.1 item 1 said 'require_auth's rules', which include the session):
    /mcp is Bearer-only. A valid session cookie alone -> 401 + Bearer challenge (never 200, never a 303 to /login);
    cookie + valid Bearer -> 200 (the cookie neither helps nor hurts)."""
    import qasso_data as D
    inst = instance_or_skip(cfg, inst_name)
    b = browser_factory(inst)
    b.login("qa_wiki_direct", D.PASSWORD)
    cookie = {"Cookie": f"llmwiki_session={b.session_value}"}
    st, _ = mcp_session(inst.url, cookie | {"X-CSRF-Token": b.session_csrf()})
    assert st == 401, st
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=HDR | cookie)
    assert r.status_code == 401 and r.headers.get("www-authenticate", "").startswith("Bearer"), (r.status_code, r.headers)
    assert r.json() == {"detail": "invalid or missing credentials"}, r.text
    st, _ = mcp_session(inst.url, cookie | {"Authorization": f"Bearer {tokens('qa_wiki_direct')['access_token']}"})
    assert st == 200, st
