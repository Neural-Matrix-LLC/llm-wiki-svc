"""LOGOUT-*: POST /logout (design B4 + v1.1 items 2/3): the form carries the session's csrf value in a hidden
field; logout revokes the refresh token at the SSO, deletes the server-side row and clears the cookie, so a
copied cookie is dead immediately (D-02 resolved)."""
from __future__ import annotations

import time

import httpx
import pytest

import qasso_data as D
from wiki_client import SESSION_COOKIE, logout_form, set_cookie

pytestmark = [pytest.mark.needs_wiki]


def _logout(b):
    return b.logout()


def _row(session_db, cfg, inst, cookie):
    from conftest import instance_name_of
    try:
        store = session_db(instance_name_of(cfg, inst))
    except pytest.skip.Exception:
        return None, None
    return store, store.row_for_cookie(cookie)


@pytest.mark.needs_sso
def test_LOGOUT_01_clears_cookie_redirects_and_revokes_at_sso(wiki, logged_in, sso_http, session_db, cfg):
    b = logged_in("qa_wiki_logout")
    cookie = b.session_value
    store, row = _row(session_db, cfg, wiki, cookie)
    refresh = store.value(row, "refresh") if row else None
    r = _logout(b)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    c = set_cookie(r, SESSION_COOKIE)
    assert c is not None and c.deleted and c.attrs.get("path", "/") == "/", c.raw if c else None
    if store:
        assert store.row_for_cookie(cookie) is None, "v1.1 item 2: logout deletes the row"
    if refresh:
        assert sso_http.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401


@pytest.mark.needs_sso
def test_LOGOUT_02_after_logout_dashboard_redirects(wiki, logged_in):
    b = logged_in("qa_wiki_logout")
    _logout(b)
    r = b.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


@pytest.mark.needs_sso
def test_LOGOUT_03_copied_cookie_dead_after_logout(wiki, logged_in):
    """D-02 resolved: a copy of the cookie taken before logout is rejected immediately (row deleted)."""
    b = logged_in("qa_wiki_logout")
    stolen = b.session_value
    _logout(b)
    with httpx.Client(base_url=wiki.url, timeout=30, follow_redirects=False) as c:
        r = c.get("/usage", headers={"Cookie": f"{SESSION_COOKIE}={stolen}", "Accept": "application/json"})
        assert r.status_code == 401, "pre-logout cookie still grants access"
        r = c.get("/dashboard", headers={"Cookie": f"{SESSION_COOKIE}={stolen}"})
        assert r.status_code == 303


@pytest.mark.needs_sso
def test_LOGOUT_03b_logout_kills_only_that_session(wiki, browser_factory, seeded):
    """Same user, two browsers = two sessions: logging out one leaves the other (no 'log out everywhere', Q-15)."""
    a, b = browser_factory(wiki), browser_factory(wiki)
    a.login("qa_wiki_logout", D.PASSWORD)
    b.login("qa_wiki_logout", D.PASSWORD)
    _logout(a)
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 200


@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["server_error", "close", "hang"])
def test_LOGOUT_04_sso_failure_still_logs_out_locally(stub, stubbed, browser_factory, session_db, mode):
    stub.config(login="mint", refresh="mint", logout=mode, hang_seconds=25, access_ttl=600)
    b = browser_factory(stubbed)
    b.login("qa_wiki_logout", D.PASSWORD)
    cookie = b.session_value
    t0 = time.monotonic()
    r = _logout(b)
    assert time.monotonic() - t0 < 14
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert b.session_value is None
    assert session_db("stubbed").row_for_cookie(cookie) is None, "row deleted even if the SSO call failed"


@pytest.mark.needs_stub
def test_LOGOUT_05_sends_the_sessions_refresh_token_to_sso(stub, stubbed, browser_factory, session_db):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=600)
    b = browser_factory(stubbed)
    b.login("qa_wiki_logout", D.PASSWORD)
    store = session_db("stubbed")
    refresh = store.value(store.row_for_cookie(b.session_value), "refresh")
    _logout(b)
    last = stub.calls()["last"].get("/api/v1/auth/logout")
    assert isinstance(last, dict) and last.get("refresh_token") == refresh, last


def test_LOGOUT_06_without_session(wiki, browser_factory):
    b = browser_factory(wiki)
    _, form = b.open_login()
    r = b.post_form("/logout", {k: v for k, v in form.fields.items() if k != form.user_field})
    assert r.status_code in (303, 400, 403) and r.status_code < 500


@pytest.mark.needs_sso
def test_LOGOUT_08_form_carries_session_csrf(wiki, logged_in):
    """v1.1 item 3: 'forms use a hidden field' = the session's csrf value (same as the meta tag)."""
    b = logged_in("qa_wiki_logout")
    page = b.get("/dashboard").text
    form = logout_form(page)
    assert form is not None
    meta = b.session_csrf()
    assert meta in form.fields.values(), f"logout form hidden fields {list(form.fields)} lack the session csrf"


def test_LOGOUT_07_logout_all_from_llm_wiki():
    pytest.skip("Q-15 decided (Philip, 2026-10-10): 'log out everywhere' is out of scope for llm-wiki; see "
                "SESS-31 for the effect of an SSO-side logout-all on an llm-wiki session")
