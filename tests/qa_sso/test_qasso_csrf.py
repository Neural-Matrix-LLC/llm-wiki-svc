"""CSRF-*: login form double-submit (design B4, CSRF-01..07/10), logout form (CSRF-08/09) and, since v1.1 item 3,
X-CSRF-Token on every cookie-authenticated non-GET request (CSRF-11..22; D-06 / Q-04 resolved).

v1.1 item 3: "Any non-GET request authenticated by the session cookie must send X-CSRF-Token equal to the
session's csrf value (exposed to the dashboard page in a meta tag); forms use a hidden field. Bearer-
authenticated requests are exempt." v1.2: applies to POST/PUT/PATCH/DELETE only (C-13); failure -> 403
{"error":"csrf_failed"} (Q-30). Anything that reaches the handler (2xx/404/422) is a fail."""
from __future__ import annotations

import pytest

import qasso_data as D
from conftest import access_log_lines
from wiki_client import logout_form

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]


def _login_data(form, user="qa_wiki_direct", pw=D.PASSWORD):
    data = dict(form.fields)
    data[form.user_field or "username"] = user
    data[form.password_field] = pw
    return data


def _csrf_field(form):
    cands = [k for k in form.fields if k != form.user_field and "next" not in k.lower()]
    assert cands, form.fields
    return cands[0]


def _rejected(b, r):
    assert r.status_code in (400, 403), r.status_code
    assert b.session_value is None


def test_CSRF_01_missing_field(wiki, browser_factory, seeded, cfg):
    b = browser_factory(wiki)
    _, form = b.open_login()
    data = _login_data(form)
    data.pop(_csrf_field(form))
    _rejected(b, b.post_form("/login", data))


