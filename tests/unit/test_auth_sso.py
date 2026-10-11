"""SSO access-token verification (docs/sso-integration-design.md B2). No network: local RSA keys."""

from __future__ import annotations

import time

import pytest
from tests.sso_fake import (
    AUDIENCE,
    ISSUER,
    PERM,
    FakeSSO,
    Key,
    access_claims,
    alg_none,
    hs256_with_public_key,
)

from llmwiki.auth import AuthError, PermissionDenied, Principal
from llmwiki.auth.session import csrf_ok
from llmwiki.auth.sso import (
    JWKSCache,
    SSOVerifier,
    check_permission,
    looks_like_jwt,
    principal_from_claims,
)


@pytest.fixture(scope="module")
def key() -> Key:
    return Key.generate()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def verifier_for(*keys: Key, clock: Clock | None = None) -> tuple[SSOVerifier, list[int]]:
    count = [0]

    def fetch(url: str) -> dict:
        count[0] += 1
        return {"keys": [k.jwk() for k in keys]}

    jwks = JWKSCache("https://sso.example.test/.well-known/jwks.json", fetch,
                     clock=clock or Clock())
    return SSOVerifier(jwks, ISSUER, AUDIENCE), count


def test_valid_token_gives_a_principal(key) -> None:
    v, _ = verifier_for(key)
    p = v.verify(key.sign(access_claims()))
    assert p == Principal(sub="42", username="alice", email="alice@example.test", is_staff=False,
                          groups=("trader", "beta"), perms=(PERM,), via="sso")
    assert check_permission(p, PERM) is p


@pytest.mark.parametrize("over,reason", [
    ({"exp": int(time.time()) - 120}, "expired"),
    ({"iss": "https://evil.example"}, "wrong iss"),
    ({"aud": "other-audience"}, "wrong aud"),
    ({"token_type": "refresh"}, "refresh token"),
    ({"token_type": None}, "no token_type"),
    ({"iat": int(time.time()) + 3600}, "iat in the future"),
])
def test_bad_claims_are_rejected(key, over, reason) -> None:
    v, _ = verifier_for(key)
    claims = {k: val for k, val in access_claims(**over).items() if val is not None}
    with pytest.raises(AuthError):
        v.verify(key.sign(claims))


def test_expiry_leeway_is_30_seconds(key) -> None:
    v, _ = verifier_for(key)
    v.verify(key.sign(access_claims(exp=int(time.time()) - 10)))  # inside leeway
    with pytest.raises(AuthError):
        v.verify(key.sign(access_claims(exp=int(time.time()) - 45)))


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "iss", "aud"])
def test_required_claims(key, missing) -> None:
    v, _ = verifier_for(key)
    claims = access_claims()
    del claims[missing]
    with pytest.raises(AuthError):
        v.verify(key.sign(claims))


def test_alg_none_is_rejected(key) -> None:
    v, _ = verifier_for(key)
    with pytest.raises(AuthError, match="RS256"):
        v.verify(alg_none(access_claims(), key.kid))


def test_hs256_confusion_with_the_public_key_is_rejected(key) -> None:
    v, _ = verifier_for(key)
    with pytest.raises(AuthError):
        v.verify(hs256_with_public_key(key, access_claims()))


def test_signature_by_another_key_with_a_known_kid_is_rejected(key) -> None:
    rogue = Key.generate()
    v, _ = verifier_for(key)
    with pytest.raises(AuthError):
        v.verify(rogue.sign(access_claims(), kid=key.kid))


def test_missing_kid_and_garbage_are_rejected(key) -> None:
    v, _ = verifier_for(key)
    with pytest.raises(AuthError, match="kid"):
        v.verify(key.sign(access_claims(), kid=None))
    with pytest.raises(AuthError):
        v.verify("not.a.jwt")


