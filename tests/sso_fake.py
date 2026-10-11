"""An in-process stand-in for the SSO server (docs/sso-integration-design.md B7), as an
``httpx.MockTransport``. It signs real RS256 tokens with a locally generated key and speaks the
SSO's login/refresh/logout/JWKS contract (SSO-server spec §5/§6), including its error shapes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://sso.example.test"
BASE_URL = "https://sso.example.test"
AUDIENCE = "myfin-services"
PERM = "application.app1"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class Key:
    private: rsa.RSAPrivateKey
    kid: str

    @classmethod
    def generate(cls, bits: int = 2048) -> Key:
        private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        der = private.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
        return cls(private, hashlib.sha256(der).hexdigest()[:16])

    @property
    def private_pem(self) -> bytes:
        return self.private.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption())

    @property
    def public_pem(self) -> bytes:
        return self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def jwk(self) -> dict:
        nums = self.private.public_key().public_numbers()
        n = nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")
        e = nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid, "n": b64url(n),
                "e": b64url(e)}

    def sign(self, claims: dict, kid: str | None = "auto", alg: str = "RS256") -> str:
        headers = {} if kid is None else {"kid": self.kid if kid == "auto" else kid}
        return jwt.encode(claims, self.private_pem, algorithm=alg, headers=headers)


def access_claims(sub: str = "42", username: str = "alice", perms: list[str] | None = None,
                  ttl: int = 900, **over: object) -> dict:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "token_type": "access",
              "jti": secrets.token_hex(16), "iat": now, "exp": now + ttl, "username": username,
              "email": f"{username}@example.test", "is_staff": False,
              "groups": ["trader", "beta"], "perms": [PERM] if perms is None else perms}
    claims.update(over)
    return claims


def hs256_with_public_key(key: Key, claims: dict) -> str:
    """The algorithm-confusion attack: HS256 'signed' with the RSA public key as the secret."""
    import hmac

    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": key.kid}).encode())
    payload = b64url(json.dumps(claims).encode())
    sig = hmac.new(key.public_pem, f"{header}.{payload}".encode(), "sha256").digest()
    return f"{header}.{payload}.{b64url(sig)}"


def alg_none(claims: dict, kid: str) -> str:
    header = b64url(json.dumps({"alg": "none", "typ": "JWT", "kid": kid}).encode())
    return f"{header}.{b64url(json.dumps(claims).encode())}."


@dataclass
class User:
    password: str
    sub: str
    perms: list[str] = field(default_factory=list)
    active: bool = True


class FakeSSO:
    """Holds users and live refresh tokens; ``transport`` is what llmwiki's httpx client uses."""

    def __init__(self, key: Key | None = None) -> None:
        self.key = key or Key.generate()
        self.users: dict[str, User] = {
            "alice": User("alice-pw", "42", [PERM]),
            "bob": User("bob-pw", "43", []),
            "carol": User("carol-pw", "44", [PERM], active=False),
        }
        self.access_ttl = 900
        self.refresh_ttl = 7 * 24 * 3600
        self.live_refresh: dict[str, str] = {}  # refresh token -> username
        self.calls: list[tuple[str, dict]] = []
        self.down = False
        self.rate_limited: int | None = None
        self.jwks_fetches = 0
        self.issuer = ISSUER

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _pair(self, name: str) -> dict:
        u = self.users[name]
        access = self.key.sign(access_claims(u.sub, name, list(u.perms), self.access_ttl,
                                             iss=self.issuer))
        now = int(time.time())
        refresh = self.key.sign({"iss": self.issuer, "aud": AUDIENCE, "sub": u.sub,
                                 "token_type": "refresh", "jti": secrets.token_hex(16),
                                 "iat": now, "exp": now + self.refresh_ttl})
        self.live_refresh[refresh] = name
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.access_ttl,
                "refresh_token": refresh,
                "user": {"id": int(u.sub), "username": name, "perms": list(u.perms)}}

    @staticmethod
    def _err(status: int, code: str, detail: str = "",
             headers: dict | None = None) -> httpx.Response:
        return httpx.Response(status, json={"error": code, "detail": detail}, headers=headers)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        self.calls.append((path, body))
        if path == "/.well-known/jwks.json":
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": [self.key.jwk()]})
        if path == "/api/v1/auth/login":
            if self.rate_limited is not None:
                return self._err(429, "rate_limited", "Too many attempts.",
                                 {"Retry-After": str(self.rate_limited)})
            name = body.get("username", "")
            u = self.users.get(name)
            if body.get("client") != "native":
                return self._err(400, "invalid_request", "client must be native here")
            if u is None or u.password != body.get("password"):
                return self._err(401, "invalid_credentials", "Invalid username or password.")
            if not u.active:
                return self._err(403, "account_inactive", "Please activate your account.")
            return httpx.Response(200, json=self._pair(name))
        if path == "/api/v1/auth/refresh":
            name = self.live_refresh.pop(body.get("refresh_token", ""), None)
            if name is None or not self.users[name].active:
                return self._err(401, "token_invalid", "Token is invalid or expired")
            return httpx.Response(200, json=self._pair(name))
        if path == "/api/v1/auth/logout":
            self.live_refresh.pop(body.get("refresh_token", ""), None)
            return httpx.Response(204)
        return self._err(404, "not_found")

    def paths(self) -> list[str]:
        return [p for p, _ in self.calls]
