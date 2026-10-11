"""PREF-*: refresh re-computes perms (design A2 'takes effect on the next refresh (<=15 min)')."""
from __future__ import annotations

import pytest

import jwt_forge
import qasso_data as D
from conftest import sso_login

pytestmark = [pytest.mark.needs_sso, pytest.mark.needs_manage]


def _refresh(sso_http, refresh_token):
    return sso_http.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


def _introspect(sso_http, token, auth):
    return sso_http.post("/api/v1/auth/introspect", json={"token": token}, auth=auth).json()


def _login(sso_http, u):
    r = sso_login(sso_http, u)
    assert r.status_code == 200, r.text
    return r.json()


def test_PREF_01_new_direct_grant_on_refresh(sso_http, sso_admin, reset_user, service_auth):
    reset_user("qa_wiki_mut_grant")
    first = _login(sso_http, "qa_wiki_mut_grant")
    assert D.APP_PERM not in (jwt_forge.payload(first["access_token"]).get("perms") or [])
    sso_admin.call("grant", "qa_wiki_mut_grant", D.APP_PERM)
    # the existing access token is unchanged (stateless) until it expires ...
    assert D.APP_PERM not in (jwt_forge.payload(first["access_token"]).get("perms") or [])
    # ... but introspect reads perms fresh from the DB (A2)
    assert D.APP_PERM in (_introspect(sso_http, first["access_token"], service_auth).get("perms") or [])
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 200, r.text
    assert D.APP_PERM in jwt_forge.payload(r.json()["access_token"])["perms"]
    if "user" in r.json():
        assert D.APP_PERM in r.json()["user"]["perms"]


def test_PREF_02_revoked_direct_grant_dropped_on_refresh(sso_http, sso_admin, reset_user, service_auth):
    reset_user("qa_wiki_mut_revoke")
    first = _login(sso_http, "qa_wiki_mut_revoke")
    assert D.APP_PERM in jwt_forge.payload(first["access_token"])["perms"]
    sso_admin.call("revoke", "qa_wiki_mut_revoke", D.APP_PERM)
    assert D.APP_PERM in jwt_forge.payload(first["access_token"])["perms"], "old token keeps the claim until exp"
    intro = _introspect(sso_http, first["access_token"], service_auth)
    assert intro["active"] is True and D.APP_PERM not in (intro.get("perms") or []), intro
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 200, r.text
    assert D.APP_PERM not in (jwt_forge.payload(r.json()["access_token"]).get("perms") or [])


def test_PREF_03_removed_from_group_dropped_on_refresh(sso_http, sso_admin, reset_user):
    reset_user("qa_wiki_mut_group")
    first = _login(sso_http, "qa_wiki_mut_group")
    assert D.APP_PERM in jwt_forge.payload(first["access_token"])["perms"]
    sso_admin.call("remove_group", "qa_wiki_mut_group", D.APP_GROUP)
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 200
    assert D.APP_PERM not in (jwt_forge.payload(r.json()["access_token"]).get("perms") or [])


def test_PREF_04_group_perm_removed_from_group(sso_http, sso_admin, reset_user, seeded):
    """Removing app1 from the group itself affects every member on their next refresh."""
    reset_user("qa_wiki_mut_group")
    first = _login(sso_http, "qa_wiki_mut_group")
    sso_admin.call("group_revoke", D.APP_GROUP, D.APP_PERM)
    try:
        r = _refresh(sso_http, first["refresh_token"])
        assert r.status_code == 200
        assert D.APP_PERM not in (jwt_forge.payload(r.json()["access_token"]).get("perms") or [])
    finally:
        sso_admin.call("group_grant", D.APP_GROUP, D.APP_PERM)


def test_PREF_05_new_group_membership_on_refresh(sso_http, sso_admin, reset_user):
    reset_user("qa_wiki_mut_grant")
    first = _login(sso_http, "qa_wiki_mut_grant")
    sso_admin.call("add_group", "qa_wiki_mut_grant", D.APP_GROUP)
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 200
    assert D.APP_PERM in jwt_forge.payload(r.json()["access_token"])["perms"]


def test_PREF_06_deactivated_user_refresh_fails(sso_http, sso_admin, reset_user, service_auth):
    reset_user("qa_wiki_mut_deact")
    first = _login(sso_http, "qa_wiki_mut_deact")
    sso_admin.call("set_active", "qa_wiki_mut_deact", False)
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 403 and r.json().get("error") == "account_inactive", r.text
    intro = _introspect(sso_http, first["access_token"], service_auth)
    assert intro["active"] is False and not intro.get("perms"), intro


def test_PREF_07_superuser_flag_on_refresh(sso_http, sso_admin, reset_user):
    reset_user("qa_wiki_mut_grant")
    first = _login(sso_http, "qa_wiki_mut_grant")
    sso_admin.call("set_superuser", "qa_wiki_mut_grant", True)
    r = _refresh(sso_http, first["refresh_token"])
    assert r.status_code == 200
    assert D.APP_PERM in jwt_forge.payload(r.json()["access_token"])["perms"]
    sso_admin.call("set_superuser", "qa_wiki_mut_grant", False)
    r2 = _refresh(sso_http, r.json()["refresh_token"])
    assert D.APP_PERM not in (jwt_forge.payload(r2.json()["access_token"]).get("perms") or [])
