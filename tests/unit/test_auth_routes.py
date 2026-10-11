"""AUTH_MODE matrix, SSO_PROTECT_READS, /login, the browser session and /logout
(docs/sso-integration-design.md B3/B4/B7 and v1.1). The SSO is tests/sso_fake.FakeSSO over an
httpx.MockTransport.
"""

from __future__ import annotations

import logging
import re
import time

import pytest
from fastapi.testclient import TestClient
from tests.sso_fake import AUDIENCE, BASE_URL, ISSUER, PERM, FakeSSO, Key, access_claims

from llmwiki.api.app import create_app
from llmwiki.config import Settings

STATIC = "test-static-token-" + "s" * 16  # >= 24 chars: accepted in sso/both mode
CLIENT_ID = "llm-wiki"
CLIENT_SECRET = "wiki-client-secret-" + "c" * 20
BROWSER_IP = "203.0.113.9"


@pytest.fixture(scope="module")
def key() -> Key:
    return Key.generate()


@pytest.fixture
def sso(key) -> FakeSSO:
    return FakeSSO(key)


def make_settings(tmp_path, **over) -> Settings:
    base = dict(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake", local_storage_path=tmp_path / "data",
        embedding_dim=64, ingest_api_token=STATIC, sso_base_url=BASE_URL, sso_issuer=ISSUER,
        sso_audience=AUDIENCE, sso_client_id=CLIENT_ID, sso_client_secret=CLIENT_SECRET,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def build(tmp_path, monkeypatch, sso):
    """build(auth_mode=..., **settings) -> TestClient on https://testserver (Secure cookies)."""
    from llmwiki import config, factory, tools

    def _build(**over) -> TestClient:
        cfg = make_settings(tmp_path, **over)
        monkeypatch.setattr(config, "settings", cfg)
        monkeypatch.setattr("llmwiki.api.routes.settings", cfg)
        monkeypatch.setattr("llmwiki.tools.default_settings", cfg)
        monkeypatch.setattr("llmwiki.factory.default_settings", cfg)
        monkeypatch.setattr(tools, "auth_transport", sso.transport)
        tools.reset_auth()
        factory.reset()
        return TestClient(create_app(), base_url="https://testserver", follow_redirects=False,
                          client=(BROWSER_IP, 50000))

    yield _build
    tools.reset_auth()
    factory.reset()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def csrf_of(client: TestClient, path: str = "/login") -> str:
    page = client.get(path)
    match = re.search(r"name='csrf_token' value='([^']+)'", page.text)
    assert match, page.text
    return match.group(1)


def login(client: TestClient, username="alice", password="alice-pw", next_url="/dashboard",
          csrf: str | None = None):
    token = csrf if csrf is not None else csrf_of(client)
    return client.post("/login", data={"username": username, "password": password,
                                       "csrf_token": token, "next": next_url})


def set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def session_cookie(response) -> str:
    return next(h for h in set_cookie_headers(response) if h.startswith("llmwiki_session="))


def csrf_meta(client: TestClient) -> str:
    """The session's CSRF value from the dashboard's meta tag (design v1.1 item 3)."""
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', client.get("/dashboard").text)
    assert match
    return match.group(1)


def store_rows(tmp_path) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "data" / "sessions.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM sessions")]
    finally:
        conn.close()


# ---------------------------------------------------------------- startup
@pytest.mark.parametrize("missing", ["sso_base_url", "sso_issuer", "sso_client_id",
                                     "sso_client_secret", "sso_required_permission"])
@pytest.mark.parametrize("mode", ["sso", "both"])
def test_sso_mode_fails_fast_without_required_settings(build, missing, mode) -> None:
    with pytest.raises(RuntimeError, match=missing.upper()):
        build(auth_mode=mode, **{missing: ""})


@pytest.mark.parametrize("mode", ["sso", "both"])
@pytest.mark.parametrize("token,why", [("changeme", "changeme"), ("x" * 23, "shorter than 24"),
                                       ("", "shorter than 24")])
def test_weak_static_token_refused_in_sso_and_both(build, mode, token, why) -> None:
    """v1.1 item 6: weak = 'changeme' or shorter than 24 characters."""
    with pytest.raises(RuntimeError, match=why):
        build(auth_mode=mode, ingest_api_token=token)


