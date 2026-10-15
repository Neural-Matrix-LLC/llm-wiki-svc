"""v1.2b: llm-wiki on sso-client (feat/sso-client). The SSO's introspect answer is authoritative for permissions and
revocation (active answers cached <= 30 s), fail closed (503, session kept), introspect 400 -> 401, client_ip on
logout / rotation (D11), CLIENT_IP_HEADER end to end with the SSO's ENFORCE binding (phase --enforce only).
Test plan: docs/qa/SSO-LOGIN-TEST-PLAN.md section V12B."""
from __future__ import annotations

import os
import time

import httpx
import pytest

import qasso_data as D
from conftest import instance_or_skip, instance_name_of

INTRO = "/api/v1/auth/introspect"
JSON = {"Accept": "application/json"}
CACHE_S = 30


def _until(fn, pred, limit, step=1.0):
    t0 = time.monotonic()
    while True:
        r = fn()
        if pred(r):
            return r, time.monotonic() - t0
        assert time.monotonic() - t0 < limit, f"condition not reached after {limit}s (last {r.status_code})"
        time.sleep(step)


# ------------------------------------------------------------------ cache bound (real SSO)
@pytest.mark.needs_wiki
@pytest.mark.needs_manage
def test_V12B_01_cache_bound_30s_bearer(cfg, tokens, sso_admin, reset_user):
    """Deactivate the user at the SSO after a 200: still 200 while the cached answer lives (proves the cache), then
    401 no later than 30 s (+3 s slack) after the FIRST introspect. Also: the answer is never older than 30 s."""
    reset_user("qa_wiki_mut_deact")
    inst = instance_or_skip(cfg, "sso")
    tok = tokens("qa_wiki_mut_deact", fresh=True)["access_token"]
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        get = lambda: c.get("/usage", headers={**JSON, "Authorization": f"Bearer {tok}"})  # noqa: E731
        t_first = time.monotonic()
        assert get().status_code == 200
        sso_admin.call("set_active", "qa_wiki_mut_deact", False)
        assert get().status_code == 200, "answer served from the 30 s cache right after the change"
        r, _ = _until(get, lambda r: r.status_code != 200, CACHE_S + 8)
        age = time.monotonic() - t_first
    assert r.status_code == 401, r.status_code
    assert 20 <= age <= CACHE_S + 3, age
    reset_user("qa_wiki_mut_deact")


# ------------------------------------------------------------------ fail closed (stub)
@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["server_error", "close", "garbage", "rate_limited"])
def test_V12B_02_sso_down_503_session_kept(stub, stubbed, browser_factory, session_db, mode):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    cookie = b.session_value
    stub.config(introspect=mode)
    t0 = time.monotonic()
    r = b.get("/usage", headers=JSON)
    assert r.status_code == 503, (mode, r.status_code)
    assert time.monotonic() - t0 < 15
    assert session_db("stubbed").row_for_cookie(cookie) is not None, "session kept while the SSO is down"
    stub.config(introspect="auto")
    assert b.get("/usage", headers=JSON).status_code == 200, "same session works again once the SSO is back"


@pytest.mark.needs_stub
def test_V12B_02b_503_carries_retry_after(stub, stubbed, browser_factory):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    stub.config(introspect="server_error")
    r = b.get("/usage", headers=JSON)
    stub.config(introspect="auto")
    assert r.status_code == 503 and r.headers.get("retry-after"), dict(r.headers)


@pytest.mark.needs_stub
def test_V12B_03_sso_hang_bounded(stub, stubbed, browser_factory, session_db):
    """SSO accepts the connection and never answers: the request ends with 503 (session kept). Records the time."""
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess",
                hang_seconds=60)
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    cookie = b.session_value
    stub.config(introspect="hang")
    t0 = time.monotonic()
    r = b.get("/usage", headers=JSON, timeout=90)
    secs = time.monotonic() - t0
    print(f"V12B_03 hang -> {r.status_code} after {secs:.1f}s")
    stub.config(introspect="auto")
    assert r.status_code == 503
    assert session_db("stubbed").row_for_cookie(cookie) is not None
    assert secs < 15, f"503 only after {secs:.1f}s (introspect timeout x attempts)"


