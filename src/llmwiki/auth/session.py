"""The browser session cookie ``llmwiki_session`` (design B4).

Signed (itsdangerous, key ``SESSION_SECRET``), **not encrypted**: it holds the SSO access and
refresh token, the access expiry and the username. That is acceptable because the cookie is
HttpOnly, Secure (configurable), SameSite=Lax and only ever sent to this service; anyone who can
read it could also read the tokens from this service's memory. The signature stops tampering and
forgery.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass

from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE_NAME = "llmwiki_session"
CSRF_COOKIE_NAME = "llmwiki_csrf"
#: Browsers drop cookies above 4096 bytes (name + value + attributes); tests assert we stay under.
MAX_COOKIE_BYTES = 4096


@dataclass(frozen=True)
class Session:
    access_token: str
    refresh_token: str
    access_exp: int
    refresh_exp: int
    username: str

    def access_expires_within(self, seconds: float, now: float | None = None) -> bool:
        return self.access_exp - (time.time() if now is None else now) <= seconds

    def max_age(self, now: float | None = None) -> int:
        return max(0, int(self.refresh_exp - (time.time() if now is None else now)))


class SessionCodec:
    """dumps/loads a :class:`Session` as a signed, timestamped cookie value."""

    def __init__(self, secret: str, max_age_cap: int = 31 * 24 * 3600) -> None:
        if not secret:
            raise ValueError("SESSION_SECRET is empty")
        self._s = URLSafeTimedSerializer(secret, salt="llmwiki-session-v1")
        self.max_age_cap = max_age_cap

    def dumps(self, session: Session) -> str:
        value = self._s.dumps(asdict(session))
        return value if isinstance(value, str) else value.decode()

    def loads(self, value: str | None, now: float | None = None) -> Session | None:
        """The session, or None for a missing, forged, tampered, malformed or expired cookie."""
        if not value:
            return None
        try:
            data = self._s.loads(value, max_age=self.max_age_cap)
            session = Session(**{k: data[k] for k in Session.__dataclass_fields__})
        except (BadSignature, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not all(isinstance(getattr(session, k), str) for k in
                   ("access_token", "refresh_token", "username")):
            return None
        if session.max_age(now) <= 0:
            return None
        return session


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, form_value: str | None) -> bool:
    """Double-submit check: a non-trivial cookie value equal to the hidden form field."""
    if not cookie_value or not form_value or len(cookie_value) < 32:
        return False
    return secrets.compare_digest(cookie_value, form_value)