def test_static_token_of_24_chars_is_accepted(build) -> None:
    assert build(auth_mode="sso", ingest_api_token="x" * 24).get("/login").status_code == 200


def test_static_mode_only_warns_about_a_weak_token_and_open_mcp(build, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="llmwiki.api.app")
    client = build(auth_mode="static", ingest_api_token="changeme")
    assert client.post("/lint", headers=bearer("changeme")).status_code == 200
    assert "MCP is unauthenticated in AUTH_MODE=static" in caplog.text
    assert "INGEST_API_TOKEN is the placeholder 'changeme'" in caplog.text


@pytest.mark.parametrize("path", ["/login", "/logout"])
def test_static_mode_needs_no_sso_settings_and_has_no_login_routes(build, path) -> None:
    """v1.1 item 5: no /login or /logout in static mode (404, GET and POST)."""
    client = build(auth_mode="static", sso_base_url="", sso_issuer="", sso_client_id="",
                   sso_client_secret="")
    assert client.get(path).status_code == 404
    assert client.post(path, data={"csrf_token": "x"}).status_code == 404
    assert client.post("/lint", headers=bearer(STATIC)).status_code == 200
    r = client.post("/lint")
    assert r.status_code == 401 and "www-authenticate" not in r.headers  # Phase 0 401, unchanged
    assert r.json() == {"detail": "invalid or missing bearer token"}


def test_static_mode_ignores_session_cookies_and_creates_no_store(build, tmp_path) -> None:
    client = build(auth_mode="static")
    r = client.get("/dashboard", headers={"Cookie": "llmwiki_session=whatever"})
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert not (tmp_path / "data" / "sessions.sqlite3").exists()


def test_jwks_url_defaults_from_the_base_url(tmp_path) -> None:
    cfg = make_settings(tmp_path, sso_base_url="https://sso.x/")
    assert cfg.effective_sso_jwks_url == "https://sso.x/.well-known/jwks.json"
    assert make_settings(tmp_path, sso_jwks_url="https://k/j").effective_sso_jwks_url == "https://k/j"


def test_healthz_reports_the_auth_mode(build) -> None:
    body = build(auth_mode="both").get("/healthz").json()
    assert body["config"]["auth"] == {"mode": "both", "sso_issuer": ISSUER,
                                      "required_permission": PERM, "protect_reads": False,
                                      "mcp_protected": True}


def test_healthz_in_static_mode_has_no_auth_block(build) -> None:
    """v1.1 item 5: the static-mode /healthz body is unchanged."""
    assert "auth" not in build(auth_mode="static").get("/healthz").json()["config"]


# ---------------------------------------------------------------- AUTH_MODE matrix (Bearer)
@pytest.mark.parametrize("mode,credential,status", [
    ("static", "static", 200), ("static", "sso", 401), ("static", "none", 401),
    ("sso", "static", 401), ("sso", "sso", 200), ("sso", "sso_noperm", 403), ("sso", "none", 401),
    ("both", "static", 200), ("both", "sso", 200), ("both", "sso_noperm", 403),
    ("both", "none", 401), ("both", "garbage", 401), ("sso", "expired", 401),
    ("both", "expired", 401),
])
def test_auth_mode_matrix_on_a_protected_route(build, key, mode, credential, status) -> None:
    client = build(auth_mode=mode)
    headers = {
        "static": bearer(STATIC),
        "sso": bearer(key.sign(access_claims())),
        "sso_noperm": bearer(key.sign(access_claims(perms=[]))),
        "expired": bearer(key.sign(access_claims(exp=int(time.time()) - 600))),
        "garbage": bearer("x.y.z"),
        "none": {},
    }[credential]
    r = client.post("/lint", headers=headers)
    assert r.status_code == status, r.text
    if status == 403:
        assert r.json() == {"detail": "forbidden", "error": "permission_denied"}
    if status == 401 and mode != "static":
        assert r.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_both_mode_falls_back_to_the_static_token_when_jwt_verification_fails(build) -> None:
    """v1.1 item 6: a JWT-shaped static token still works in both mode, not in sso mode."""
    jwt_shaped = "aaaaaaaaaa.bbbbbbbbbb.cccccccccc"
    assert build(auth_mode="both", ingest_api_token=jwt_shaped).post(
        "/lint", headers=bearer(jwt_shaped)).status_code == 200
    assert build(auth_mode="sso", ingest_api_token=jwt_shaped).post(
        "/lint", headers=bearer(jwt_shaped)).status_code == 401


