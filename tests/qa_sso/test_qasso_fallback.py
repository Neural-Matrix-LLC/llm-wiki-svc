"""FB-*: static-token fallback in AUTH_MODE=both (design v1.1 item 6; D-09 / Q-16 resolved).

"Try SSO verification when the bearer parses as a JWT; if verification fails, fall back to the constant-time
static compare." The `dotted` instance (LLM_WIKI_DOTTED_URL, AUTH_MODE=both) has an INGEST_API_TOKEN that parses
as a JWT (base64url JSON header + payload + random 'signature', see run/start_all.sh) but is not an SSO token,
so before v1.1 it was unusable. Fallback applies to FAILED VERIFICATION only: a verified SSO token without the
permission is 403, never a static compare.
"""
from __future__ import annotations

import time

import httpx
import pytest

import jwt_forge
import matrix as M
import qasso_data as D

pytestmark = [pytest.mark.needs_wiki]


@pytest.fixture(scope="module")
def dotted(cfg):
    if not (cfg.dotted_url and cfg.dotted_token):
        pytest.skip("LLM_WIKI_DOTTED_URL / LLM_WIKI_DOTTED_TOKEN not set")
    return cfg.dotted_url, cfg.dotted_token


def _req(url, tok, method="GET", path="/usage", **kw):
    h = {"Accept": "application/json"} | kw.pop("headers", {})
    if tok is not None:
        h["Authorization"] = f"Bearer {tok}"
    with httpx.Client(base_url=url, timeout=30, follow_redirects=False) as c:
        return c.request(method, path, headers=h, **kw)


def test_FB_01_token_parses_as_jwt(dotted):
    _, tok = dotted
    assert tok.count(".") == 2 and jwt_forge.header(tok)["alg"] == "RS256", "harness: token must look like a JWT"
    assert len(tok) >= 24


@pytest.mark.parametrize("probe", [p for p in M.PROTECTED if not p.deny_only], ids=lambda p: p.id)
def test_FB_02_jwt_shaped_static_token_works_on_protected_routes(dotted, probe):
    url, tok = dotted
    kw = probe.kw()
    r = _req(url, tok, probe.method, probe.path, headers=kw.pop("headers", {}), **kw)
    assert r.status_code == probe.allowed_status, (probe.id, r.status_code, r.text[:200])


def test_FB_03_jwt_shaped_static_token_on_dashboard_and_mcp(dotted):
    url, tok = dotted
    assert _req(url, tok, path="/dashboard", headers={"Accept": "text/html"}).status_code == 200
    r = _req(url, tok, "POST", "/mcp/", json=M.MCP_INIT, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200, "v1.1 item 1: MCP in both mode accepts the static token"


@pytest.mark.parametrize("variant", ["flip_last", "drop_last", "append", "other_sig", "case"])
def test_FB_04_near_miss_jwt_shaped_tokens_rejected(dotted, variant):
    url, tok = dotted
    h, p, sig = tok.split(".")
    bad = {"flip_last": tok[:-1] + ("A" if tok[-1] != "A" else "B"), "drop_last": tok[:-1], "append": tok + "x",
           "other_sig": f"{h}.{p}.{'Q' * len(sig)}", "case": tok.swapcase()}[variant]
    assert _req(url, bad).status_code == 401


@pytest.mark.needs_sso
def test_FB_05_valid_sso_token_still_wins(dotted, tokens, seeded):
    url, _ = dotted
    assert _req(url, tokens("qa_wiki_direct")["access_token"]).status_code == 200


@pytest.mark.needs_sso
@pytest.mark.parametrize("user", ["qa_wiki_none", "qa_wiki_staff", "qa_wiki_app2"])
def test_FB_06_verified_token_without_perm_is_403_not_fallback(dotted, tokens, seeded, user):
    """A token that VERIFIES but lacks application.app1 is a 403; the fallback must not turn it into a 401
    (static compare failing) nor into a 200."""
    url, _ = dotted
    r = _req(url, tokens(user)["access_token"])
    assert r.status_code == 403 and r.json() == {"detail": "forbidden", "error": "permission_denied"}


@pytest.mark.needs_sso
@pytest.mark.needs_key
@pytest.mark.parametrize("case", ["expired", "wrong_aud", "wrong_iss", "attacker_key", "alg_none", "refresh_type"])
def test_FB_07_failed_verification_then_failed_compare_is_401(dotted, tokens, forge, seeded, case):
    url, _ = dotted
    base = tokens("qa_wiki_direct")["access_token"]
    tok = {
        "expired": lambda: forge(base, exp=int(time.time()) - 120),
        "wrong_aud": lambda: forge(base, aud="someone-else"),
        "wrong_iss": lambda: forge(base, iss="https://evil.example"),
        "attacker_key": lambda: forge(base, _key=jwt_forge.new_rsa_key()),
        "alg_none": lambda: jwt_forge.alg_none(jwt_forge.payload(base)),
        "refresh_type": lambda: tokens("qa_wiki_direct")["refresh_token"],
    }[case]()
    r = _req(url, tok)
    assert r.status_code == 401, (case, r.status_code)
    assert "bearer" in r.headers.get("www-authenticate", "").lower()


@pytest.mark.needs_sso
def test_FB_08_sso_mode_has_no_fallback(cfg, static_token):
    """In AUTH_MODE=sso the static token is never accepted, JWT-shaped or not (matrix covers the plain token)."""
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    assert _req(inst.url, static_token).status_code == 401
    if cfg.dotted_token:
        assert _req(inst.url, cfg.dotted_token).status_code == 401


def test_FB_09_dotted_static_token_on_both_instance_with_other_token_rejected(cfg, dotted):
    """The JWT-shaped token belongs to the dotted instance only; the regular both instance rejects it."""
    inst = cfg.instances.get("both") or pytest.skip("LLM_WIKI_BASE_URL (both) not set")
    _, tok = dotted
    assert _req(inst.url, tok).status_code == 401


def test_FB_10_cookie_not_affected(dotted, browser_factory, seeded):
    """Session login on the dotted instance works normally (fallback is a Bearer-only path)."""
    url, _ = dotted
    b = browser_factory(url)
    b.login("qa_wiki_direct", D.PASSWORD)
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 200