@pytest.mark.needs_stub
def test_V12B_04_introspect_400_is_401(stub, stubbed, browser_factory):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    stub.config(introspect="bad_request")
    assert b.get("/usage", headers=JSON).status_code == 401


@pytest.mark.needs_stub
def test_V12B_05_introspect_inactive_is_401_and_session_deleted(stub, stubbed, browser_factory, session_db):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    cookie = b.session_value
    stub.config(introspect="inactive")
    assert b.get("/usage", headers=JSON).status_code == 401
    assert session_db("stubbed").row_for_cookie(cookie) is None


@pytest.mark.needs_stub
def test_V12B_06_introspect_perm_change_current(stub, stubbed, browser_factory):
    """The stub answers with the CURRENT perms knob: dropping app1 there -> 403 although the token still claims it."""
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    stub.config(perms=[])
    assert b.get("/usage", headers=JSON).status_code == 403


@pytest.mark.needs_stub
def test_V12B_07_introspect_sends_client_ip_and_service_auth(stub, stubbed, browser_factory):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_sess")
    b = browser_factory(stubbed)
    b.login("qa_wiki_sess", D.PASSWORD)
    assert b.get("/usage", headers=JSON).status_code == 200
    calls = stub.calls()
    assert calls["last"][INTRO].get("ip") == "127.0.0.1", calls["last"][INTRO]
    assert calls["auth"][INTRO]["client_id"] == D.LOGIN_CLIENT_ID


# ------------------------------------------------------------------ D11 client_ip on logout / refresh / rotation
@pytest.mark.needs_stub
def test_V12B_08_client_ip_on_logout(stub, stubbed, browser_factory):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_logout")
    b = browser_factory(stubbed)
    b.login("qa_wiki_logout", D.PASSWORD)
    assert stub.calls()["last"]["/api/v1/auth/login"].get("client_ip") == "127.0.0.1"
    b.logout()
    last = stub.calls()["last"]["/api/v1/auth/logout"]
    assert last.get("client_ip") == "127.0.0.1" and last.get("refresh_token"), sorted(last)


@pytest.mark.needs_stub
def test_V12B_09_client_ip_on_rotation(stub, stubbed, browser_factory):
    """Session rotation (login while signed in) logs the old refresh token out with client_ip (D11)."""
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600, username="qa_wiki_rot")
    b = browser_factory(stubbed)
    b.login("qa_wiki_rot", D.PASSWORD)
    n0 = stub.count("/api/v1/auth/logout")
    r = b.submit_login_while_signed_in("qa_wiki_rot", D.PASSWORD)
    assert r.status_code == 303
    assert stub.count("/api/v1/auth/logout") == n0 + 1
    assert stub.calls()["last"]["/api/v1/auth/logout"].get("client_ip") == "127.0.0.1"


@pytest.mark.needs_stub
def test_V12B_10_client_ip_on_refresh(stub, stubbed, browser_factory):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=3, username="qa_wiki_refresh")
    b = browser_factory(stubbed)
    b.login("qa_wiki_refresh", D.PASSWORD)
    time.sleep(5)
    assert b.get("/usage", headers=JSON).status_code == 200
    assert stub.count("/api/v1/auth/refresh") >= 1
    assert stub.calls()["last"]["/api/v1/auth/refresh"].get("client_ip") == "127.0.0.1"


# ------------------------------------------------------------------ ENFORCE + CLIENT_IP_HEADER (phase --enforce)
enforce = pytest.mark.skipif(os.environ.get("QA_SSO_ENFORCE_PHASE") != "1",
                             reason="needs start_all.sh --enforce (SSO ENFORCE, wiki-sso CLIENT_IP_HEADER=x-real-ip)")