def test_unknown_kid_refetches_at_most_once_per_minute(key) -> None:
    clock = Clock()
    v, count = verifier_for(key, clock=clock)
    v.verify(key.sign(access_claims()))
    assert count[0] == 1  # first use
    rogue = Key.generate()
    clock.t += 61
    for _ in range(20):  # a burst of made-up kids: one refetch only
        with pytest.raises(AuthError, match="unknown signing key"):
            v.verify(rogue.sign(access_claims()))
    assert count[0] == 2
    clock.t += 30
    with pytest.raises(AuthError):
        v.verify(rogue.sign(access_claims()))
    assert count[0] == 2  # still inside the 60 s window
    clock.t += 31
    with pytest.raises(AuthError):
        v.verify(rogue.sign(access_claims()))
    assert count[0] == 3
    v.verify(key.sign(access_claims()))  # the known key keeps working without a fetch
    assert count[0] == 3


def test_key_rotation_is_picked_up_by_the_refetch(key) -> None:
    clock = Clock()
    published = [key]

    def fetch(url: str) -> dict:
        return {"keys": [k.jwk() for k in published]}

    v = SSOVerifier(JWKSCache("u", fetch, clock=clock), ISSUER, AUDIENCE)
    v.verify(key.sign(access_claims()))
    new = Key.generate()
    published.append(new)
    clock.t += 60
    assert v.verify(new.sign(access_claims())).username == "alice"


def test_cache_expires_after_an_hour_and_a_failed_fetch_keeps_old_keys(key) -> None:
    clock = Clock()
    state = {"fail": False, "n": 0}

    def fetch(url: str) -> dict:
        state["n"] += 1
        if state["fail"]:
            raise OSError("down")
        return {"keys": [key.jwk(), {"kty": "EC", "kid": "ec"}, {"kty": "RSA"}]}

    v = SSOVerifier(JWKSCache("u", fetch, clock=clock), ISSUER, AUDIENCE)
    v.verify(key.sign(access_claims()))
    state["fail"] = True
    clock.t += 3601
    v.verify(key.sign(access_claims()))  # stale + failed refetch: old keys kept
    assert state["n"] == 2


def test_first_fetch_failure_is_an_auth_error(key) -> None:
    def fetch(url: str) -> dict:
        raise OSError("down")

    v = SSOVerifier(JWKSCache("u", fetch), ISSUER, AUDIENCE)
    with pytest.raises(AuthError, match="JWKS"):
        v.verify(key.sign(access_claims()))


def test_permission_present_and_absent(key) -> None:
    v, _ = verifier_for(key)
    assert check_permission(v.verify(key.sign(access_claims())), PERM)
    with pytest.raises(PermissionDenied) as info:
        check_permission(v.verify(key.sign(access_claims(perms=[]))), PERM)
    assert info.value.permission == PERM
    with pytest.raises(PermissionDenied):  # another app's permission is not ours
        check_permission(v.verify(key.sign(access_claims(perms=["application.app2"]))), PERM)


def test_principal_from_odd_claims() -> None:
    p = principal_from_claims({"sub": 7, "groups": "admins", "perms": ["a", 3]})
    assert p.sub == "7" and p.groups == () and p.perms == ("a",)


def test_looks_like_jwt() -> None:
    assert looks_like_jwt("a.b.c")
    assert not looks_like_jwt("changeme")
    assert not looks_like_jwt("a..c")
    assert not looks_like_jwt("a.b.c.d")


# ---------------------------------------------------------------- CSRF helper
def test_csrf_ok() -> None:
    token = "x" * 43
    assert csrf_ok(token, token)
    assert not csrf_ok(token, "y" * 43)
    assert not csrf_ok(None, token) and not csrf_ok(token, "")
    assert not csrf_ok("short", "short")


def test_fake_sso_tokens_verify(key) -> None:
    sso = FakeSSO(key)
    v, _ = verifier_for(key)
    body = sso.handle(__import__("httpx").Request(
        "POST", "https://sso.example.test/api/v1/auth/login",
        json={"username": "alice", "password": "alice-pw", "client": "native"})).json()
    assert v.verify(body["access_token"]).perms == (PERM,)
