"""LOGIN-*: the login page against the real SSO (design B4). Instance: sso (else both)."""
from __future__ import annotations

import html
import re

import pytest

import qasso_data as D
from conftest import access_log_lines
from wiki_client import SESSION_COOKIE, set_cookie

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]

MSG_WRONG = "Wrong username or password"
MSG_INACTIVE = "Please activate your account"
MSG_NOPERM = "doesn't have access to llm-wiki"


def _norm(page: str) -> str:
    """Strip per-request noise (CSRF values, echoed username) to compare two error pages."""
    page = re.sub(r'value="[^"]*"', 'value=""', page)
    page = re.sub(r"value='[^']*'", "value=''", page)
    page = re.sub(r"qa_[a-z_]+|nobody_[a-z0-9_]+", "USER", page)
    return page


def _failed(b, r):
    assert r.status_code != 303, (r.status_code, r.headers.get("location"))
    assert r.status_code < 500 or r.status_code == 502, r.status_code
    assert b.session_value is None and set_cookie(r, SESSION_COOKIE) is None
    assert "access_token" not in r.text and "eyJ" not in r.text


def test_LOGIN_01_get_login_page(wiki, browser_factory):
    b = browser_factory(wiki)
    r, form = b.open_login("/dashboard")
    assert r.headers["content-type"].startswith("text/html")
    assert form.password_field and form.user_field, form.raw[:300]
    assert form.method == "post"
    hidden = [k for k in form.fields if k not in (form.user_field,)]
    assert hidden, "a hidden CSRF field is expected (double submit)"
    assert b.csrf_cookie_names(), "a CSRF cookie is expected on GET /login"
    assert SESSION_COOKIE not in b.jar
    assert "/dashboard" in form.fields.values() or "/dashboard" in form.action, "next must survive the form"


@pytest.mark.parametrize("username", ["qa_wiki_direct", "qa_wiki_group", "qa_wiki_super_app1", "qa_wiki_super_group"])
def test_LOGIN_02_success_sets_session_and_redirects(wiki, browser_factory, seeded, username):
    b = browser_factory(wiki)
    r = b.login(username, D.PASSWORD)
    assert r.headers["location"] == "/dashboard"
    d = b.get("/dashboard")
    assert d.status_code == 200, d.status_code
    assert f"Signed in as {username}" in d.text or ("Signed in as" in d.text and username in d.text)
    assert "Log out" in d.text


