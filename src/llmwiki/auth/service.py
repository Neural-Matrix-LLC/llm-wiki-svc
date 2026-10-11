"""One object per Settings that the HTTP layer uses for every auth decision (design B2-B4, v1.1)."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import replace

import httpx

from llmwiki.auth.client import SSOClient
from llmwiki.auth.principal import (
    STATIC_PRINCIPAL,
    AuthError,
    PermissionDenied,
    Principal,
    SSOLoginError,
    SSOMisconfigured,
    SSOUnavailable,
)
from llmwiki.auth.session import (
    COOKIE_NAME,
    CSRF_HEADER,
    LOGIN_CSRF_COOKIE,
    SessionRecord,
    SessionStore,
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
    login_csrf_cookie = LOGIN_CSRF_COOKIE
    csrf_header = CSRF_HEADER
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
        self._store: SessionStore | None = None

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
        return SSOClient(self.cfg.sso_base_url, self.http, self.cfg.sso_client_id,
                         self.cfg.sso_client_secret.get_secret_value())

    @property
    def verifier(self) -> SSOVerifier:
        if self._verifier is None:
            jwks = JWKSCache(self.cfg.effective_sso_jwks_url, http_fetch(self.http),
                             clock=self._clock)
            self._verifier = SSOVerifier(jwks, self.cfg.sso_issuer, self.cfg.sso_audience)
        return self._verifier

    @property
    def store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore(self.cfg.effective_session_db_path,
                                       int(self.cfg.session_max_age_hours * 3600),
                                       idle_s=int(self.cfg.session_idle_hours * 3600))
        return self._store

    def init_sessions(self) -> int:
        """Create the session table if needed and prune expired rows (at start). Returns pruned."""
        self.store.init()
        return self.store.prune()

    # ------------------------------------------------------------------ bearer tokens
    def static_matches(self, supplied: str) -> bool:
        return secrets.compare_digest(supplied, self.cfg.ingest_api_token.get_secret_value())

    def authenticate_bearer(self, token: str) -> Principal:
        """v1.1 item 6: a JWT goes to SSO verification (sso/both); in ``both`` a failed
        verification falls back to the constant-time static compare. PermissionDenied (a valid SSO
        token without the permission) never falls back."""
        if looks_like_jwt(token) and self.sso_enabled:
            try:
                principal = self.verifier.verify(token)
            except AuthError:
                if self.static_enabled and self.static_matches(token):
                    return STATIC_PRINCIPAL
                raise
            return check_permission(principal, self.required_permission)
        if self.static_enabled and self.static_matches(token):
            return STATIC_PRINCIPAL
        raise AuthError("invalid bearer token")

    # ------------------------------------------------------------------ browser session
    def _verified(self, body: dict) -> tuple[str, str, int, Principal]:
        access = str(body["access_token"])
        refresh = str(body.get("refresh_token") or "")
        if not refresh:
            raise SSOUnavailable("SSO answered without a refresh token")
        claims = self.verifier.claims(access)
        principal = check_permission(principal_from_claims(claims), self.required_permission)
        return access, refresh, int(claims["exp"]), principal

    def login(self, username: str, password: str,
              client_ip: str | None = None) -> tuple[str, SessionRecord, Principal]:
        """SSO password login -> ``(session_id, record, principal)``.

        PermissionDenied: no session is created and the refresh token is discarded at the SSO.
        """
        body = self.client.login(username, password, client_ip)
        try:
            access, refresh, exp, principal = self._verified(body)
        except PermissionDenied:
            self._discard(str(body.get("refresh_token") or ""))
            raise
        session_id, rec = self.store.create(username=principal.username or username,
                                            sub=principal.sub, access=access, refresh=refresh,
                                            access_exp=exp)
        return session_id, rec, replace(principal, via="session")

    def _discard(self, refresh_token: str) -> None:
        if not refresh_token:
            return
        try:
            self.client.logout(refresh_token)
        except (SSOLoginError, SSOUnavailable) as exc:
            log = logger.error if isinstance(exc, SSOMisconfigured) else logger.warning
            log("SSO logout failed (%s); the refresh token expires on its own",
                type(exc).__name__)

    def get_session(self, session_id: str | None) -> SessionRecord | None:
        return self.store.get(session_id)

    def session_principal(self, rec: SessionRecord,
                          client_ip: str | None = None) -> tuple[SessionRecord, Principal]:
        """Principal for a session; rotates its tokens first when the access token is due.

        * refresh refused by the SSO -> row deleted, AuthError;
        * refreshed tokens lack the permission -> row deleted, new refresh token discarded,
          PermissionDenied (v1.1 Q-06);
        * SSO unreachable / 5xx -> row kept; the current access token is used while still valid,
          else SSOUnavailable (-> 503, v1.1 Q-05).
        """
        if rec.access_expires_within(REFRESH_MARGIN_S):
            denied: list[PermissionDenied] = []

            def do_refresh(current: SessionRecord) -> tuple[str, str, int]:
                body = self.client.refresh(current.refresh, client_ip)
                try:
                    access, refresh, exp, _ = self._verified(body)
                except PermissionDenied as exc:
                    self._discard(str(body.get("refresh_token") or ""))
                    denied.append(exc)
                    raise
                return access, refresh, exp

            try:
                fresh, _ = self.store.refresh_locked(rec.id_hash, rec.refresh,
                                                     REFRESH_MARGIN_S, do_refresh)
            except SSOLoginError as exc:
                self.store.delete(rec.id_hash)
                raise AuthError(f"session refresh refused ({exc.code})") from exc
            except PermissionDenied:
                self.store.delete(rec.id_hash)
                raise
            except SSOUnavailable as exc:
                if isinstance(exc, SSOMisconfigured):
                    logger.error("session refresh: %s", exc)
                if rec.access_expires_within(0):
                    raise
                logger.warning("SSO unreachable for a session refresh; "
                               "using the current access token")
                fresh = rec
            except AuthError:
                self.store.delete(rec.id_hash)  # the SSO handed out a token we cannot verify
                raise
            if fresh is None:
                raise AuthError("session ended")
            rec = fresh
        try:
            principal = check_permission(self.verifier.verify(rec.access), self.required_permission)
        except PermissionDenied:
            self.store.delete(rec.id_hash)
            raise
        return rec, replace(principal, via="session")

    def logout(self, rec: SessionRecord | None) -> None:
        if rec is not None:
            self.store.delete(rec.id_hash)
            self._discard(rec.refresh)

    def retire(self, old: SessionRecord | None, new: SessionRecord) -> None:
        """Session rotation after a successful login: delete the session the browser had before
        and discard its refresh token at the SSO, best effort. Never raises - nothing here may
        block the login that already succeeded."""
        if old is None or old.id_hash == new.id_hash:
            return
        try:
            self.store.delete(old.id_hash)
        except Exception:  # pragma: no cover - SQLite failure; the row expires on its own
            logger.exception("could not delete the previous session of %s", old.username)
        try:
            self._discard(old.refresh)  # logs SSOLoginError / SSOUnavailable itself
        except Exception as exc:
            logger.warning("SSO logout of the previous session failed (%s)", type(exc).__name__)
        logger.info("session rotated on login: previous session of %s deleted", old.username)

    def session_max_age(self, rec: SessionRecord) -> int:
        return self.store.remaining_s(rec)
