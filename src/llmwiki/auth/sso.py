"""SSO access-token verification (design B2), ported from SSO-server ``examples/verifier``.

PyJWT only, RS256 only. ``iss``, ``aud``, ``exp``, ``iat`` (30 s leeway) and ``token_type ==
"access"``
are enforced. Keys come from the SSO's JWKS by ``kid``: cached for ``lifespan`` (1 h); an unknown
``kid`` refetches the JWKS **at most once per ``min_refetch_interval`` (60 s)**, so tokens with
made-up
kids cannot make this service hammer the SSO. A failed fetch keeps the old keys.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

from llmwiki.auth.principal import AuthError, PermissionDenied, Principal

Fetch = Callable[[str], dict]


def http_fetch(client: httpx.Client) -> Fetch:
    """A JWKS fetcher over ``client`` (its transport is what tests replace)."""

    def fetch(url: str) -> dict:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("JWKS is not a JSON object")
        return data

    return fetch


class JWKSCache:
    """kid -> RSA public key, with a rate-limited refetch on unknown kids. Thread-safe."""

    def __init__(self, jwks_url: str, fetch: Fetch, *, lifespan: float = 3600.0,
                 min_refetch_interval: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.jwks_url = jwks_url
        self.lifespan = lifespan
        self.min_refetch_interval = min_refetch_interval
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self.fetch_count = 0

    def _refresh(self, now: float) -> None:
        self._attempted_at = now
        self.fetch_count += 1
        try:
            data = self._fetch(self.jwks_url)
            keys: dict[str, Any] = {}
            for jwk in data.get("keys", []):
                if (not isinstance(jwk, dict) or jwk.get("kty") != "RSA"
                        or jwk.get("use", "sig") != "sig" or not jwk.get("kid")):
                    continue
                keys[str(jwk["kid"])] = jwt.PyJWK.from_dict(jwk, algorithm="RS256").key
        except Exception as exc:  # network, JSON or key errors: keep what we have
            if not self._keys:
                raise AuthError(f"cannot fetch the SSO JWKS ({type(exc).__name__})") from exc
            return
        if keys:
            self._keys = keys
            self._fetched_at = now

    def _may_refetch(self, now: float) -> bool:
        return self._attempted_at is None or now - self._attempted_at >= self.min_refetch_interval

    def get_key(self, kid: str | None) -> Any:
        if not kid or not isinstance(kid, str):
            raise AuthError("token has no kid header")
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self.lifespan
            if stale and self._may_refetch(now):
                self._refresh(now)
            if kid not in self._keys and self._may_refetch(now):
                self._refresh(now)  # unknown kid: maybe a key rotation (rate-limited)
            key = self._keys.get(kid)
        if key is None:
            raise AuthError("unknown signing key")
        return key


class SSOVerifier:
    """Verify an SSO access token and turn it into a :class:`Principal`."""

    def __init__(self, jwks: JWKSCache, issuer: str, audience: str = "myfin-services",
                 leeway: int = 30) -> None:
        self.jwks = jwks
        self.issuer = issuer
        self.audience = audience
        self.leeway = leeway

    def claims(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise AuthError("only RS256 tokens are accepted")
            key = self.jwks.get_key(header.get("kid"))
            claims: dict = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                leeway=self.leeway, options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token ({type(exc).__name__})") from exc
        if claims.get("token_type") != "access":
            raise AuthError("not an access token")
        iat = claims.get("iat")
        if isinstance(iat, (int, float)) and iat > time.time() + self.leeway:
            raise AuthError("token issued in the future")
        return claims

    def verify(self, token: str) -> Principal:
        return principal_from_claims(self.claims(token))


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if isinstance(v, str))


def principal_from_claims(claims: dict, via: str = "sso") -> Principal:
    return Principal(
        sub=str(claims.get("sub", "")),
        username=str(claims.get("username", "")),
        email=str(claims.get("email", "") or ""),
        is_staff=bool(claims.get("is_staff", False)),
        groups=_str_tuple(claims.get("groups")),
        perms=_str_tuple(claims.get("perms")),
        via="session" if via == "session" else "sso",
    )


def check_permission(principal: Principal, permission: str) -> Principal:
    """``permission in principal.perms`` or :class:`PermissionDenied` (-> 403)."""
    if not principal.has_perm(permission):
        raise PermissionDenied(permission, principal)
    return principal


def looks_like_jwt(value: str) -> bool:
    """Three non-empty dot-separated parts (design B3)."""
    parts = value.split(".")
    return len(parts) == 3 and all(parts)
