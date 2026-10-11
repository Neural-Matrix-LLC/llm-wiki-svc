"""MATRIX-*: AUTH_MODE {static, sso, both} x SSO_PROTECT_READS x route class x credential (B1/B3/B4/B5 + v1.1 1/3/5/6/7).

Each llm-wiki instance is started with one configuration (run/start_all.sh); this module asks every
configured instance every question and compares against matrix.expect().
"""
from __future__ import annotations

import pytest

import creds as C
import matrix as M
from conftest import ALL_INSTANCE_NAMES, CFG, instance_or_skip

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]

KINDS = {"protected": M.PROTECTED, "read": M.READ, "healthz": M.HEALTH, "dashboard": M.DASHBOARD, "mcp": M.MCP}


@pytest.fixture(scope="module")
def session_cookies(cfg, seeded):
    """One browser session (qa_wiki_direct) per sso/both instance: (cookie, csrf from the dashboard meta tag)."""
    import qasso_data as D
    from wiki_client import Browser
    out = {}
    for name, inst in cfg.instances.items():
        if inst.mode == "static":
            continue
        b = Browser(inst.url)
        try:
            b.login("qa_wiki_direct", D.PASSWORD)
            try:
                csrf = b.session_csrf()
            except AssertionError as e:
                csrf = None
                print(f"no csrf meta on {name}: {e}")
            out[name] = (b.session_value, csrf)
        except AssertionError as e:   # login itself is tested elsewhere; the matrix just loses this column
            out[name] = None
            print(f"session login failed on {name}: {e}")
        finally:
            b.close()
    return out


def _check(inst, probe, kw, expected):
    import httpx
    kw = {**probe.kw(), **{k: v for k, v in kw.items() if k != "headers"},
          "headers": {**probe.kw().get("headers", {}), **kw.get("headers", {})}}
    if "params" in probe.kw() and "params" in kw:
        kw["params"] = {**probe.kw()["params"], **kw["params"]}
    with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
        r = c.request(probe.method, probe.path, **kw)
    if expected == M.ALLOW:
        if probe.allowed_status is not None:
            assert r.status_code == probe.allowed_status, (probe.id, r.status_code, r.text[:300])
        else:
            assert r.status_code not in (401, 403, 303) and r.status_code < 500, (probe.id, r.status_code, r.text[:300])
    else:
        assert r.status_code in expected, (probe.id, "expected", expected, "got", r.status_code, r.text[:300])
        if r.status_code == 401 and inst.mode != "static":
            assert "bearer" in r.headers.get("www-authenticate", "").lower(), "B3: 401 carries WWW-Authenticate: Bearer"
        if r.status_code == 401 and inst.mode == "static":
            assert "www-authenticate" not in r.headers, "v1.1 item 5: static 401 headers byte-for-byte as today"
        if r.status_code == 403 and r.headers.get("content-type", "").startswith("application/json"):
            assert r.json() == {"detail": "forbidden", "error": "permission_denied"}, r.text
        if r.status_code == 303:
            assert r.headers["location"].startswith("/login?next="), r.headers.get("location")
    return r


@pytest.mark.parametrize("cred", list(M.CREDS))
@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("inst_name", ALL_INSTANCE_NAMES)
def test_MATRIX(inst_name, kind, cred, cfg, tokens, jwks, request, session_cookies):
    inst = instance_or_skip(cfg, inst_name)
    expected = M.expect(inst.mode, inst.protect_reads, kind, cred)
    if isinstance(expected, tuple) and expected and expected[0] == "skip":
        pytest.skip(expected[1])
    try:
        key = request.getfixturevalue("signing_key")
    except pytest.skip.Exception:
        key = None
    kw = C.build(cred, tokens=tokens, cfg=cfg, jwks=jwks, signing_key_or_none=key,
                 session_cookie=session_cookies.get(inst_name))
    for probe in KINDS[kind]:
        if probe.deny_only and expected == M.ALLOW:
            continue
        _check(inst, probe, kw, expected)
