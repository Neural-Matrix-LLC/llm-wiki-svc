"""SU-*: superusers get NO automatic app access (Thomas via Philip, 2026-10-11; sso-server e9b0b36:
SSO_SUPERUSER_ALL_APPS defaults to false, true is an opt-in). End to end through llm-wiki.

SU-01  default: superuser without a grant -> perms [] -> llm-wiki 403 JSON (Bearer), HTML no-permission page on
       /login, no session row, no cookie; refresh token discarded at the SSO
SU-02  default: superuser with a direct grant / via a group -> normal login, 200
SU-03  inactive superusers (with or without grants) -> login refused at llm-wiki, no session
SU-04  flag true (2nd SSO, same keys/issuer): the same no-grant superuser's token -> 200 at llm-wiki (Bearer)
SU-05  flag true: the same user logs in through the llm-wiki login page of a throw-away instance whose SSO_BASE_URL
       is the flag-true SSO -> 303 /dashboard and a session
"""
from __future__ import annotations

import html

import httpx
import pytest

import jwt_forge
import qasso_data as D
from conftest import instance_name_of, sso_login
from wiki_client import SESSION_COOKIE

MSG_NOPERM = "doesn't have access to llm-wiki"


def _json_get(url, token, path="/usage"):
    with httpx.Client(base_url=url, timeout=30) as c:
        return c.get(path, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})


@pytest.mark.grey
def test_SU_01_superuser_without_grant_denied_by_default(cfg, wiki, sso_http, browser_factory, seeded, session_db):
    tok = sso_login(sso_http, "qa_wiki_super").json()["access_token"]
    assert jwt_forge.payload(tok)["perms"] == []
    r = _json_get(wiki.url, tok)
    assert r.status_code == 403, r.status_code
    assert "www-authenticate" not in {k.lower() for k in r.headers}, "403 has no challenge"
    # HTML route with the Bearer: 403 page, not a redirect to /login
    with httpx.Client(base_url=wiki.url, timeout=30) as c:
        h = c.get("/dashboard", headers={"Authorization": f"Bearer {tok}", "Accept": "text/html"})
    assert h.status_code == 403, h.status_code
    # login page: no-permission page, no session cookie, no session row
    store = session_db(instance_name_of(cfg, wiki))
    rows_before = len(store.rows_for("qa_wiki_super"))
    b = browser_factory(wiki)
    lr = b.submit_login("qa_wiki_super", D.PASSWORD)
    assert lr.status_code != 303, (lr.status_code, lr.headers.get("location"))
    assert MSG_NOPERM in html.unescape(lr.text) and "application.app1" in lr.text
    assert b.session_value is None
    assert not any(v.startswith(f"{SESSION_COOKIE}=") and "Max-Age=0" not in v
                   for v in lr.headers.get_list("set-cookie"))
    assert len(store.rows_for("qa_wiki_super")) == rows_before == 0
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 401


@pytest.mark.parametrize("username", ["qa_wiki_super_app1", "qa_wiki_super_group"])
def test_SU_02_superuser_with_explicit_grant_works(wiki, browser_factory, sso_http, seeded, username):
    tok = sso_login(sso_http, username).json()["access_token"]
    assert jwt_forge.payload(tok)["perms"] == [D.APP_PERM]
    assert _json_get(wiki.url, tok).status_code == 200
    b = browser_factory(wiki)
    b.login(username, D.PASSWORD)
    assert b.get("/dashboard").status_code == 200


@pytest.mark.grey
@pytest.mark.parametrize("username", ["qa_wiki_inactive_super", "qa_wiki_inactive_super_app1"])
def test_SU_03_inactive_superuser_cannot_log_in(cfg, wiki, browser_factory, seeded, session_db, username):
    store = session_db(instance_name_of(cfg, wiki))
    b = browser_factory(wiki)
    r = b.submit_login(username, D.PASSWORD)
    assert r.status_code != 303 and b.session_value is None, r.status_code
    assert r.status_code < 500
    assert store.rows_for(username) == []


def test_SU_04_flag_true_same_superuser_gets_access(cfg, wiki, sso_admin, seeded):
    if not cfg.sso_allapps_url:
        pytest.skip("SSO_ALLAPPS_URL not set")
    with httpx.Client(base_url=cfg.sso_allapps_url, timeout=30) as c:
        tok = sso_login(c, "qa_wiki_super").json()["access_token"]
    assert jwt_forge.payload(tok)["perms"] == sso_admin.json("all_app_perms")
    assert _json_get(wiki.url, tok).status_code == 200      # same keys/issuer: llm-wiki trusts it
    with httpx.Client(base_url=cfg.sso_allapps_url, timeout=30) as c:
        r = sso_login(c, "qa_wiki_inactive_super")
    assert r.status_code == 403 and r.json().get("error") == "account_inactive"


def test_SU_05_flag_true_login_page_works_for_superuser(cfg, tmp_path, browser_factory, seeded):
    """Throw-away llm-wiki (sso mode) on the startup port, SSO_BASE_URL = the flag-true SSO (issuer unchanged).
    Stopped by its own process group, never by name or port."""
    if not cfg.sso_allapps_url:
        pytest.skip("SSO_ALLAPPS_URL not set")
    from test_qasso_startup import PORT, _outcome, _start, _stop
    p = _start(cfg, tmp_path, {"AUTH_MODE": "sso", "SSO_BASE_URL": cfg.sso_allapps_url, "SSO_ISSUER": cfg.sso_issuer})
    try:
        assert _outcome(p)[0] == "serving"
        b = browser_factory(f"http://127.0.0.1:{PORT}")
        r = b.login("qa_wiki_super", D.PASSWORD)
        assert r.headers["location"] == "/dashboard"
        assert b.get("/dashboard").status_code == 200
        nb = browser_factory(f"http://127.0.0.1:{PORT}")
        assert nb.submit_login("qa_wiki_none", D.PASSWORD).status_code != 303   # non-superusers unaffected
    finally:
        _stop(p)
