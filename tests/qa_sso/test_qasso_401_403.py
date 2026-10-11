"""SEM-*: 401 vs 403 semantics, HTML vs JSON (design B2/B3/B4)."""
from __future__ import annotations

import httpx
import pytest

import qasso_data as D

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]


def _c(inst):
    return httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False)


def test_SEM_01_no_perm_is_403_with_exact_body_and_no_challenge(wiki, tokens):
    with _c(wiki) as c:
        r = c.get("/usage", headers={"Authorization": f"Bearer {tokens('qa_wiki_none')['access_token']}"})
    assert r.status_code == 403
    assert r.json() == {"detail": "forbidden", "error": "permission_denied"}
    assert "www-authenticate" not in r.headers, "403 must not invite a re-authentication / refresh loop"


@pytest.mark.parametrize("auth", [None, "Bearer not-a-token", "Bearer a.b.c"])
def test_SEM_02_invalid_or_missing_is_401_with_bearer_challenge(wiki, auth):
    with _c(wiki) as c:
        r = c.get("/usage", headers={"Authorization": auth} if auth else {})
    assert r.status_code == 401
    www = r.headers.get("www-authenticate", "")
    assert www.lower().startswith("bearer"), www
    if auth:
        assert 'error="invalid_token"' in www, www


def test_SEM_03_401_never_says_forbidden_403_never_for_bad_token(wiki, tokens):
    with _c(wiki) as c:
        bad = c.get("/usage", headers={"Authorization": "Bearer a.b.c"})
        assert "permission_denied" not in bad.text
        expired_like = c.get("/usage", headers={"Authorization": f"Bearer {tokens('qa_wiki_direct')['refresh_token']}"})
        assert expired_like.status_code == 401


def test_SEM_04_html_route_unauthenticated_redirects_json_route_401(wiki):
    with _c(wiki) as c:
        html = c.get("/dashboard", headers={"Accept": "text/html"})
        api = c.get("/usage", headers={"Accept": "text/html"})   # a JSON route stays 401 even for a browser
    loc = html.headers.get("location", "")
    assert html.status_code == 303 and loc in ("/login?next=/dashboard", "/login?next=%2Fdashboard"), loc
    assert api.status_code == 401


def test_SEM_05_dashboard_redirect_keeps_query_in_next(wiki):
    with _c(wiki) as c:
        r = c.get("/dashboard?month=2026-10")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/login?next=") and "2026-10" in loc and "month" in loc, loc


def test_SEM_06_bearer_without_perm_on_html_route_is_403(wiki, tokens):
    with _c(wiki) as c:
        r = c.get("/dashboard", headers={"Authorization": f"Bearer {tokens('qa_wiki_none')['access_token']}"})
    assert r.status_code == 403


def test_SEM_07_healthz_and_login_public_even_with_protected_reads(cfg):
    inst = cfg.instances.get("sso_reads") or cfg.instances.get("both_reads")
    if not inst:
        pytest.skip("no SSO_PROTECT_READS=true instance")
    with _c(inst) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/login").status_code == 200
        assert c.get("/domains").status_code == 401


def test_SEM_08_accept_header_does_not_change_api_semantics(wiki, tokens):
    """Design distinguishes by route (HTML vs JSON), not by Accept. JSON route + Accept: text/html -> still JSON 403."""
    with _c(wiki) as c:
        r = c.get("/usage", headers={"Accept": "text/html", "Authorization": f"Bearer {tokens('qa_wiki_none')['access_token']}"})
    assert r.status_code == 403 and r.headers["content-type"].startswith("application/json")


def test_SEM_09_session_user_without_perm_never_gets_session(wiki, browser_factory, seeded):
    """No-permission login must not create a session (B4), so 403-with-session cannot happen on login."""
    b = browser_factory(wiki)
    r = b.submit_login("qa_wiki_none", D.PASSWORD)
    assert r.status_code != 303 and b.session_value is None


@pytest.mark.parametrize("hdr,want", [("Bearer garbage", 401), ("Bearer a.b.c", 401),
                                      ("Basic Zm9vOmJhcg==", 303)])
def test_SEM_10_html_route_invalid_explicit_bearer_is_401(cfg, hdr, want):
    """v1.1 item 7 (Q-07) / v1.2 Q-31: HTML route with an invalid explicit *Bearer* -> 401; no credentials -> 303
    /login. A non-Bearer scheme (Basic) is not a credential for llm-wiki -> treated as none -> 303."""
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    with _c(inst) as c:
        r = c.get("/dashboard", headers={"Authorization": hdr, "Accept": "text/html"})
        assert r.status_code == want, (hdr, r.status_code)
        assert c.get("/dashboard", headers={"Accept": "text/html"}).status_code == 303


def test_SEM_11_html_route_expired_sso_bearer_is_401(cfg, tokens, forge):
    import time
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    expired = forge(tokens("qa_wiki_direct")["access_token"], exp=int(time.time()) - 120)
    with _c(inst) as c:
        assert c.get("/dashboard", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_SEM_12_invalid_bearer_plus_valid_cookie_is_401(cfg, browser_factory, seeded):
    """Q-31 (new): an explicit Bearer decides, even next to a valid session cookie (JSON and HTML)."""
    import qasso_data as D
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    b = browser_factory(inst)
    b.login("qa_wiki_direct", D.PASSWORD)
    assert b.get("/usage", headers={"Authorization": "Bearer garbage", "Accept": "application/json"}).status_code == 401
    assert b.get("/dashboard", headers={"Authorization": "Bearer garbage"}).status_code == 401