def test_both_mode_no_fallback_for_a_valid_token_without_permission(build, key) -> None:
    client = build(auth_mode="both")
    no_perm = bearer(key.sign(access_claims(perms=[])))
    assert client.post("/lint", headers=no_perm).status_code == 403


def test_require_token_is_an_alias_of_require_auth() -> None:
    from llmwiki.api import routes

    assert routes.require_token is routes.require_auth


def test_sso_bearer_works_on_every_write_route_kind(build, key) -> None:
    client = build(auth_mode="sso")
    h = bearer(key.sign(access_claims()))
    assert client.put("/domains/notes", json={"description": "d"}, headers=h).status_code == 200
    assert client.get("/usage", headers=h).status_code == 200
    assert client.post("/ingest", json={"text": "hello world"}, headers=h).status_code == 200


# ---------------------------------------------------------------- SSO_PROTECT_READS
READS = ["/search?q=x", "/answer?q=x", "/concepts", "/page/nothing", "/domains", "/worker",
         "/sources/0123456789abcdef"]


@pytest.mark.parametrize("path", READS)
def test_reads_are_open_by_default(build, path) -> None:
    assert build(auth_mode="sso").get(path).status_code not in (401, 403)


@pytest.mark.parametrize("path", READS)
def test_protect_reads_requires_auth(build, key, path) -> None:
    client = build(auth_mode="sso", sso_protect_reads=True)
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bearer(key.sign(access_claims(perms=[])))).status_code == 403
    assert client.get(path, headers=bearer(key.sign(access_claims()))).status_code not in (401, 403)


def test_protect_reads_keeps_healthz_and_login_public(build) -> None:
    client = build(auth_mode="sso", sso_protect_reads=True)
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200


def test_protect_reads_in_static_mode_uses_the_static_token(build) -> None:
    client = build(auth_mode="static", sso_protect_reads=True)
    assert client.get("/concepts").status_code == 401
    assert client.get("/concepts", headers=bearer(STATIC)).status_code == 200


def test_protect_reads_with_a_session(build) -> None:
    client = build(auth_mode="sso", sso_protect_reads=True)
    assert login(client).status_code == 303
    assert client.get("/concepts").status_code == 200


# ---------------------------------------------------------------- login page
def test_login_page_renders_a_form_with_csrf(build) -> None:
    client = build(auth_mode="sso")
    r = client.get("/login?next=/dashboard")
    assert r.status_code == 200 and "<form class='login'" in r.text
    assert "name='username'" in r.text and "type='password'" in r.text
    assert "value='/dashboard'" in r.text and r.headers["cache-control"] == "no-store"
    csrf_header = next(h for h in set_cookie_headers(r) if h.startswith("llmwiki_login_csrf="))
    assert "SameSite=strict" in csrf_header and "HttpOnly" in csrf_header
    assert "Secure" in csrf_header
    value = csrf_header.split(";")[0].split("=", 1)[1]
    assert f"value='{value}'" in r.text  # double submit: cookie == hidden field


def test_login_success_sets_the_session_and_redirects(build, sso) -> None:
    client = build(auth_mode="sso")
    r = login(client, next_url="/dashboard?month=2026-10")
    assert r.status_code == 303 and r.headers["location"] == "/dashboard?month=2026-10"
    assert sso.calls[0] == ("/api/v1/auth/login",
                            {"username": "alice", "password": "alice-pw", "client": "native",
                             "client_ip": BROWSER_IP})
    import base64

    expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert sso.auth_headers[0] == f"Basic {expected}"  # ServiceClient Basic auth (v1.1 item 4)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Signed in as <strong>alice</strong>" in page.text and "Log out" in page.text
    assert page.headers["cache-control"] == "no-store"
    # the login double-submit cookie is cleared once signed in
    assert any(h.startswith('llmwiki_login_csrf=""') for h in set_cookie_headers(r))


