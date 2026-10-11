"""PERM-*: the SSO-side `perms` claim (design A1/A2 + v1.1 item 8). Talks to the SSO only (feat/app-permissions).

v1.1 item 8: `perms` is ALWAYS present, [] when none (Q-11); superusers get every application.* while
SSO_SUPERUSER_ALL_APPS is true (default), switchable on the SSO server (D-11/Q-17)."""
from __future__ import annotations

import pytest

import jwt_forge
import qasso_data as D
from conftest import sso_login

pytestmark = [pytest.mark.needs_sso, pytest.mark.needs_manage]

GRANTED = ["qa_wiki_direct", "qa_wiki_group", "qa_wiki_both", "qa_wiki_mixed"]
NOT_GRANTED = ["qa_wiki_none", "qa_wiki_staff", "qa_wiki_nonapp", "qa_wiki_app2"]


def _perms(obj):
    return obj.get("perms", None)


@pytest.mark.parametrize("username", GRANTED + NOT_GRANTED + ["qa_wiki_super", "qa_wiki_super_app1", "qa_wiki_many"])
def test_PERM_01_access_token_perms_claim(tokens, sso_admin, username):
    """A2: perms = sorted application.* perms (direct + group; superuser = all application.*)."""
    body = tokens(username)
    claim = _perms(jwt_forge.payload(body["access_token"]))
    if username in ("qa_wiki_super", "qa_wiki_super_app1"):
        expected = sso_admin.json("all_app_perms")
        assert D.APP_PERM in expected and D.OTHER_APP_PERM in expected
    else:
        expected = D.expected_perms(username)
    assert claim is not None, "v1.1 item 8: perms is always present ([] when none)"
    assert claim == expected, (username, claim, expected)
    assert claim == sorted(claim), "perms must be sorted"
    assert all(isinstance(p, str) and p.startswith("application.") for p in claim), "only application.* exported"


def test_PERM_02_direct_grant(tokens):
    assert D.APP_PERM in jwt_forge.payload(tokens("qa_wiki_direct")["access_token"])["perms"]


def test_PERM_03_group_grant(tokens):
    pl = jwt_forge.payload(tokens("qa_wiki_group")["access_token"])
    assert D.APP_PERM in pl["perms"] and D.APP_GROUP in pl.get("groups", [])


def test_PERM_04_direct_and_group_no_duplicates(tokens):
    assert jwt_forge.payload(tokens("qa_wiki_both")["access_token"])["perms"] == [D.APP_PERM]


def test_PERM_05_superuser_gets_every_application_perm(tokens, sso_admin):
    pl = jwt_forge.payload(tokens("qa_wiki_super")["access_token"])
    assert pl["perms"] == sso_admin.json("all_app_perms")


@pytest.mark.parametrize("username", NOT_GRANTED)
def test_PERM_06_no_grant_no_app1(tokens, username):
    """Staff flag, a non-application permission, or another app's permission never imply application.app1."""
    perms = _perms(jwt_forge.payload(tokens(username)["access_token"]))
    assert isinstance(perms, list) and D.APP_PERM not in perms, perms


def test_PERM_07_only_application_namespace_exported(tokens):
    for u in ("qa_wiki_nonapp", "qa_wiki_mixed", "qa_wiki_none"):
        perms = _perms(jwt_forge.payload(tokens(u)["access_token"])) or []
        assert D.NON_APP_PERM not in perms, (u, perms)
        assert all(p.startswith("application.") for p in perms)


@pytest.mark.parametrize("username", ["qa_wiki_inactive", "qa_wiki_inactive_super"])
def test_PERM_08_inactive_user_cannot_log_in(sso_http, seeded, username):
    """Inactive users (even with a grant / superuser) get 403 account_inactive and no tokens (A2: no perms)."""
    r = sso_login(sso_http, username)
    assert r.status_code == 403 and r.json().get("error") == "account_inactive", r.text
    assert "access_token" not in r.text


def test_PERM_09_inactive_user_computed_perms_empty(sso_admin, seeded):
    """A2 'inactive users get none' (Django get_all_permissions returns set() for inactive users)."""
    assert sso_admin.json("user_perms", "qa_wiki_inactive") == []
    assert sso_admin.json("user_perms", "qa_wiki_inactive_super") == []


@pytest.mark.parametrize("username", ["qa_wiki_direct", "qa_wiki_none", "qa_wiki_super"])
def test_PERM_10_login_response_user_object_has_perms(tokens, sso_admin, username):
    """A2: the `user` object in the login response carries perms too, equal to the token claim."""
    body = tokens(username)
    assert "user" in body, body.keys()
    assert body["user"].get("perms") == jwt_forge.payload(body["access_token"]).get("perms")


@pytest.mark.parametrize("username", ["qa_wiki_direct", "qa_wiki_group", "qa_wiki_none", "qa_wiki_super"])
def test_PERM_11_me_has_perms(sso_http, tokens, username):
    body = tokens(username)
    r = sso_http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 200, r.text
    me = r.json()
    me = me.get("user", me)
    assert me.get("perms") == jwt_forge.payload(body["access_token"]).get("perms"), me


