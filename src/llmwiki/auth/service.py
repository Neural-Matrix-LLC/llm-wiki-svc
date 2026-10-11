"""One object per Settings that the HTTP layer uses for every auth decision (design B2-B4)."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import replace

import httpx
import jwt

from llmwiki.auth.client import SSOClient
from llmwiki.auth.principal import (
    STATIC_PRINCIPAL,
    AuthError,
    PermissionDenied,
    Principal,
    SSOLoginError,
    SSOUnavailable,
)
from llmwiki.auth.session import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    MAX_COOKIE_BYTES,
    Session,
    SessionCodec,
    csrf_ok,
    new_csrf_token,
)
from llmwiki.auth.sso import (
    JWKSCache,
    SSOVerifier,
    check_permission,
    http_fetch,
    looks_like_jwt,
    principal_from_claims,
)
from llmwiki.config import Settings

logger = logging.getLogger(__name__)

#: Refresh the access token server-side when it expires within this many seconds (design B4).
REFRESH_MARGIN_S = 60
SSO_TIMEOUT_S = 10.0


class AuthService:
    """Everything api/ needs, behind one object (api/ may not import llmwiki.auth itself)."""

    session_cookie = COOKIE_NAME
    csrf_cookie = CSRF_COOKIE_NAME
    max_cookie_bytes = MAX_COOKIE_BYTES
    new_csrf_token = staticmethod(new_csrf_token)
    csrf_ok = staticmethod(csrf_ok)

    def __init__(self, cfg: Settings, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.mode = cfg.auth_mode
        self.required_permission = cfg.sso_required_permission
        self._http: httpx.Client | None = None
        self._transport = transport
        self._clock = clock
        self._verifier: SSOVerifier | None = None
        self._codec: SessionCodec | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def sso_enabled(self) -> bool:
        return self.mode in ("sso", "both")

    @property
    def static_enabled(self) -> bool:
        return self.mode in ("static", "both")

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=SSO_TIMEOUT_S, transport=self._transport)
        return self._http

    @property
    def client(self) -> SSOClient:
        return SSOClient(self.cfg.sso_base_url, self.http)

    @property
    def verifier(self) -> SSOVerifier:
        if self._verifier is None:
            jwks = JWKSCache(self.cfg.effective_sso_jwks_url, http_fetch(self.http),
                             clock=self._clock)
            self._verifier = SSOVerifier(jwks, self.cfg.sso_issuer, self.cfg.sso_audience)
        return self._verifier

    @property
    def codec(self) -> SessionCodec:
        if self._codec is None:
            self._codec = SessionCodec(self.cfg.session_secret.get_secret_value())
        return self._codec

    # ------------------------------------------------------------------ bearer tokens
    def static_matches(self, supplied: str) -> bool:
        return secrets.compare_digest(supplied, self.cfg.ingest_api_token.get_secret_value())

    def authenticate_bearer(self, token: str) -> Principal:
        """Design B3: a JWT goes to SSO verification (if allowed), else the static token."""
        if looks_like_jwt(token) and self.sso_enabled:
            principal = self.verifier.verify(token)
            return check_permission(principal, self.required_permission)
        if self.static_enabled and self.static_matches(token):
            return STATIC_PRINCIPAL
        raise AuthError("invalid bearer token")

    # ------------------------------------------------------------------ browser session
    def _session_from_tokens(self, body: dict,
                             username_hint: str = "") -> tuple[Session, Principal]:
        access = str(body["access_token"])
        refresh = str(body.get("refresh_token") or "")
        if not refresh:
            raise SSOUnavailable("SSO answered without a refresh token")
        claims = self.verifier.claims(access)
        principal = check_permission(principal_from_claims(claims), self.required_permission)
        try:  # refresh token: issued by the same SSO over TLS; only its exp is read here
            refresh_exp = int(jwt.decode(refresh, options={"verify_signature": False})["exp"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            refresh_exp = int(claims["exp"])
        session = Session(access_token=access, refresh_token=refresh, access_exp=int(claims["exp"]),
                          refresh_exp=refresh_exp,
                          username=principal.username or username_hint)
        return session, principal

    def login(self, username: str, password: str) -> tuple[Session, Principal]:
        """SSO password login. PermissionDenied: the refresh token is discarded at the SSO first."""
        body = self.client.login(username, password)
        try:
            return self._session_from_tokens(body, username)
        except PermissionDenied:
            self._discard(str(body.get("refresh_token") or ""))
            raise

    def _discard(self, refresh_token: str) -> None:
        if not refresh_token:
            return
        try:
            self.client.logout(refresh_token)
        except (SSOLoginError, SSOUnavailable) as exc:
            logger.warning("SSO logout failed (%s); the refresh token expires on its own",
                           type(exc).__name__)

    def session_principal(self, session: Session) -> tuple[Session, Principal, bool]:
        """Principal for a session, refreshing (and rotating) it first if the access token is due.

        Returns ``(session, principal, rotated)``. Raises AuthError when the session is no longer
        usable (refresh refused, token invalid) and PermissionDenied when the permission was
        revoked.
        """
        if session.access_expires_within(REFRESH_MARGIN_S):
            try:
                body = self.client.refresh(session.refresh_token)
            except SSOLoginError as exc:
                raise AuthError(f"session refresh refused ({exc.code})") from exc
            except SSOUnavailable:
                if session.access_expires_within(0):
                    raise
                # SSO briefly down but the access token is still valid: use it, retry next request
                logger.warning("SSO unreachable for a session refresh; "
                               "using the current access token")
            else:
                try:
                    session, principal = self._session_from_tokens(body, session.username)
                except PermissionDenied:
                    # permission revoked since login: drop the freshly rotated refresh token too
                    self._discard(str(body.get("refresh_token") or ""))
                    raise
                return session, replace(principal, via="session"), True
        principal = check_permission(self.verifier.verify(session.access_token),
                                     self.required_permission)
        return session, replace(principal, via="session"), False

    def logout(self, session: Session | None) -> None:
        if session is not None:
            self._discard(session.refresh_token)

    # ------------------------------------------------------------------ cookies
    def dump_session(self, session: Session) -> str:
        return self.codec.dumps(session)

    def load_session(self, value: str | None) -> Session | None:
        return self.codec.loads(value)