def test_login_accepts_an_email_style_identifier_and_passes_it_through(build, sso) -> None:
    sso.users["alice@example.test"] = sso.users["alice"]
    client = build(auth_mode="sso")
    assert login(client, username="alice@example.test").status_code == 303


def test_session_cookie_is_an_opaque_id(build, sso, tmp_path) -> None:
    """v1.1 item 2: the cookie holds only a random 256-bit id; tokens stay server-side."""
    client = build(auth_mode="sso")
    header = session_cookie(login(client))
    attrs = [a.strip() for a in header.split(";")]
    assert "HttpOnly" in attrs and "Secure" in attrs
    assert "SameSite=lax" in attrs and "Path=/" in attrs
    max_age = int(next(a for a in attrs if a.startswith("Max-Age=")).split("=")[1])
    assert 168 * 3600 - 5 <= max_age <= 168 * 3600  # the absolute session lifetime
    value = attrs[0].split("=", 1)[1]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", value)  # token_urlsafe(32) = 256 bits
    rows = store_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    import hashlib

    assert row["id_hash"] == hashlib.sha256(value.encode()).hexdigest() and value not in str(row)
    assert row["username"] == "alice" and row["sub"] == "42"
    assert row["refresh"] in sso.live_refresh and row["access"].startswith("eyJ")
    assert set(row) == {"id_hash", "username", "sub", "access", "refresh", "access_exp", "csrf",
                        "created_at", "last_seen"}
    assert ((tmp_path / "data" / "sessions.sqlite3").stat().st_mode & 0o777) == 0o600


def test_session_db_path_is_configurable(build, tmp_path) -> None:
    client = build(auth_mode="sso", session_db_path=tmp_path / "elsewhere" / "s.db")
    login(client)
    assert (tmp_path / "elsewhere" / "s.db").exists()


def test_session_max_age_hours_sets_the_cookie_lifetime(build) -> None:
    header = session_cookie(login(build(auth_mode="sso", session_max_age_hours=2)))
    assert "Max-Age=7200" in header or "Max-Age=7199" in header


def test_cookie_secure_flag_can_be_disabled_for_local_http(build) -> None:
    client = build(auth_mode="sso", session_cookie_secure=False)
    assert "Secure" not in session_cookie(login(client))


@pytest.mark.parametrize("user,password,status,message", [
    ("alice", "wrong", 401, "Wrong username or password"),
    ("nobody", "nobody-pw", 401, "Wrong username or password"),
    ("carol", "carol-pw", 403, "Please activate your account"),
    ("bob", "bob-pw", 403, "doesn&#x27;t have access to llm-wiki (application.app1)"),
])
def test_login_failures(build, sso, user, password, status, message) -> None:
    client = build(auth_mode="sso")
    r = login(client, user, password)
    assert r.status_code == status and message in r.text
    assert not any(h.startswith("llmwiki_session=") for h in set_cookie_headers(r))
    assert f"value='{user}'" in r.text  # username kept, password never echoed
    assert password not in r.text.replace(f"value='{user}'", "")


def test_no_permission_discards_the_refresh_token_at_the_sso(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client, "bob", "bob-pw")
    assert sso.paths() == ["/api/v1/auth/login", "/.well-known/jwks.json", "/api/v1/auth/logout"]
    assert sso.live_refresh == {}


def test_rate_limited_login_shows_retry_after(build, sso) -> None:
    sso.rate_limited = 42
    r = login(build(auth_mode="sso"))
    assert r.status_code == 429 and "try again in 42 s" in r.text
    assert r.headers["retry-after"] == "42"


def test_sso_down_is_a_503_page(build, sso) -> None:
    """v1.2 C-14: SSO unreachable -> 503 everywhere, login included."""
    client = build(auth_mode="sso")
    token = csrf_of(client)
    sso.down = True
    r = login(client, csrf=token)
    assert r.status_code == 503 and "Login service unavailable" in r.text
    assert r.headers["retry-after"] == "30"


def test_sso_5xx_is_a_503_page(build, sso, monkeypatch) -> None:
    import httpx

    client = build(auth_mode="sso")
    token = csrf_of(client)
    monkeypatch.setattr(sso, "handle", lambda request: httpx.Response(502, text="bad gateway"))
    from llmwiki import tools

    monkeypatch.setattr(tools, "auth_transport", sso.transport)
    tools.reset_auth()
    assert login(client, csrf=token).status_code == 503


