"""JWTV-*: llm-wiki's SSO token verification (design B2), probed through a protected route.

Uses a validly signed token (QA SSO key) with one property changed, so the only reason to reject is the
property under test. Protected probe: GET /usage (200 when allowed). Instance: sso (else both).
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest

import jwt_forge
import qasso_data as D
from conftest import access_log_lines

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso, pytest.mark.needs_key]
PROBE = "/usage"


def _get(wiki, token, path=PROBE):
    with httpx.Client(base_url=wiki.url, timeout=30, follow_redirects=False) as c:
        return c.get(path, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})


@pytest.fixture(scope="module")
def base(tokens):
    return tokens("qa_wiki_direct")["access_token"]


def test_JWTV_01_valid_token_allowed(wiki, base):
    assert _get(wiki, base).status_code == 200


@pytest.mark.parametrize("age,ok", [(-20, True), (-45, False)])
def test_JWTV_02_exp_leeway_30s(wiki, forge, base, age, ok):
    now = int(time.time())
    tok = forge(base, iat=now - 900, exp=now + age)
    assert (_get(wiki, tok).status_code == 200) is ok


@pytest.mark.parametrize("claims,why", [
    ({"iat": "future"}, "iat 10 min in the future"),
    ({"nbf": "future"}, "nbf 10 min in the future"),
    ({"exp": None}, "exp missing"),
    ({"iat": None}, "iat missing"),
    ({"token_type": None}, "token_type missing"),
    ({"token_type": "refresh"}, "token_type refresh"),
    ({"token_type": "ACCESS"}, "token_type case"),
    ({"aud": None}, "aud missing"),
    ({"iss": None}, "iss missing"),
    ({"aud": "myfin-services-evil"}, "aud near-miss"),
    ({"iss": "issuer-near-miss"}, "iss near-miss"),
    ({"exp": "string"}, "exp not numeric"),
])
def test_JWTV_03_claim_checks_reject(wiki, forge, base, claims, why):
    now = int(time.time())
    fix = {k: (now + 600 if v == "future" else v) for k, v in claims.items()}
    if fix.get("iss") == "issuer-near-miss":
        fix["iss"] = jwt_forge.payload(base)["iss"].rstrip("/") + "/"
    if fix.get("exp") == "string":
        fix["exp"] = str(now + 600)
        if _get(wiki, forge(base, **fix)).status_code == 200:
            pytest.xfail("F-INFO: a numeric *string* exp is accepted (PyJWT 2.x int()s it; RFC 7519 wants a JSON "
                         "number). Only the SSO key holder can mint such a token -> informational")
    r = _get(wiki, forge(base, **fix))
    assert r.status_code == 401, (why, r.status_code, r.text[:200])


def test_JWTV_04_aud_list_containing_audience(wiki, forge, base, cfg):
    """RFC 7519 allows aud as an array; PyJWT accepts it. Allowed (documents behaviour)."""
    assert _get(wiki, forge(base, aud=[cfg.sso_audience, "other"])).status_code == 200


@pytest.mark.parametrize("alg", ["RS384", "RS512", "PS256"])
def test_JWTV_05_only_rs256(wiki, signing_key, base, alg):
    tok = jwt_forge.sign(jwt_forge.claims_like(base), signing_key, jwt_forge.header(base)["kid"], alg=alg)
    assert _get(wiki, tok).status_code == 401


@pytest.mark.parametrize("variant", ["spki_pem", "spki_pem_nonewline", "pkcs1_pem", "spki_der"])
def test_JWTV_06_hs256_key_confusion(wiki, jwks, base, variant):
    pub = jwt_forge.jwk_to_public_key(jwks["keys"][0])
    secret = jwt_forge.public_key_secret_variants(pub)[variant]
    tok = jwt_forge.hs256_with_secret(jwt_forge.claims_like(base), secret, kid=jwt_forge.header(base)["kid"])
    assert _get(wiki, tok).status_code == 401


@pytest.mark.parametrize("alg", ["none", "None", "NONE", "nOnE"])
def test_JWTV_07_alg_none(wiki, base, alg):
    assert _get(wiki, jwt_forge.alg_none(jwt_forge.claims_like(base), alg=alg, kid=jwt_forge.header(base)["kid"])).status_code == 401


def test_JWTV_08_tampered_signature_and_payload(wiki, base, tokens):
    assert _get(wiki, jwt_forge.tamper_signature(base)).status_code == 401
    none_tok = tokens("qa_wiki_none")["access_token"]
    assert _get(wiki, jwt_forge.tamper_payload(none_tok, perms=[D.APP_PERM])).status_code == 401


def test_JWTV_09_missing_kid_and_header_injection(wiki, signing_key, base):
    pl = jwt_forge.claims_like(base)
    assert _get(wiki, jwt_forge.sign(pl, signing_key, None)).status_code == 401  # v1.2 Q-14: missing kid -> reject
    attacker = jwt_forge.new_rsa_key()
    for extra in ({"jku": "https://evil.example/jwks.json"}, {"x5u": "https://evil.example/cert.pem"},
                  {"jwk": jwt_forge.public_jwk(attacker.public_key(), "evil")}):
        tok = jwt_forge.sign(pl, attacker, jwt_forge.header(base)["kid"], extra_headers=extra)
        assert _get(wiki, tok).status_code == 401, extra


@pytest.mark.parametrize("perms,why", [
    ("application.app1", "string instead of list ('in' would be a substring test)"),
    ("xapplication.app1y", "substring of a string"),
    ({"application.app1": True}, "object: 'in' would test keys"),
    ([["application.app1"]], "nested list"),
    (["Application.App1"], "case"),
    ([" application.app1"], "leading space"),
    (["application.app1 "], "trailing space"),
    (["application.app10"], "prefix match"),
    (["application.*"], "wildcard"),
    ([], "empty"),
    (None, "claim absent"),
])
def test_JWTV_10_perm_check_is_exact_list_membership(wiki, forge, base, perms, why):
    """B2: SSO_REQUIRED_PERMISSION in principal.perms. Validly signed tokens with malformed perms -> 403 (or 401
    when the token is rejected as malformed), NEVER 200."""
    r = _get(wiki, forge(base, perms=perms) if perms is not None else forge(base, _drop=("perms",)))
    assert r.status_code in (401, 403), (why, r.status_code)
    if r.status_code == 403:
        assert r.json() == {"detail": "forbidden", "error": "permission_denied"}


def test_JWTV_11_staff_flag_does_not_grant(wiki, forge, base):
    r = _get(wiki, forge(base, perms=[], is_staff=True, groups=["admins", D.APP_GROUP]))
    assert r.status_code == 403


def test_JWTV_12_valid_token_of_deleted_or_inactive_user_still_accepted(wiki, forge, base):
    """Stateless verification: llm-wiki does not introspect, so a well-signed unexpired token is honoured until
    exp (<= access TTL). Documents the revocation window (see SESS-11 / PREF-02)."""
    assert _get(wiki, forge(base, sub="999999999", username="qa_deleted_user")).status_code == 200


def test_JWTV_13_unknown_kid_refetch_limited(wiki, signing_key, base, cfg):
    """B2: unknown kid -> at most one JWKS refetch per 60 s."""
    before = access_log_lines(cfg, "/.well-known/jwks.json")
    for _ in range(6):
        tok = jwt_forge.sign(jwt_forge.claims_like(base), signing_key, "unknown-" + uuid.uuid4().hex[:8])
        assert _get(wiki, tok).status_code == 401
    assert access_log_lines(cfg, "/.well-known/jwks.json") - before <= 1


# ("Bearer {t} " with a trailing space cannot be sent: httpx refuses header values with surrounding whitespace)
@pytest.mark.parametrize("hdr", ["bearer {t}", "BEARER {t}", "Bearer  {t}"])
def test_JWTV_14_bearer_scheme_parsing(wiki, base, hdr):
    with httpx.Client(base_url=wiki.url, timeout=30) as c:
        r = c.get(PROBE, headers={"Authorization": hdr.format(t=base)})
    assert r.status_code in (200, 401), r.status_code    # never 500


def test_JWTV_15_oversized_token_no_500(wiki, forge, base):
    r = _get(wiki, forge(base, pad="A" * 12000))
    assert r.status_code in (200, 400, 401, 413, 431), r.status_code


def test_JWTV_16_two_dot_static_token_lookalike(wiki, cfg):
    """B3: a bearer with 3 dot-separated parts is treated as a JWT; garbage like that is 401 not 500."""
    for t in ("a.b.c", "..", "x" * 20 + ".." + "y" * 20, "eyJ.eyJ.sig"):
        assert _get(wiki, t).status_code == 401, t


def test_JWTV_17_required_permission_is_configurable(cfg, tokens):
    """B1 SSO_REQUIRED_PERMISSION: an instance requiring application.qa_app2 (LLM_WIKI_APP2_URL)."""
    import os
    url = os.environ.get("LLM_WIKI_APP2_URL")
    if not url:
        pytest.skip("LLM_WIKI_APP2_URL not set (instance with SSO_REQUIRED_PERMISSION=application.qa_app2)")
    with httpx.Client(base_url=url, timeout=30) as c:
        def st(u):
            return c.get(PROBE, headers={"Authorization": f"Bearer {tokens(u)['access_token']}"}).status_code
        assert st("qa_wiki_app2") == 200
        assert st("qa_wiki_direct") == 403
        assert st("qa_wiki_mixed") == 200
