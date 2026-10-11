"""Regression round @ 6520890 (fixes after QA round 1). New IDs:

NS-*   Cache-Control: no-store on /login, /logout, /dashboard in sso/both (Q-20), absent in static (byte-identical)
RED-*  token= redaction in the uvicorn access log (PRE-02), other sinks and error paths
ROT-*  session rotation on a successful re-login (a13bc48): new id/cookie/CSRF, old cookie dead, old refresh token
       logged out at the SSO, no leftover row; best effort when the SSO logout fails
SID-*  malformed source ids (PRE-01, InvalidSourceId): HTTP 404, MCP tool error, CLI exit 1 without a traceback
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

import qasso_data as D
from conftest import instance_name_of, instance_or_skip
from wiki_client import SESSION_COOKIE

NO_STORE = "no-store"
BAD_IDS = ["qa-no-such-source", "..%2F..%2Fetc%2Fpasswd", "0000000000000000-UPPER", "x" * 200, "%00", "'or'1'='1"]


def _c(inst, **kw):
    return httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False, **kw)


def _cc(r):
    return r.headers.get_list("cache-control")


# ================================================================ NS: Cache-Control no-store (Q-20)
@pytest.mark.needs_wiki
@pytest.mark.needs_sso
@pytest.mark.parametrize("inst_name", ["sso", "both"])
def test_NS_01_no_store_on_every_login_logout_dashboard_response(cfg, browser_factory, seeded, inst_name):
    """Every response of /login, /logout, /dashboard in sso/both carries exactly one Cache-Control: no-store
    (form, failed login, success redirect, signed-in /login redirect, dashboard 200/303/401, logout)."""
    inst = instance_or_skip(cfg, inst_name)
    b = browser_factory(inst)
    seen = []
    r, _ = b.open_login(); seen.append(("GET /login", r))
    seen.append(("POST /login wrong", b.submit_login("qa_wiki_direct", D.WRONG_PASSWORD)))
    seen.append(("POST /login ok", b.submit_login("qa_wiki_direct", D.PASSWORD)))
    seen.append(("GET /login signed-in", b.get("/login")))
    seen.append(("GET /dashboard", b.get("/dashboard")))
    seen.append(("POST /logout", b.logout()))
    with _c(inst) as c:
        seen.append(("GET /dashboard anon", c.get("/dashboard", headers={"Accept": "text/html"})))
        seen.append(("GET /dashboard bad bearer", c.get("/dashboard", headers={"Authorization": "Bearer x.y.z"})))
        seen.append(("GET /logout", c.get("/logout")))
        seen.append(("GET /login?next=//evil", c.get("/login?next=//evil.example")))
    bad = [(n, r.status_code, _cc(r)) for n, r in seen if _cc(r) != [NO_STORE]]
    assert not bad, bad
    print("NS-01", inst_name, [(n, r.status_code) for n, r in seen])


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_NS_02_no_store_not_spread_to_api_routes(cfg, tokens):
    """Only the three pages: API and health routes keep their previous headers."""
    inst = instance_or_skip(cfg, "sso")
    with _c(inst) as c:
        for path in ("/healthz", "/usage", "/domains"):
            r = c.get(path, headers={"Authorization": f"Bearer {tokens('qa_wiki_direct')['access_token']}"})
            assert NO_STORE not in ",".join(_cc(r)), (path, _cc(r))


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "static_reads"])
def test_NS_03_static_mode_has_no_cache_control(cfg, static_token, inst_name):
    """Q-20 is sso/both only: static /dashboard (200 with ?token=, 401 without, 401 bad token) and /login (404)
    carry no Cache-Control at all - byte-identical to bca2bef (REG-* snapshot)."""
    inst = instance_or_skip(cfg, inst_name)
    with _c(inst) as c:
        rs = {"dash ok": c.get(f"/dashboard?token={static_token}"),
              "dash bearer": c.get("/dashboard", headers={"Authorization": f"Bearer {static_token}"}),
              "dash none": c.get("/dashboard"), "dash bad": c.get("/dashboard?token=wrong"),
              "login": c.get("/login"), "logout": c.post("/logout")}
    assert rs["dash ok"].status_code == 200 and rs["login"].status_code == 404, \
        {k: r.status_code for k, r in rs.items()}
    bad = {k: _cc(r) for k, r in rs.items() if "cache-control" in r.headers}
    assert not bad, bad


@pytest.mark.needs_wiki
@pytest.mark.parametrize("trail", ["/dashboard/", "/login/", "/Dashboard"])
def test_NS_04_path_variants(cfg, trail):
    """Path variants are not the pages themselves; they must not serve page content without no-store."""
    inst = instance_or_skip(cfg, "sso")
    with _c(inst) as c:
        r = c.get(trail, headers={"Accept": "text/html"})
    print("NS-04", trail, r.status_code, _cc(r), r.headers.get("location"))
    assert r.status_code in (301, 302, 303, 307, 308, 401, 404, 405) or _cc(r) == [NO_STORE], (r.status_code, _cc(r))
    if r.status_code == 200:
        assert _cc(r) == [NO_STORE]


# ================================================================ RED: token= redaction (PRE-02)
def _log_of(cfg, name):
    files = [f for f in cfg.log_files if f.endswith(f"wiki-{name}.log")]
    if not files:
        pytest.skip(f"no log file for wiki-{name} in LLM_WIKI_LOG_FILES")
    return Path(files[0])


def _new_text(path: Path, start: int) -> str:
    time.sleep(0.6)
    with open(path, "rb") as fh:
        fh.seek(start)
        return fh.read().decode(errors="replace")


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
def test_RED_01_access_log_redacts_token_params(cfg, inst_name):
    """Every spelling of a token query parameter reaching uvicorn's access log is redacted, success and error
    paths alike (200, 401, 404, 405, 422)."""
    inst = instance_or_skip(cfg, inst_name)
    log = _log_of(cfg, inst_name)
    secret = "qaRED" + uuid.uuid4().hex
    start = log.stat().st_size
    tok = cfg.static_token
    urls = [f"/dashboard?token={tok}", f"/dashboard?month=2026-10&token={tok}", f"/dashboard?TOKEN={secret}a",
            f"/healthz?access_token={secret}b", f"/healthz?x=1;token={secret}c", f"/dashboard?token={secret}d",
            f"/sources/qa-bad?token={secret}e", f"/search?token={secret}f", f"/nope?token={secret}g",
            f"/dashboard?token={secret}h&token={secret}i"]
    with _c(inst) as c:
        codes = [c.get(u).status_code for u in urls]
        codes.append(c.put(f"/healthz?token={secret}j").status_code)
    text = _new_text(log, start)
    assert "[redacted]" in text, text[-800:]
    leaks = [ln for ln in text.splitlines() if secret in ln or (tok and tok in ln)]
    assert not leaks, (codes, leaks[:5])


@pytest.mark.needs_wiki
def test_RED_02_percent_encoded_param_name_observed(cfg, static_token):
    """'?%74oken=' is decoded to 'token' by Starlette (so it authenticates) but the access log prints the raw
    query, which the redaction regex (literal 'token=') does not match. Low: needs a client that encodes the name."""
    inst = instance_or_skip(cfg, "static")
    log = _log_of(cfg, "static")
    start = log.stat().st_size
    with _c(inst) as c:
        r = c.get(f"/dashboard?%74oken={static_token}")
    text = _new_text(log, start)
    leaked = static_token in text
    print(f"RED-02 observed: status {r.status_code}, raw-encoded name {'LEAKS' if leaked else 'redacted'}")
    # the parameter authenticates (Starlette decodes the name), so its value is the static token: must not leak
    assert not leaked, "token value logged in clear when the parameter name is percent-encoded (config.py:24)"


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
def test_RED_03_server_error_path_does_not_log_token(cfg, inst_name):
    """Error path: a request that makes the route fail (POST /compile/<malformed id>, see SID-03) with ?token=.
    Traceback/error lines must not carry the token either."""
    inst = instance_or_skip(cfg, inst_name)
    log = _log_of(cfg, inst_name)
    start = log.stat().st_size
    with _c(inst) as c:
        r = c.post(f"/compile/qa-bad-id?token={cfg.static_token}",
                   headers={"Authorization": f"Bearer {cfg.static_token}"})
    text = _new_text(log, start)
    print("RED-03", inst_name, r.status_code)
    assert cfg.static_token not in text


# ================================================================ ROT: session rotation on re-login (a13bc48)
def _sso_refresh(cfg, refresh_token):
    with httpx.Client(base_url=cfg.sso_url, timeout=30) as c:
        return c.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


@pytest.mark.grey
@pytest.mark.needs_wiki
@pytest.mark.needs_sso
@pytest.mark.parametrize("second_user", ["same", "other"])
def test_ROT_01_relogin_rotates_session(cfg, browser_factory, seeded, session_db, second_user):
    """Re-login (same user, or another user in the same browser): new session id + CSRF; the old cookie gets 401
    (JSON) / 303 to /login (HTML) immediately; the old row is gone; the SSO answers 401 to the old refresh token;
    exactly one row exists for the browser afterwards."""
    inst = instance_or_skip(cfg, "sso")
    store = session_db(instance_name_of(cfg, inst))
    b = browser_factory(inst)
    b.login("qa_wiki_sess", D.PASSWORD)
    old_cookie, old_csrf = b.session_value, b.session_csrf()
    old_row = store.row_for_cookie(old_cookie)
    old_refresh = store.value(old_row, "refresh")
    rows_before = len(store.rows())
    r = b.submit_login_while_signed_in("qa_wiki_sess" if second_user == "same" else "qa_wiki_sess2", D.PASSWORD)
    assert r.status_code == 303 and b.session_value and b.session_value != old_cookie, r.status_code
    assert b.session_csrf() != old_csrf
    assert store.row_for_cookie(old_cookie) is None, "old row must be deleted"
    assert store.row_for_cookie(b.session_value) is not None
    assert len(store.rows()) == rows_before, "net rows: one deleted, one created"
    h = {"Cookie": f"{SESSION_COOKIE}={old_cookie}"}
    with _c(inst) as c:
        assert c.get("/usage", headers=h | {"Accept": "application/json"}).status_code == 401
        rh = c.get("/dashboard", headers=h | {"Accept": "text/html"})
        assert rh.status_code == 303 and rh.headers["location"].startswith("/login"), rh.status_code
        assert c.get("/usage", headers={"Cookie": f"{SESSION_COOKIE}={b.session_value}",
                                        "Accept": "application/json"}).status_code == 200
    rr = _sso_refresh(cfg, old_refresh)
    assert rr.status_code == 401, (rr.status_code, rr.text[:200])


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_ROT_02_failed_relogin_keeps_session(cfg, browser_factory, seeded):
    """Q-10 confirmed: wrong password while signed in -> same cookie, still works (no rotation)."""
    inst = instance_or_skip(cfg, "sso")
    b = browser_factory(inst)
    b.login("qa_wiki_sess", D.PASSWORD)
    old = b.session_value
    r = b.submit_login_while_signed_in("qa_wiki_sess", D.WRONG_PASSWORD)
    assert r.status_code != 303 and b.session_value == old
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 200


@pytest.mark.grey
@pytest.mark.needs_stub
def test_ROT_03_rotation_when_sso_logout_fails(stub, stubbed, browser_factory, session_db):
    """Best effort: the SSO logout of the old refresh token fails (500 / connection close) -> the login still
    succeeds and the old row is still deleted."""
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=900, username="qa_wiki_rot")
    b = browser_factory(stubbed)
    b.login("qa_wiki_rot", D.PASSWORD)
    store = session_db("stubbed")
    for mode in ("server_error", "close"):
        old = b.session_value
        stub.config(logout=mode)
        n0 = stub.count("/api/v1/auth/logout")
        r = b.submit_login_while_signed_in("qa_wiki_rot", D.PASSWORD)
        assert r.status_code == 303 and b.session_value != old, (mode, r.status_code)
        assert store.row_for_cookie(old) is None, mode
        assert stub.count("/api/v1/auth/logout") == n0 + 1, "old refresh token must be sent to the SSO logout"
        assert stub.calls()["last"]["/api/v1/auth/logout"].get("refresh_token"), mode


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_ROT_04_planted_unknown_cookie_then_login(cfg, browser_factory, seeded):
    """Fixation guard still holds after the change: a planted unknown id is replaced and stays dead."""
    inst = instance_or_skip(cfg, "sso")
    b = browser_factory(inst)
    planted = "qa-planted-" + "B" * 40
    b.jar[SESSION_COOKIE] = planted
    b.login("qa_wiki_sess", D.PASSWORD)
    assert b.session_value != planted
    with _c(inst) as c:
        assert c.get("/usage", headers={"Cookie": f"{SESSION_COOKIE}={planted}",
                                        "Accept": "application/json"}).status_code == 401


# ================================================================ SID: malformed source ids (PRE-01)
@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_SID_01_get_sources_malformed_is_404(cfg, inst_name, bad):
    inst = instance_or_skip(cfg, inst_name)
    with _c(inst) as c:
        r = c.get(f"/sources/{bad}")
    assert r.status_code == 404, (bad, r.status_code, r.text[:200])
    assert "Traceback" not in r.text


@pytest.mark.needs_wiki
def test_SID_02_get_sources_wellformed_unknown(cfg):
    """Control: a well-formed unknown id is not a 500 (status recorded)."""
    inst = instance_or_skip(cfg, "static")
    with _c(inst) as c:
        r = c.get("/sources/00000000000000aa")
    print("SID-02", r.status_code, r.text[:120])
    assert r.status_code < 500


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
@pytest.mark.parametrize("bad", BAD_IDS[:3])
def test_SID_03_post_compile_malformed_is_404(cfg, static_token, inst_name, bad):
    """The other HTTP route taking a source id: POST /compile/{source_id} (protected). Expected the same 404 as
    GET /sources (one InvalidSourceId 'for HTTP, MCP and CLI')."""
    inst = instance_or_skip(cfg, inst_name)
    with _c(inst) as c:
        r = c.post(f"/compile/{bad}", headers={"Authorization": f"Bearer {static_token}"})
    assert r.status_code == 404, (bad, r.status_code, r.text[:200])


def _mcp_call(url, auth, tool, args):
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"} | auth
    with httpx.Client(base_url=url, timeout=30) as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "qa", "version": "0"}}}
        r = c.post("/mcp/", json=init, headers=hdr)
        assert r.status_code == 200, r.status_code
        sid = r.headers.get("mcp-session-id")
        h = hdr | ({"mcp-session-id": sid} if sid else {})
        c.post("/mcp/", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args}}, headers=h)
    assert r.status_code == 200, r.status_code
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return r.json()


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
@pytest.mark.parametrize("bad", BAD_IDS[:3])
def test_SID_04_mcp_compile_update_malformed_is_tool_error(cfg, static_token, inst_name, bad):
    inst = instance_or_skip(cfg, inst_name)
    auth = {} if inst.mode == "static" and not inst.protect_reads else {"Authorization": f"Bearer {static_token}"}
    out = _mcp_call(inst.url, auth, "compile_update", {"source_id": bad})
    res = out.get("result") or {}
    text = json.dumps(out)
    assert res.get("isError") is True, text[:400]
    assert "not a valid source_id" in text and "Traceback" not in text, text[:400]


@pytest.mark.needs_wiki
def test_SID_05_mcp_tools_taking_ids_inventory(cfg):
    """compile_update is the only MCP tool with a source_id (the other six take slugs/queries/domains); this
    pins that inventory so a new id-taking tool gets a SID case."""
    inst = instance_or_skip(cfg, "static")
    from test_qasso_mcp_static import mcp_session
    st, tools = mcp_session(inst.url, {})
    assert st == 200
    assert sorted(tools) == sorted(["search_wiki", "get_page", "ingest_source", "compile_update", "list_concepts",
                                    "list_domains", "lint_wiki"]), tools


def _cli(tmp_path, *args):
    exe = Path(sys.executable).with_name("llmwiki")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "STORAGE_BACKEND": "local",
           "LOCAL_STORAGE_PATH": str(tmp_path / "data"), "VECTOR_BACKEND": "memory", "EMBEDDING_BACKEND": "fake",
           "EMBEDDING_DIM": "64", "LLM_BACKEND": "fake", "LEXICAL_BACKEND": "memory", "RERANKER_BACKEND": "none"}
    return subprocess.run([str(exe), *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("cmd", ["compile", "source"])
@pytest.mark.parametrize("bad", ["qa-no-such-source", "../../etc/passwd", ""])
def test_SID_06_cli_malformed_id_clean_error(tmp_path, cmd, bad):
    p = _cli(tmp_path, cmd, bad)
    out = p.stdout + p.stderr
    assert p.returncode != 0, out[-500:]
    assert "Traceback" not in out, out[-1500:]
    assert "not a valid source_id" in p.stderr, out[-500:]


@pytest.mark.needs_wiki
def test_SID_07_compile_wellformed_unknown_id(cfg, static_token, tmp_path):
    """PRE-03 (pre-existing, outside PRE-01): a well-formed id with no captured source. HTTP should not be a 500
    and the CLI should not print a traceback. Not part of 6520890's scope - reported Low."""
    inst = instance_or_skip(cfg, "static")
    with _c(inst) as c:
        r = c.post("/compile/00000000000000aa", headers={"Authorization": f"Bearer {static_token}"})
    p = _cli(tmp_path, "compile", "00000000000000aa")
    print("SID-07 http", r.status_code, "cli rc", p.returncode, "traceback" if "Traceback" in p.stderr else "clean")
    assert r.status_code < 500 and "Traceback" not in p.stderr, (r.status_code, p.stderr[-300:])