@pytest.mark.parametrize("username", ["qa_wiki_direct", "qa_wiki_group", "qa_wiki_none", "qa_wiki_super"])
def test_PERM_12_introspect_has_perms(sso_http, tokens, service_auth, username):
    body = tokens(username)
    r = sso_http.post("/api/v1/auth/introspect", json={"token": body["access_token"]}, auth=service_auth)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["active"] is True
    assert j.get("perms") == jwt_forge.payload(body["access_token"]).get("perms"), j


def test_PERM_13_claim_format(tokens):
    """perms is a JSON array of strings (never a space-separated string, never null) in all three places."""
    body = tokens("qa_wiki_mixed")
    pl = jwt_forge.payload(body["access_token"])
    assert isinstance(pl["perms"], list) and all(isinstance(p, str) for p in pl["perms"])
    assert pl["perms"] == [D.APP_PERM, D.OTHER_APP_PERM]


def test_PERM_14_refresh_token_has_no_perms_or_same(tokens):
    """Refresh tokens are not authorization tokens. Record whether they carry perms (design silent: Q-12)."""
    pl = jwt_forge.payload(tokens("qa_wiki_direct")["refresh_token"])
    assert pl.get("token_type") == "refresh"
    if "perms" in pl:
        pytest.xfail("Q-12: refresh token carries perms; harmless only if nobody authorises on it")


def test_PERM_15_many_groups_token_size(tokens):
    """Size of the access token for a user with 40 groups + 2 perms (feeds COOKIE-06)."""
    tok = tokens("qa_wiki_many")["access_token"]
    pl = jwt_forge.payload(tok)
    assert len(pl.get("groups", [])) == len(D.MANY_GROUPS)
    print(f"qa_wiki_many access token: {len(tok)} bytes, refresh: {len(tokens('qa_wiki_many')['refresh_token'])} bytes")


@pytest.mark.parametrize("username", NOT_GRANTED[:2])
def test_PERM_16_empty_perms_is_empty_list_everywhere(sso_http, tokens, service_auth, username):
    """v1.1 item 8 (Q-11): [] (never absent, never null) in the token, the login user object, /me, /introspect."""
    body = tokens(username)
    expected = []
    assert jwt_forge.payload(body["access_token"])["perms"] == expected
    assert body["user"]["perms"] == expected
    me = sso_http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me.get("user", me)["perms"] == expected
    intro = sso_http.post("/api/v1/auth/introspect", json={"token": body["access_token"]}, auth=service_auth).json()
    assert intro["perms"] == expected


# ---------------------------------------------------------------- SSO_SUPERUSER_ALL_APPS (v1.1 item 8)
@pytest.fixture(scope="module")
def nosuper_http(cfg):
    if not cfg.sso_nosuper_url:
        pytest.skip("SSO_NOSUPER_URL not set (2nd SSO on the same DB with SSO_SUPERUSER_ALL_APPS=false)")
    import httpx
    with httpx.Client(base_url=cfg.sso_nosuper_url, timeout=30) as c:
        yield c


@pytest.mark.parametrize("username", ["qa_wiki_super", "qa_wiki_super_app1"])
def test_PERM_17_superuser_all_apps_on_default(tokens, sso_admin, username):
    assert jwt_forge.payload(tokens(username)["access_token"])["perms"] == sso_admin.json("all_app_perms")


@pytest.mark.parametrize("username,expected", [("qa_wiki_super", []),
                                               ("qa_wiki_super_app1", [D.APP_PERM]),
                                               ("qa_wiki_direct", [D.APP_PERM]),
                                               ("qa_wiki_mixed", [D.APP_PERM, D.OTHER_APP_PERM])])
def test_PERM_18_superuser_all_apps_off(nosuper_http, service_auth, seeded, username, expected):
    """SSO_SUPERUSER_ALL_APPS=false: superusers get only their explicit (user + group) grants; others unchanged.
    Consistent across token, login user object, /me and /introspect."""
    r = sso_login(nosuper_http, username)
    assert r.status_code == 200, r.text
    body = r.json()
    assert jwt_forge.payload(body["access_token"])["perms"] == expected
    assert body["user"]["perms"] == expected
    me = nosuper_http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me.get("user", me)["perms"] == expected
    intro = nosuper_http.post("/api/v1/auth/introspect", json={"token": body["access_token"]}, auth=service_auth).json()
    assert intro["perms"] == expected


def test_PERM_19_superuser_all_apps_off_refresh_consistent(nosuper_http, seeded):
    r = sso_login(nosuper_http, "qa_wiki_super")
    ref = nosuper_http.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert ref.status_code == 200
    assert jwt_forge.payload(ref.json()["access_token"])["perms"] == []


def test_PERM_20_superuser_without_grant_denied_at_llm_wiki_when_off(nosuper_http, wiki, seeded):
    """End to end: a superuser token from the flag-off SSO (same key/issuer) is 403 at llm-wiki."""
    import httpx
    tok = sso_login(nosuper_http, "qa_wiki_super").json()["access_token"]
    with httpx.Client(base_url=wiki.url, timeout=30) as c:
        assert c.get("/usage", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
