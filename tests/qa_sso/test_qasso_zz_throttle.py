"""THR-*: throttling (design B4 + v1.1 item 4, D-03 resolved by design).

v1.1 item 4: /api/v1/auth/login and /refresh accept HTTP Basic from a registered, active ServiceClient plus a
JSON field client_ip; only then do the IP throttle and the login log use client_ip (validated as an IP).
Without a Basic header the field is ignored; v1.2: an invalid Basic header -> 401 invalid_client (C-19), a
non-IP client_ip with valid credentials -> 400 invalid_request (Q-28). llm-wiki sends SSO_CLIENT_ID/SECRET and the browser's IP.
Per-username throttle unchanged.

Run alone with the SSO at spec rates (per IP 10/min, per username 5/min, refresh 60/min):
QA_SSO_THROTTLE_PHASE=1. Every case uses its own loopback source address (127.0.0.x) so the per-IP buckets of
different cases never collide and no 61 s sleeps are needed. The server sees the TCP peer address (llm-wiki:
request.client.host; SSO: REMOTE_ADDR with SSO_NUM_PROXIES=0).
"""
from __future__ import annotations

import json
import re
import time
import uuid

import pytest

import qasso_data as D
from wiki_client import api

pytestmark = [pytest.mark.throttle, pytest.mark.slow]
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
IP_LIMIT, USER_LIMIT = 10, 5


def _u(tag):
    return f"nobody_thr_{tag}_{uuid.uuid4().hex[:8]}"


def _sso_login(c, username, password=D.WRONG_PASSWORD, client_ip=None, auth=None):
    body = {"username": username, "password": password}
    if client_ip is not None:
        body["client_ip"] = client_ip
    return c.post(LOGIN, json=body, auth=auth)


# ================================================================ through llm-wiki
@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_THR_01_per_username_limit_message(wiki, browser_factory, seeded):
    last = None
    for _ in range(USER_LIMIT + 1):
        b = browser_factory(wiki, source_ip="127.0.0.21")
        last = b.submit_login("qa_wiki_thr_user", D.WRONG_PASSWORD)
    assert "Too many attempts" in last.text, last.text[:500]
    m = re.search(r"try again in (\d+)\s*s", last.text, re.I)
    assert m and 0 < int(m.group(1)) <= 60, last.text[:500]
    assert b.session_value is None


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_THR_02_correct_password_while_throttled_no_session(wiki, browser_factory, seeded):
    b = browser_factory(wiki, source_ip="127.0.0.21")
    r = b.submit_login("qa_wiki_thr_user", D.PASSWORD)
    assert r.status_code != 303 and "Too many attempts" in r.text


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_THR_03_one_attacker_does_not_lock_out_everyone(wiki, browser_factory, seeded):
    """D-03: an attacker (127.0.0.22) burns the per-IP limit; a user from another IP (127.0.0.23) still signs in,
    because llm-wiki forwards the browser IP as client_ip under its ServiceClient."""
    for i in range(IP_LIMIT + 1):
        browser_factory(wiki, source_ip="127.0.0.22").submit_login(_u(f"att{i}"), D.WRONG_PASSWORD)
    b = browser_factory(wiki, source_ip="127.0.0.23")
    r = b.submit_login("qa_wiki_thr_victim", D.PASSWORD)
    assert r.status_code == 303, "an unrelated user is locked out by someone else's failed attempts"


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_THR_04_attacker_ip_itself_is_throttled(wiki, browser_factory, seeded):
    """The per-IP limit still applies to the real end user: the attacker's 11th attempt is throttled."""
    last = None
    for i in range(IP_LIMIT + 1):
        last = browser_factory(wiki, source_ip="127.0.0.24").submit_login(_u(f"ip{i}"), D.WRONG_PASSWORD)
    assert "Too many attempts" in last.text, last.text[:300]