def test_sso_token_with_a_wrong_issuer_is_a_503_misconfigured(build, sso, caplog) -> None:
    sso.issuer = "https://impostor.example"
    r = login(build(auth_mode="sso"))
    assert r.status_code == 503 and "Login service misconfigured" in r.text
    assert not any(h.startswith("llmwiki_session=") for h in set_cookie_headers(r))


def test_wrong_service_credentials_are_a_503_misconfigured(build, sso, caplog) -> None:
    """v1.2 C-19: the SSO answers 401 invalid_client -> error log + 503 'misconfigured'."""
    sso.client_secret = "the-real-secret"
    r = login(build(auth_mode="sso"))
    assert r.status_code == 503 and "Login service misconfigured" in r.text
    assert any(rec.levelname == "ERROR" and "invalid_client" in rec.getMessage()
               for rec in caplog.records)
    assert CLIENT_SECRET not in caplog.text


def test_wrong_service_credentials_during_refresh_keep_the_session(build, sso, tmp_path) -> None:
    sso.access_ttl = -5
    client = build(auth_mode="sso")
    login(client)
    sso.client_secret = "rotated-at-the-sso"
    r = client.get("/usage")
    assert r.status_code == 503 and r.json() == {"detail": "Login service misconfigured"}
    page = client.get("/dashboard")
    assert page.status_code == 503 and "Login service misconfigured" in page.text
    assert len(store_rows(tmp_path)) == 1  # kept


def test_empty_fields_are_a_400(build) -> None:
    assert login(build(auth_mode="sso"), "", "").status_code == 400


@pytest.mark.parametrize("csrf", ["", "forged-value-" + "z" * 40])
def test_login_csrf_failure(build, sso, csrf) -> None:
    client = build(auth_mode="sso")
    csrf_of(client)  # sets the cookie; the form value below does not match it
    r = login(client, csrf=csrf)
    assert r.status_code == 403 and "expired" in r.text
    assert sso.calls == []  # the SSO is never called


def test_login_csrf_failure_without_cookie(build, sso) -> None:
    client = build(auth_mode="sso")
    r = client.post("/login", data={"username": "alice", "password": "alice-pw",
                                    "csrf_token": "a" * 43})
    assert r.status_code == 403 and sso.calls == []


@pytest.mark.parametrize("target", [
    "https://evil.example/", "//evil.example/x", "/\\evil.example", "http:/evil",
    "javascript:alert(1)",
    "evil", "/login", "/logout", "/%0d%0aSet-Cookie:x", "\\\\evil", "/ok\r\nX: y",
])
def test_open_redirects_are_rejected(build, target) -> None:
    from llmwiki.api.login import safe_next

    assert safe_next(target) in ("/dashboard", target) and not safe_next(target).startswith("//")
    if target.startswith(("/%0d", )):
        return  # an encoded path on this origin is harmless
    r = login(build(auth_mode="sso"), next_url=target)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"


def test_relative_next_is_kept(build) -> None:
    from llmwiki.api.login import safe_next

    assert safe_next("/concepts?prefix=a") == "/concepts?prefix=a"
    assert safe_next(None) == "/dashboard"


def test_signed_in_user_visiting_login_goes_to_next(build) -> None:
    client = build(auth_mode="sso")
    login(client)
    r = client.get("/login?next=/concepts")
    assert r.status_code == 303 and r.headers["location"] == "/concepts"


# ---------------------------------------------------------------- dashboard
def test_unauthenticated_browser_is_redirected_to_login(build) -> None:
    r = build(auth_mode="sso").get("/dashboard?month=2026-10")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fdashboard%3Fmonth%3D2026-10"


def test_sso_mode_ignores_the_token_query_parameter(build) -> None:
    r = build(auth_mode="sso").get("/dashboard", params={"token": STATIC})
    assert r.status_code == 303  # not accepted: tokens stay out of URLs


def test_both_mode_still_accepts_the_token_query_parameter(build) -> None:
    client = build(auth_mode="both")
    assert client.get("/dashboard", params={"token": STATIC}).status_code == 200
    assert client.get("/dashboard", params={"token": "wrong"}).status_code == 401