@enforce
@pytest.mark.needs_wiki
def test_V12B_11_client_ip_header_with_enforce(cfg, browser_factory, session_db, sso_http, service_auth):
    """wiki-sso trusts 127.0.0.1 and reads X-Real-IP; the SSO (ENFORCE) binds the session to that IP at login.
    Same IP -> 200; another IP -> 401, the session row is deleted and the SSO revoked the whole sid (introspect
    from the original IP is inactive too). XFF interaction: see V12B-14 (D-19)."""
    inst = instance_or_skip(cfg, "sso")
    ip_a, ip_b = "203.0.113.7", "198.51.100.9"
    b = browser_factory(inst)
    r = b.submit_login("qa_wiki_sess2", D.PASSWORD, headers={"X-Real-IP": ip_a})
    assert r.status_code == 303, r.status_code
    store = session_db(instance_name_of(cfg, inst))
    access = store.value(store.row_for_cookie(b.session_value), "access")
    assert b.get("/usage", headers={**JSON, "X-Real-IP": ip_a}).status_code == 200
    ok = sso_http.post(INTRO, auth=service_auth, json={"token": access, "ip": ip_a}).json()
    assert ok["active"] is True and ok.get("ip_binding") == "ENFORCE" and ok.get("ip_match") is True, ok
    cookie = b.session_value
    r = b.get("/usage", headers={**JSON, "X-Real-IP": ip_b})
    assert r.status_code == 401, r.status_code
    assert store.row_for_cookie(cookie) is None
    gone = sso_http.post(INTRO, auth=service_auth, json={"token": access, "ip": ip_a}).json()
    assert gone == {"active": False}, "ENFORCE mismatch revokes the whole SSO session"


@enforce
@pytest.mark.needs_wiki
def test_V12B_12_no_header_binds_peer_ip(cfg, browser_factory):
    """Without X-Real-IP / XFF the client IP is the trusted peer itself (127.0.0.1); the bound session then refuses
    a later request that claims another IP via the header."""
    inst = instance_or_skip(cfg, "sso")
    b = browser_factory(inst)
    b.login("qa_wiki_sess", D.PASSWORD)
    assert b.get("/usage", headers=JSON).status_code == 200
    assert b.get("/usage", headers={**JSON, "X-Real-IP": "203.0.113.50"}).status_code == 401


@enforce
@pytest.mark.needs_wiki
def test_V12B_13_header_ignored_on_instances_without_it(cfg, tokens):
    """Instances without CLIENT_IP_HEADER/TRUSTED_PROXY_IPS (wiki-both) ignore X-Real-IP: a bearer token bound to
    127.0.0.1 stays valid whatever the header claims. (XFF: V12B-14, D-19.)"""
    inst = instance_or_skip(cfg, "both")
    tok = tokens("qa_wiki_direct", fresh=True)["access_token"]
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        for h in ({}, {"X-Real-IP": "203.0.113.60"}):
            assert c.get("/usage", headers={**JSON, **h, "Authorization": f"Bearer {tok}"}).status_code == 200, h


@enforce
@pytest.mark.needs_wiki
@pytest.mark.parametrize("name,hdrs", [
    ("both", {"X-Forwarded-For": "203.0.113.61"}),                         # no CLIENT_IP_HEADER configured
    ("sso", {"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "203.0.113.62"}),  # header must win over XFF
])
def test_V12B_14_xff_never_read_unless_configured(cfg, tokens, name, hdrs):
    """A loopback client sending X-Forwarded-For must not pick its own client IP: without CLIENT_IP_HEADER no header
    is read (design v1.2b 'Client IP'); with CLIENT_IP_HEADER=x-real-ip that header decides. Bearer bound to
    127.0.0.1 -> must stay 200 (observed: 401, the SSO logs ip_mismatch with the XFF value and revokes the sid)."""
    inst = instance_or_skip(cfg, name)
    tok = tokens("qa_wiki_direct", fresh=True)["access_token"]
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        r = c.get("/usage", headers={**JSON, **hdrs, "Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.status_code