@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_THR_05_browser_x_forwarded_for_not_trusted(wiki, browser_factory, seeded):
    """A browser rotating X-Forwarded-For / X-Real-IP / CF-Connecting-IP must not get fresh IP buckets.
    The design does not say how llm-wiki finds 'the browser's IP' behind a proxy (Q-25); QA expects forwarded
    headers to be ignored unless a trusted-proxy setting says otherwise (none in the design)."""
    last = None
    for i in range(IP_LIMIT + 1):
        spoof = f"198.51.100.{i + 1}"
        last = browser_factory(wiki, source_ip="127.0.0.25").submit_login(
            _u(f"xff{i}"), D.WRONG_PASSWORD,
            headers={"X-Forwarded-For": spoof, "X-Real-IP": spoof, "CF-Connecting-IP": spoof})
    assert "Too many attempts" in last.text, "spoofed forwarding headers evaded the per-IP throttle"


# ================================================================ at the SSO directly (sso-server side of item 4)
@pytest.mark.needs_sso
def test_THR_10_client_ip_ignored_without_basic(cfg, seeded):
    """A non-service caller (no Basic) rotating client_ip is still throttled on its own address."""
    with api(cfg.sso_url, source_ip="127.0.0.31") as c:
        codes = [_sso_login(c, _u(f"nb{i}"), client_ip=f"203.0.113.{i + 1}").status_code for i in range(IP_LIMIT + 1)]
    assert codes[-1] == 429 and 429 not in codes[:-1], codes


@pytest.mark.needs_sso
@pytest.mark.parametrize("case", ["wrong_secret", "inactive_client", "unknown_client", "garbage_basic"])
def test_THR_11_invalid_basic_is_401_invalid_client(cfg, seeded, case):
    """v1.2 C-19: a Basic header that does not authenticate an active ServiceClient -> 401 invalid_client
    (WWW-Authenticate: Basic), no silent fallback - even with the RIGHT user password (no token issued)."""
    import httpx
    auth = {"wrong_secret": (D.LOGIN_CLIENT_ID, "wrong-secret"),
            "inactive_client": (D.INACTIVE_CLIENT_ID, D.INACTIVE_CLIENT_SECRET),
            "unknown_client": ("qa-no-such-client", "x"),
            "garbage_basic": None}[case]
    ip = {"wrong_secret": "127.0.0.32", "inactive_client": "127.0.0.33",
          "unknown_client": "127.0.0.34", "garbage_basic": "127.0.0.35"}[case]
    with api(cfg.sso_url, source_ip=ip) as c:
        rs = []
        for i in range(IP_LIMIT + 1):
            body = {"username": "qa_wiki_direct" if i == 0 else _u(f"{case}{i}"),
                    "password": D.PASSWORD if i == 0 else D.WRONG_PASSWORD, "client_ip": f"203.0.113.{i + 1}"}
            if auth is None:
                rs.append(c.post(LOGIN, json=body, headers={"Authorization": "Basic !!!not-base64!!!"}))
            else:
                rs.append(c.post(LOGIN, json=body, auth=httpx.BasicAuth(*auth)))
    codes = [r.status_code for r in rs]
    assert set(codes) == {401}, codes
    assert all(r.json().get("error") == "invalid_client" for r in rs), [r.text[:120] for r in rs[:3]]
    assert "access_token" not in rs[0].text, "a correct password must not log in through a bad ServiceClient"
    assert rs[0].headers.get("www-authenticate", "").lower().startswith("basic"), rs[0].headers


@pytest.mark.needs_sso
def test_THR_12_valid_service_client_distinct_client_ips_not_ip_throttled(cfg, seeded, login_client_auth):
    with api(cfg.sso_url, source_ip="127.0.0.36") as c:
        codes = [_sso_login(c, _u(f"d{i}"), client_ip=f"203.0.113.{100 + i}", auth=login_client_auth).status_code
                 for i in range(IP_LIMIT + 3)]
    assert 429 not in codes, codes


@pytest.mark.needs_sso
def test_THR_13_valid_service_client_same_client_ip_throttled(cfg, seeded, login_client_auth):
    with api(cfg.sso_url, source_ip="127.0.0.37") as c:
        codes = [_sso_login(c, _u(f"s{i}"), client_ip="203.0.113.200", auth=login_client_auth).status_code
                 for i in range(IP_LIMIT + 1)]
    assert codes[-1] == 429 and 429 not in codes[:-1], codes
    with api(cfg.sso_url, source_ip="127.0.0.37") as c:   # the caller's own bucket is untouched
        assert _sso_login(c, _u("own"), client_ip="203.0.113.201", auth=login_client_auth).status_code == 401