def test_LOGIN_03_login_by_email(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    b.login(D.USERS["qa_wiki_direct"]["email"], D.PASSWORD)
    assert b.get("/dashboard").status_code == 200


def test_LOGIN_04_next_honoured(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.login("qa_wiki_direct", D.PASSWORD, next_path="/usage?month=2026-10")
    assert r.headers["location"] == "/usage?month=2026-10"
    assert b.get("/usage?month=2026-10", headers={"Accept": "application/json"}).status_code == 200


def test_LOGIN_05_wrong_password(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.submit_login("qa_wiki_direct", D.WRONG_PASSWORD)
    _failed(b, r)
    assert MSG_WRONG in r.text
    assert D.WRONG_PASSWORD not in r.text, "password must not be echoed"


def test_LOGIN_06_unknown_user_indistinguishable(wiki, browser_factory, seeded):
    b1, b2 = browser_factory(wiki), browser_factory(wiki)
    known = b1.submit_login("qa_wiki_direct", D.WRONG_PASSWORD)
    unknown = b2.submit_login("nobody_qa_xyz", D.WRONG_PASSWORD)
    _failed(b2, unknown)
    assert known.status_code == unknown.status_code
    assert MSG_WRONG in unknown.text
    assert _norm(known.text) == _norm(unknown.text), "error pages must not reveal whether the account exists"


def test_LOGIN_07_inactive_correct_password(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.submit_login("qa_wiki_inactive", D.PASSWORD)
    _failed(b, r)
    assert MSG_INACTIVE in r.text


def test_LOGIN_08_inactive_wrong_password_looks_like_wrong_password(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.submit_login("qa_wiki_inactive", D.WRONG_PASSWORD)
    _failed(b, r)
    assert MSG_WRONG in r.text and MSG_INACTIVE not in r.text


@pytest.mark.parametrize("username", ["qa_wiki_none", "qa_wiki_staff", "qa_wiki_app2", "qa_wiki_nonapp",
                                      "qa_wiki_super"])  # superuser without a grant (default false, 2026-10-11)
def test_LOGIN_09_no_permission_page_and_no_session(wiki, browser_factory, seeded, username):
    b = browser_factory(wiki)
    r = b.submit_login(username, D.PASSWORD)
    _failed(b, r)
    text = html.unescape(r.text)        # the page HTML-escapes the apostrophe (doesn&#x27;t) - correct
    assert MSG_NOPERM in text and "application.app1" in text


def test_LOGIN_10_no_permission_discards_refresh_token_at_sso(wiki, browser_factory, seeded, cfg):
    """B4: on no permission llm-wiki calls SSO /logout with the refresh token."""
    before = access_log_lines(cfg, "POST /api/v1/auth/logout")
    b = browser_factory(wiki)
    b.submit_login("qa_wiki_none", D.PASSWORD)
    assert access_log_lines(cfg, "POST /api/v1/auth/logout") == before + 1


@pytest.mark.parametrize("user,pw", [("", D.PASSWORD), ("qa_wiki_direct", ""), ("", ""),
                                     ("qa_wiki_direct", "x" * 10000), ("q" * 5000, D.PASSWORD)])
def test_LOGIN_11_degenerate_input_no_500(wiki, browser_factory, seeded, user, pw):
    b = browser_factory(wiki)
    _failed(b, b.submit_login(user, pw))


def test_LOGIN_12_username_reflection_is_escaped(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.submit_login('<script>alert("u")</script>', D.WRONG_PASSWORD)
    assert '<script>alert("u")</script>' not in r.text


def test_LOGIN_13_next_reflection_is_escaped(wiki, browser_factory):
    b = browser_factory(wiki)
    r = b.get('/login?next=/x"><script>alert(1)</script>')
    assert r.status_code in (200, 400)
    assert "<script>alert(1)</script>" not in r.text


def test_LOGIN_14_success_response_carries_no_tokens(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    r = b.login("qa_wiki_direct", D.PASSWORD)
    assert "eyJ" not in r.text and "eyJ" not in r.headers.get("location", "")
    assert "token" not in r.headers.get("location", "").lower()


def test_LOGIN_15_login_post_sends_native_client_service_auth_and_client_ip(stub, stubbed, browser_factory):
    """B4 + v1.1 item 4: POST /login -> SSO login with client "native", HTTP Basic as SSO_CLIENT_ID, and
    client_ip = the browser's IP (here the loopback source address the test binds to)."""
    import qasso_data as QD
    b = browser_factory(stubbed, source_ip="127.0.0.61")
    b.submit_login("qa_wiki_direct", D.PASSWORD)
    calls = stub.calls()
    last = calls["last"].get("/api/v1/auth/login") or {}
    assert last.get("client") == "native", last
    assert last.get("username") == "qa_wiki_direct"
    assert last.get("client_ip") == "127.0.0.61", last
    auth = calls.get("auth", {}).get("/api/v1/auth/login") or {}
    assert auth.get("scheme") == "basic" and auth.get("client_id") == QD.LOGIN_CLIENT_ID, auth


def test_LOGIN_15b_refresh_sends_service_auth_and_client_ip(stub, stubbed, browser_factory):
    """v1.1 item 4: /refresh is delegated the same way (client_ip of the browser whose request triggered it)."""
    import time

    import qasso_data as QD
    stub.config(login="mint", refresh="mint", access_ttl=65, username="qa_wiki_refresh")
    b = browser_factory(stubbed, source_ip="127.0.0.62")
    b.login("qa_wiki_refresh", D.PASSWORD)
    time.sleep(6)
    assert b.get("/dashboard").status_code == 200
    calls = stub.calls()
    assert (calls["last"].get("/api/v1/auth/refresh") or {}).get("client_ip") == "127.0.0.62", calls["last"]
    auth = calls.get("auth", {}).get("/api/v1/auth/refresh") or {}
    assert auth.get("scheme") == "basic" and auth.get("client_id") == QD.LOGIN_CLIENT_ID, auth


def test_LOGIN_16_already_signed_in_visit_to_login(wiki, browser_factory, seeded):
    """Q-08 (still not in the design; QA judgment): GET /login while signed in is either the form (200) or a
    303 to a LOCAL path; never 5xx, never an off-site redirect. Behaviour is printed for the decision list."""
    b = browser_factory(wiki)
    b.login("qa_wiki_direct", D.PASSWORD)
    r = b.get("/login?next=//evil.example/x")
    print("Q-08 observed:", r.status_code, r.headers.get("location"))
    # Q-08 confirmed by Philip (2026-10-10): 303 to the sanitised next (off-site -> /dashboard)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard", (r.status_code, r.headers.get("location"))


def test_LOGIN_17_login_page_in_static_mode(cfg):
    """v1.1 item 5 (Q-01): no /login in static mode -> 404 (also REG-12)."""
    import httpx
    inst = cfg.instances.get("static") or pytest.skip("LLM_WIKI_STATIC_URL not set")
    with httpx.Client(base_url=inst.url, timeout=30) as c:
        assert c.get("/login").status_code == 404


def test_LOGIN_18_get_login_has_no_sso_dependency(stub, stubbed, browser_factory):
    """Rendering the form never calls the SSO, so it works while the SSO is down."""
    stub.config(login="close", refresh="close", logout="close")
    b = browser_factory(stubbed)
    r = b.get("/login")
    assert r.status_code == 200
    assert stub.count("/api/v1/auth/login") == 0
