"""Build every credential of matrix.CREDS as request kwargs (headers / params / cookies)."""
from __future__ import annotations

import base64
import time

import pytest

import jwt_forge
import qasso_data as D


def bearer(tok: str) -> dict:
    return {"headers": {"Authorization": f"Bearer {tok}"}}


def build(name: str, *, tokens, cfg, jwks, signing_key_or_none, session_cookie=None) -> dict:
    """-> kwargs for httpx. pytest.skip when a prerequisite is missing for that credential."""
    now = int(time.time())
    if name == "none":
        return {}
    if name in ("static_ok", "static_query_ok", "static_wrong"):
        if not cfg.static_token:
            pytest.skip("LLM_WIKI_STATIC_TOKEN not set")
        if name == "static_ok":
            return bearer(cfg.static_token)
        if name == "static_query_ok":
            return {"params": {"token": cfg.static_token}}
        return bearer(cfg.static_token + "-wrong")
    if name == "basic_scheme":
        return {"headers": {"Authorization": "Basic " + base64.b64encode(b"qa:" + (cfg.static_token or "x").encode()).decode()}}
    if name == "garbage_jwt":
        return bearer("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln")
    user = {"sso_direct": "qa_wiki_direct", "sso_group": "qa_wiki_group", "sso_super": "qa_wiki_super_app1", "sso_super_nogrant": "qa_wiki_super",
            "sso_none": "qa_wiki_none", "sso_staff": "qa_wiki_staff", "sso_other_app": "qa_wiki_app2"}
    if name in user:
        return bearer(tokens(user[name])["access_token"])
    base = tokens("qa_wiki_direct")["access_token"]
    kid = jwt_forge.header(base).get("kid")
    if name == "sso_refresh_token":
        return bearer(tokens("qa_wiki_direct")["refresh_token"])
    if name == "sso_tampered_perms":
        return bearer(jwt_forge.tamper_payload(tokens("qa_wiki_none")["access_token"], perms=[D.APP_PERM]))
    if name == "sso_attacker_key":
        return bearer(jwt_forge.sign(jwt_forge.claims_like(base), jwt_forge.new_rsa_key(), kid))
    if name == "sso_alg_none":
        return bearer(jwt_forge.alg_none(jwt_forge.claims_like(base), kid=kid))
    if name == "sso_hs256_confusion":
        pub = jwt_forge.jwk_to_public_key(jwks["keys"][0])
        secret = jwt_forge.public_key_secret_variants(pub)["spki_pem"]
        return bearer(jwt_forge.hs256_with_secret(jwt_forge.claims_like(base), secret, kid=kid))
    if name == "session_ok":
        if not session_cookie or not session_cookie[0]:
            pytest.skip("no session cookie for this instance")
        cookie, csrf = session_cookie
        h = {"Cookie": f"llmwiki_session={cookie}"}
        if csrf:
            h["X-CSRF-Token"] = csrf      # v1.1 item 3: required on cookie-authenticated non-GET requests
        return {"headers": h}
    if signing_key_or_none is None:
        pytest.skip("SSO_TEST_PRIVATE_KEY_PATH not set (needs a validly signed token)")
    key = signing_key_or_none
    if name == "sso_expired":
        return bearer(jwt_forge.sign(jwt_forge.claims_like(base, iat=now - 1200, exp=now - 120), key, kid))
    if name == "sso_wrong_aud":
        return bearer(jwt_forge.sign(jwt_forge.claims_like(base, aud="some-other-audience"), key, kid))
    if name == "sso_wrong_iss":
        return bearer(jwt_forge.sign(jwt_forge.claims_like(base, iss="https://evil-sso.example"), key, kid))

    raise KeyError(name)