def test_dashboard_accepts_an_sso_bearer(build, key) -> None:
    client = build(auth_mode="sso")
    assert client.get("/dashboard", headers=bearer(key.sign(access_claims()))).status_code == 200
    assert client.get("/dashboard",
                      headers=bearer(key.sign(access_claims(perms=[])))).status_code == 403


def test_forged_session_cookie_is_cleared(build) -> None:
    client = build(auth_mode="sso")
    client.cookies.set("llmwiki_session", "forged.value.sig", domain="testserver.local")
    r = client.get("/dashboard", headers={"Cookie": "llmwiki_session=forged.value"})
    assert r.status_code == 303
    assert any(h.startswith('llmwiki_session=""') and "Max-Age=0" in h
               for h in set_cookie_headers(r))


def test_json_write_with_a_session_needs_the_csrf_header(build) -> None:
    """v1.1 item 3: cookie-authenticated non-GET requests need X-CSRF-Token."""
    client = build(auth_mode="sso")
    login(client)
    r = client.post("/lint")
    assert r.status_code == 403 and r.json() == {"error": "csrf_failed"}  # v1.2 Q-30
    assert client.post("/lint", headers={"X-CSRF-Token": "x" * 43}).json() == {
        "error": "csrf_failed"}
    csrf = csrf_meta(client)
    assert client.post("/lint", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.put("/domains/notes", json={"description": "d"},
                      headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/usage").status_code == 200  # GET needs no CSRF header


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_csrf_applies_to_post_put_patch_delete(build, method) -> None:
    """v1.2 C-13: exactly these methods need the header (with a cookie session)."""
    from fastapi import Depends

    from llmwiki.api import routes

    client = build(auth_mode="sso")
    client.app.add_api_route("/_probe", lambda: {"ok": True}, methods=[method, "GET", "HEAD",
                                                                       "OPTIONS"],
                             dependencies=[Depends(routes.require_auth)])
    login(client)
    csrf = csrf_meta(client)
    assert client.request(method, "/_probe").status_code == 403
    assert client.request(method, "/_probe", headers={"X-CSRF-Token": csrf}).status_code == 200
    for safe in ("GET", "HEAD", "OPTIONS"):
        assert client.request(safe, "/_probe").status_code == 200, safe


def test_invalid_bearer_is_401_even_with_a_valid_session(build) -> None:
    """v1.2 Q-31: an explicit Bearer is never overridden by the cookie."""
    client = build(auth_mode="both")
    login(client)
    csrf = csrf_meta(client)
    for path, method in (("/usage", "GET"), ("/lint", "POST"), ("/dashboard", "GET")):
        r = client.request(method, path, headers={**bearer("wrong-token"), "X-CSRF-Token": csrf})
        assert r.status_code == 401, path
    assert client.get("/usage").status_code == 200  # the session itself still works


def test_bearer_requests_are_exempt_from_csrf(build, key) -> None:
    client = build(auth_mode="both")
    login(client)  # a session cookie is present too, but the Bearer authenticates
    assert client.post("/lint", headers=bearer(key.sign(access_claims()))).status_code == 200
    assert client.post("/lint", headers=bearer(STATIC)).status_code == 200


def test_the_csrf_value_is_per_session(build) -> None:
    client = build(auth_mode="sso")
    login(client)
    first = csrf_meta(client)
    client.cookies.clear()
    login(client)
    assert csrf_meta(client) != first


# ---------------------------------------------------------------- refresh
def test_session_refresh_rotates_both_tokens_server_side(build, sso, tmp_path) -> None:
    sso.access_ttl = 30  # expires within the 60 s margin -> refresh on the next request
    client = build(auth_mode="sso")
    login(client)
    before = store_rows(tmp_path)[0]
    old_refresh = next(iter(sso.live_refresh))
    sso.access_ttl = 900
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "/api/v1/auth/refresh" in sso.paths()
    i = sso.paths().index("/api/v1/auth/refresh")
    assert sso.calls[i][1] == {"refresh_token": old_refresh, "client_ip": BROWSER_IP}
    assert sso.auth_headers[i].startswith("Basic ")
    assert "set-cookie" not in r.headers  # the cookie (session id) does not change
    after = store_rows(tmp_path)[0]
    assert after["refresh"] != before["refresh"] and after["access"] != before["access"]
    assert after["created_at"] == before["created_at"]  # no sliding lifetime
    assert old_refresh not in sso.live_refresh and len(sso.live_refresh) == 1
    sso.calls.clear()
    assert client.get("/dashboard").status_code == 200
    assert "/api/v1/auth/refresh" not in sso.paths()  # fresh token now: no refresh


def test_refresh_picks_up_a_revoked_permission(build, sso) -> None:
    sso.access_ttl = 30
    client = build(auth_mode="sso")
    login(client)
    sso.users["alice"].perms = []
    r = client.get("/dashboard")
    assert r.status_code == 403 and "doesn&#x27;t have access" in r.text
    assert any(h.startswith('llmwiki_session=""') for h in set_cookie_headers(r))
    assert sso.live_refresh == {}  # the rotated refresh token was discarded too
    assert client.get("/usage").status_code == 401  # cookie gone


def test_refresh_permission_revoked_on_a_json_route_is_403(build, sso, tmp_path) -> None:
    sso.access_ttl = 30
    client = build(auth_mode="sso")
    login(client)
    sso.users["alice"].perms = []
    assert client.post("/lint").status_code == 403
    assert store_rows(tmp_path) == []  # session deleted (v1.1 Q-06)


def test_refresh_failure_clears_the_session_and_redirects(build, sso, tmp_path) -> None:
    sso.access_ttl = 30
    client = build(auth_mode="sso")
    login(client)
    sso.live_refresh.clear()  # e.g. logout-all at the SSO
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert any(h.startswith('llmwiki_session=""') for h in set_cookie_headers(r))
    assert store_rows(tmp_path) == []


def test_refresh_failure_on_a_json_route_is_401(build, sso) -> None:
    sso.access_ttl = 30
    client = build(auth_mode="sso")
    login(client)
    sso.live_refresh.clear()
    r = client.get("/usage")
    assert r.status_code == 401 and r.headers["www-authenticate"]


def test_sso_down_during_refresh_keeps_a_still_valid_session(build, sso) -> None:
    sso.access_ttl = 30
    client = build(auth_mode="sso")
    login(client)
    sso.down = True
    r = client.get("/dashboard")
    assert r.status_code == 200  # access token still valid for ~30 s
    assert not any(h.startswith("llmwiki_session=") for h in set_cookie_headers(r))


def test_sso_down_with_an_expired_access_token_is_503_and_keeps_the_session(build, sso,
                                                                            tmp_path) -> None:
    """v1.1 item 7 (Q-05): 503 JSON / 503 HTML, session kept, usable again once the SSO is back."""
    sso.access_ttl = -5
    client = build(auth_mode="sso")
    login(client)  # SSO accepted the login; the token is already within the leeway
    sso.down = True
    r = client.get("/usage")
    assert r.status_code == 503 and r.json() == {"detail": "Login service unavailable"}
    assert not any(h.startswith("llmwiki_session=") for h in set_cookie_headers(r))
    page = client.get("/dashboard")
    assert page.status_code == 503 and "Login service unavailable" in page.text
    assert page.headers["retry-after"] == "30"
    assert len(store_rows(tmp_path)) == 1
    sso.down = False
    sso.access_ttl = 900
    assert client.get("/dashboard").status_code == 200


# ---------------------------------------------------------------- logout
def test_logout_calls_the_sso_and_clears_the_cookie(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client)
    refresh_token = next(iter(sso.live_refresh))
    page = client.get("/dashboard")
    csrf = re.search(r"name='csrf_token' value='([^']+)'", page.text).group(1)
    r = client.post("/logout", data={"csrf_token": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert ("/api/v1/auth/logout", {"refresh_token": refresh_token}) in sso.calls
    assert refresh_token not in sso.live_refresh
    assert any(h.startswith('llmwiki_session=""') and "Max-Age=0" in h
               for h in set_cookie_headers(r))
    assert client.get("/dashboard").status_code == 303


def test_logout_needs_csrf(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client)
    r = client.post("/logout", data={"csrf_token": "nope"})
    assert r.status_code == 403
    assert "/api/v1/auth/logout" not in sso.paths()
    assert client.get("/dashboard").status_code == 200  # still signed in


def test_logout_survives_an_sso_outage(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client)
    csrf = re.search(r"name='csrf_token' value='([^']+)'", client.get("/dashboard").text).group(1)
    sso.down = True
    r = client.post("/logout", data={"csrf_token": csrf})
    assert r.status_code == 303  # cookie cleared locally; the refresh token expires on its own


# ---------------------------------------------------------------- logs
def test_no_tokens_or_passwords_in_logs(build, sso, key, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    sso.access_ttl = 30
    client = build(auth_mode="both")
    login(client)
    issued = list(sso.live_refresh)
    sso.access_ttl = 900
    client.get("/dashboard")
    issued += list(sso.live_refresh)
    jwt_bearer = key.sign(access_claims())
    client.post("/lint", headers=bearer(jwt_bearer))
    client.post("/lint", headers=bearer(STATIC))
    client.post("/lint", headers=bearer("wrong-static-token-value"))
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "require_auth:" in logged and "login ok: alice" in logged
    for secret in [*issued, jwt_bearer, STATIC, "alice-pw", CLIENT_SECRET]:
        assert secret not in logged
    assert not re.search(r"eyJ[\w-]{20,}\.[\w-]{20,}", logged)


# ---------------------------------------------------------------- static mode unchanged (v1.1 #5)
def test_static_mode_openapi_has_no_new_security_or_descriptions(build) -> None:
    schema = build(auth_mode="static").get("/openapi.json").json()
    for path in ("/search", "/answer", "/concepts", "/domains", "/worker"):
        assert "security" not in schema["paths"][path]["get"], path
    assert "/login" not in schema["paths"] and "/logout" not in schema["paths"]
    assert schema["paths"]["/dashboard"]["get"]["description"].startswith(
        "The usage dashboard (Phase 2). Bearer header, or ``?token=`` for a browser.\n\n"
        "The same static token as every other protected route;")


def test_static_mode_dashboard_has_no_session_markup(build) -> None:
    page = build(auth_mode="static").get("/dashboard", params={"token": STATIC})
    assert page.status_code == 200
    assert "csrf-token" not in page.text and "Signed in as" not in page.text
    assert "</style></head><body>\n<h1>" in page.text


# ---------------------------------------------------------------- Cache-Control (QA Q-20)
@pytest.mark.parametrize("mode", ["sso", "both"])
def test_login_and_dashboard_responses_are_no_store(build, sso, key, mode) -> None:
    client = build(auth_mode=mode)
    good = bearer(key.sign(access_claims()))
    responses = {
        "GET /login": client.get("/login"),
        "GET /dashboard (redirect)": client.get("/dashboard"),
        "POST /login csrf error": client.post("/login", data={"csrf_token": "x"}),
        "POST /login empty": login(client, "", ""),
        "POST /login wrong password": login(client, "alice", "wrong"),
        "POST /login no permission": login(client, "bob", "bob-pw"),
        "POST /login success (redirect)": login(client),
        "GET /login signed in (redirect)": client.get("/login?next=/concepts"),
        "GET /dashboard": client.get("/dashboard"),
        "GET /dashboard bearer": client.get("/dashboard", headers=good),
        "GET /dashboard bad bearer": client.get("/dashboard", headers=bearer("nope")),
    }
    csrf = csrf_meta(client)
    responses["POST /logout"] = client.post("/logout", data={"csrf_token": csrf})
    sso.down = True
    responses["POST /login SSO down"] = login(client)
    for name, r in responses.items():
        assert r.headers.get_list("cache-control") == ["no-store"], (name, r.status_code)


def test_static_mode_dashboard_headers_are_unchanged(build) -> None:
    """Static /dashboard keeps its Phase 0 headers: no Cache-Control added (byte-for-byte rule)."""
    client = build(auth_mode="static")
    for r in (client.get("/dashboard", params={"token": STATIC}), client.get("/dashboard")):
        assert "cache-control" not in r.headers


def test_other_routes_are_not_forced_to_no_store(build) -> None:
    assert "cache-control" not in build(auth_mode="sso").get("/concepts").headers