BAD_IPS = {  # case -> (source address, rotating invalid client_ip values)
    "word": ("127.0.0.40", lambda i: f"not-an-ip-{i}"),
    "octet": ("127.0.0.41", lambda i: f"999.1.1.{i}"),
    "blank": ("127.0.0.42", lambda i: " " * i),
    "cidr": ("127.0.0.43", lambda i: f"203.0.113.{i + 1}/24"),
    "list": ("127.0.0.44", lambda i: f"203.0.113.{i + 1}, 10.0.0.1"),
}


@pytest.mark.needs_sso
@pytest.mark.parametrize("case", list(BAD_IPS))
def test_THR_14_invalid_client_ip_is_400(cfg, seeded, login_client_auth, case):
    """v1.2 Q-28: valid ServiceClient credentials + a client_ip that is not an IP -> 400 invalid_request, every
    time (so rotating junk values can never mint fresh throttle buckets)."""
    src, val = BAD_IPS[case]
    with api(cfg.sso_url, source_ip=src) as c:
        rs = [_sso_login(c, _u(f"v{i}"), client_ip=val(i), auth=login_client_auth) for i in range(IP_LIMIT + 1)]
    codes = [r.status_code for r in rs]
    assert set(codes) == {400}, codes
    assert all(r.json().get("error") == "invalid_request" for r in rs), rs[0].text[:200]


@pytest.mark.needs_sso
def test_THR_15_login_log_uses_client_ip_only_when_delegated(cfg, seeded, login_client_auth):
    if not cfg.sso_server_log:
        pytest.skip("SSO_SERVER_LOG not set")
    from pathlib import Path
    user_d, user_n = _u("logd"), _u("logn")
    with api(cfg.sso_url, source_ip="127.0.0.50") as c:
        _sso_login(c, user_d, client_ip="203.0.113.77", auth=login_client_auth)
        _sso_login(c, user_n, client_ip="203.0.113.78")
    time.sleep(1)
    events = {}
    for line in Path(cfg.sso_server_log).read_text(errors="replace").splitlines():
        i = line.find("{")
        if i < 0 or '"event": "login"' not in line:
            continue
        try:
            ev = json.loads(line[i:])
        except ValueError:
            continue
        events[ev.get("username")] = ev
    assert events.get(user_d, {}).get("ip") == "203.0.113.77", events.get(user_d)
    assert events.get(user_n, {}).get("ip") == "127.0.0.50", events.get(user_n)


@pytest.mark.needs_sso
def test_THR_16_per_username_unchanged_under_delegation(cfg, seeded, login_client_auth):
    user = _u("pu")
    with api(cfg.sso_url, source_ip="127.0.0.51") as c:
        codes = [_sso_login(c, user, client_ip=f"203.0.113.{150 + i}", auth=login_client_auth).status_code
                 for i in range(USER_LIMIT + 1)]
    assert codes[-1] == 429 and 429 not in codes[:-1], codes


@pytest.mark.needs_sso
def test_THR_17_refresh_delegation(cfg, seeded, login_client_auth, sso_http):
    """Refresh (60/min per IP): delegated with distinct client_ips -> no 429 after 61; plain caller -> 61st 429.
    Uses throw-away (invalid) refresh tokens: the throttle runs before validation."""
    with api(cfg.sso_url, source_ip="127.0.0.52") as c:
        deleg = [c.post(REFRESH, json={"refresh_token": "x.y.z", "client_ip": f"203.0.113.{i % 250 + 1}"},
                        auth=login_client_auth).status_code for i in range(61)]
    with api(cfg.sso_url, source_ip="127.0.0.53") as c:
        plain = [c.post(REFRESH, json={"refresh_token": "x.y.z", "client_ip": f"203.0.113.{i % 250 + 1}"}).status_code
                 for i in range(61)]
    assert 429 not in deleg, deleg[-5:]
    assert plain[-1] == 429, plain[-5:]