def test_CSRF_02_missing_cookie(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    _, form = b.open_login()
    for name in b.csrf_cookie_names():
        b.jar.pop(name)
    _rejected(b, b.post_form("/login", _login_data(form)))


def test_CSRF_03_mismatch(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    _, form = b.open_login()
    data = _login_data(form)
    data[_csrf_field(form)] = data[_csrf_field(form)][::-1] + "x"
    _rejected(b, b.post_form("/login", data))


def test_CSRF_04_pair_from_two_browsers(wiki, browser_factory, seeded):
    a, b = browser_factory(wiki), browser_factory(wiki)
    _, form_a = a.open_login()
    b.open_login()
    _rejected(b, b.post_form("/login", _login_data(form_a)))


def test_CSRF_05_both_empty(wiki, browser_factory, seeded):
    b = browser_factory(wiki)
    _, form = b.open_login()
    data = _login_data(form)
    data[_csrf_field(form)] = ""
    for name in b.csrf_cookie_names():
        b.jar[name] = ""
    _rejected(b, b.post_form("/login", data))


def test_CSRF_06_rejected_login_never_reaches_sso(wiki, browser_factory, seeded, cfg):
    before = access_log_lines(cfg, "POST /api/v1/auth/login")
    b = browser_factory(wiki)
    _, form = b.open_login()
    data = _login_data(form)
    data.pop(_csrf_field(form))
    b.post_form("/login", data)
    assert access_log_lines(cfg, "POST /api/v1/auth/login") == before


def test_CSRF_07_token_is_random_and_long(wiki, browser_factory):
    vals = []
    for _ in range(3):
        b = browser_factory(wiki)
        _, form = b.open_login()
        vals.append(form.fields[_csrf_field(form)])
    assert len(set(vals)) == 3 and all(len(v) >= 22 for v in vals), vals


def test_CSRF_08_logout_without_token_keeps_session(wiki, logged_in):
    b = logged_in("qa_wiki_logout")
    form = logout_form(b.get("/dashboard").text)
    assert form is not None
    data = dict(form.fields)
    for k in list(data):
        data.pop(k)
    r = b.post_form("/logout", data)
    assert r.status_code in (400, 403)
    assert b.get("/dashboard").status_code == 200, "a forged logout must not end the session"


def test_CSRF_09_logout_by_get_not_possible(wiki, logged_in):
    b = logged_in("qa_wiki_logout")
    r = b.get("/logout")
    assert r.status_code in (404, 405), r.status_code
    assert b.get("/dashboard").status_code == 200


def test_CSRF_10_cookie_cookie_flags(wiki, browser_factory):
    from wiki_client import parse_set_cookies
    b = browser_factory(wiki)
    r = b.get("/login")
    csrf = [c for c in parse_set_cookies(r) if c.name != "llmwiki_session"]
    assert csrf, "CSRF cookie expected"
    assert csrf[0].attrs.get("samesite", "").lower() == "strict", csrf[0].raw


# ================================================================ v1.1 item 3: X-CSRF-Token on cookie writes
from matrix import MCP, PROTECTED  # noqa: E402

WRITES = [p for p in PROTECTED if p.method != "GET" and not p.deny_only]
ALL_WRITES = [p for p in PROTECTED if p.method != "GET"]
REJECT = (403,)   # v1.2 Q-30
CSRF_BODY = {"error": "csrf_failed"}


def _csrf_failed(r, ctx=""):
    assert r.status_code == 403, (ctx, r.status_code, r.text[:200])
    assert r.json() == CSRF_BODY, (ctx, r.text[:200])


def _cookie_write(b, probe, csrf=None, extra_headers=None):
    kw = probe.kw()
    headers = {"Accept": "application/json"} | kw.pop("headers", {}) | (extra_headers or {})
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return b.request(probe.method, probe.path, headers=headers, **kw)


@pytest.fixture
def signed_in(wiki, logged_in):
    b = logged_in("qa_wiki_direct")
    return b, b.session_csrf()


@pytest.mark.parametrize("probe", ALL_WRITES, ids=lambda p: p.id)
def test_CSRF_11_cookie_write_without_header_rejected(signed_in, probe):
    """Includes the deny-only routes (/worker/resume, /synthesize): they are only ever sent WITHOUT a token."""
    b, _ = signed_in
    r = _cookie_write(b, probe)
    _csrf_failed(r, probe.id)


@pytest.mark.parametrize("probe", WRITES, ids=lambda p: p.id)
def test_CSRF_12_cookie_write_wrong_token_rejected(signed_in, probe):
    b, csrf = signed_in
    for bad in (csrf[::-1] + "x", csrf[:-1], "A" * len(csrf)):
        r = _cookie_write(b, probe, bad)
        _csrf_failed(r, (probe.id, bad[:8]))


@pytest.mark.parametrize("probe", WRITES, ids=lambda p: p.id)
def test_CSRF_13_token_from_another_session_rejected(wiki, browser_factory, seeded, probe):
    """Per-session token: user B's (or the same user's other session's) csrf value does not work with A's cookie."""
    a, other_user, same_user = browser_factory(wiki), browser_factory(wiki), browser_factory(wiki)
    a.login("qa_wiki_direct", D.PASSWORD)
    other_user.login("qa_wiki_group", D.PASSWORD)
    same_user.login("qa_wiki_direct", D.PASSWORD)
    for foreign in (other_user.session_csrf(), same_user.session_csrf()):
        assert foreign != a.session_csrf()
        r = _cookie_write(a, probe, foreign)
        _csrf_failed(r, probe.id)


@pytest.mark.parametrize("probe", WRITES, ids=lambda p: p.id)
def test_CSRF_14_cookie_write_with_correct_token_authorised(signed_in, probe):
    b, csrf = signed_in
    r = _cookie_write(b, probe, csrf)
    assert r.status_code == probe.allowed_status, (probe.id, r.status_code, r.text[:200])


@pytest.mark.parametrize("probe", WRITES, ids=lambda p: p.id)
@pytest.mark.parametrize("cred", ["static_ok", "sso_direct"])
def test_CSRF_15_bearer_writes_exempt(wiki, probe, cred, tokens, cfg, jwks, signing_key, seeded):
    """Bearer-authenticated requests need no X-CSRF-Token (static token only where the mode accepts it)."""
    import httpx

    from creds import build
    if cred == "static_ok" and wiki.mode == "sso":
        pytest.skip("static token not accepted in sso mode")
    kwargs = build(cred, tokens=tokens, cfg=cfg, jwks=jwks, signing_key_or_none=signing_key)
    kw = probe.kw()
    headers = {"Accept": "application/json"} | kw.pop("headers", {}) | kwargs.get("headers", {})
    with httpx.Client(base_url=wiki.url, timeout=30) as c:
        r = c.request(probe.method, probe.path, headers=headers, **kw)
    assert r.status_code == probe.allowed_status, (probe.id, cred, r.status_code)


def test_CSRF_16_cookie_get_needs_no_token(signed_in):
    b, _ = signed_in
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 200


def test_CSRF_17_meta_token_per_session_not_in_cookie(wiki, browser_factory, seeded):
    a, b = browser_factory(wiki), browser_factory(wiki)
    a.login("qa_wiki_direct", D.PASSWORD)
    b.login("qa_wiki_direct", D.PASSWORD)
    ta, tb = a.session_csrf(), b.session_csrf()
    assert ta != tb and len(ta) >= 22, (ta, tb)
    assert ta not in (a.session_value or "") and a.session_csrf() == ta, "stable for the session, not in the cookie"


@pytest.mark.parametrize("probe", WRITES[:3], ids=lambda p: p.id)
def test_CSRF_18_invalid_bearer_plus_cookie_not_authorised(signed_in, probe):
    """Bearer exemption must not combine with cookie auth: an invalid Bearer + a valid cookie and no csrf header
    is never authorised (expected 401: the explicit Bearer decides, Q-31)."""
    b, _ = signed_in
    r = _cookie_write(b, probe, extra_headers={"Authorization": "Bearer not-a-valid-token-at-all-0123456789"})
    assert r.status_code in (401, 403), (probe.id, r.status_code)
    assert r.status_code == 401, "Q-31: invalid explicit Bearer expected to win (401)"


def test_CSRF_19_cross_site_multipart_upload_rejected(signed_in):
    """The classic form-CSRF shape: a cross-site multipart POST with the cookie (SameSite=Lax blocks it in
    browsers; the token check must block it regardless)."""
    b, _ = signed_in
    r = b.request("POST", "/upload", files={"file": ("x.md", b"# qa csrf", "text/markdown")},
                  headers={"Origin": "https://evil.example", "Accept": "application/json"})
    _csrf_failed(r)


def test_CSRF_20_empty_header_rejected(signed_in):
    b, _ = signed_in
    r = _cookie_write(b, WRITES[0], "")
    _csrf_failed(r)


def test_CSRF_21_cookie_mcp_post_without_token_rejected(cfg, browser_factory, seeded):
    """MCP is auth-wrapped in sso/both (v1.1 item 1). Developer's choice: /mcp is Bearer-only, so a session cookie
    alone is rejected (401 + Bearer challenge) - there is then no cookie/CSRF surface on /mcp at all."""
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    b = browser_factory(inst)
    b.login("qa_wiki_direct", D.PASSWORD)
    for csrf in (None, b.session_csrf()):     # even WITH the right csrf value: the cookie is not a credential there
        r = _cookie_write(b, MCP[0], csrf)
        assert r.status_code == 401, (csrf is not None, r.status_code, r.text[:200])
        assert r.headers.get("www-authenticate", "").startswith("Bearer"), r.headers


def test_CSRF_22_token_as_form_field_on_api_write_not_accepted(signed_in):
    """Q-30 follow-up (QA judgment, not stated in v1.2): on API writes the token must come in X-CSRF-Token; a
    csrf form field in a multipart /upload is not a substitute ('forms use a hidden field' = the logout form)."""
    b, csrf = signed_in
    r = b.request("POST", "/upload", files={"file": ("x.md", b"# qa csrf", "text/markdown")},
                  data={"csrf": csrf, "csrf_token": csrf, "csrfmiddlewaretoken": csrf},
                  headers={"Accept": "application/json"})
    _csrf_failed(r)


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_CSRF_23_safe_methods_not_csrf_checked(signed_in, method):
    """v1.2 C-13: CSRF applies to POST/PUT/PATCH/DELETE only. HEAD/OPTIONS with the cookie and no token never
    get the csrf_failed 403 (whatever the route answers otherwise)."""
    b, _ = signed_in
    for path in ("/usage", "/ingest", "/dashboard"):
        r = b.request(method, path, headers={"Accept": "application/json"})
        assert not (r.status_code == 403 and "csrf_failed" in r.text), (method, path, r.status_code)
        assert r.status_code < 500, (method, path, r.status_code)
